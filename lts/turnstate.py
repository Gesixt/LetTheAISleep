"""What the per-turn hook knew at the previous prompt.

Kept apart from `lts.journal` deliberately. A journal record means "one `SessionStart`", and the
trend checks' own messages say "in the last 5 **sessions**"; writing a record per turn would
redefine `TREND_WINDOW` from five sessions to five turns without one line of those checks changing.
This file answers a different question — what happened between the last two prompts — so it is a
different file.

Best-effort throughout, like `lts.journal` and `lts.hooklog`: a read returns `{}` rather than
raising, and a write that fails costs the state and nothing else. State that could break the hook
carrying it would be a new way for memory to die silently.

The file is `paths.turn_state_file(cfg)` — one per project, with no session dimension — so it
outlives a session, and "no previous state" means the first prompt ever recorded for the project
rather than the first prompt of each session. The *count* inside it does not outlive a session: it
carries the session it was counted in and `advance` restarts it when that changes, for the reason
given there.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from lts import watermark


def read(path: Path) -> dict:
    """The last state written, or `{}` when there is none, it is unreadable, or it is not a dict."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def advance(previous: dict, *, capture_mark: str | None, at: str | None = None,
            session_id: str | None = None) -> dict:
    """The state this prompt leaves behind.

    `misses` counts consecutive prompts that found the capture mark exactly where the prompt before
    them left it — i.e. prompts between which the `Stop` hook recorded no new exchange. The count
    starts at 0 when there is no previous state at all, because with nothing to compare against
    there is no evidence either way, and a project one prompt away from a failure would be a false
    demand.

    What "unmoved" is evidence *of* is narrower than "the Stop hook is dead", which is why the
    caller needs a tolerance rather than acting on one miss. Read 2026-10-01 in
    `claude/hooks/stop.py` and `lts/watermark.py`, the mark is a transcript *position*
    (`watermark.mark_of`), written only when the transcript yielded an exchange, so it also stays
    put when `Stop` ran correctly and there was nothing to record: an interrupted turn where `Stop`
    never fired, a transcript that could not be read (the `if here:` guard in `stop.py` leaves the
    mark alone rather than replaying the file), and a turn whose records are all scaffolding or
    carry no text (`transcript.is_scaffolding` drops a bare slash command, and a reply of pure tool
    calls has no text block). A sleep is *not* one of those cases: the armed branch of `stop.py`
    writes `watermark.mark_at(now)`, so the mark moves and this count resets on its own.

    An absent mark compared with an absent mark counts as a miss: a project whose `Stop` hook has
    never run has no mark to be unmoved, and that is the same evidence, not an exemption. A mark
    that was present and is now absent counts as movement instead — nothing in this project deletes
    the mark file, so its disappearance says something about the file and nothing about `Stop`, and
    the prompt after it compares absent with absent and starts the count again.

    A `misses` that is not a non-negative `int` is treated as absent rather than as arithmetic: this
    file is best-effort and may have been half-written or hand-edited.

    **The count does not cross a session.** This file is per project — `paths.turn_state_file` has no
    session dimension and nothing in the tree removes it — so without this a session whose last turn
    the user interrupted would leave `misses = 1` on disk and the first prompt of the next session
    would inherit it, reaching `healthchecks._CAPTURE_MISS_TOLERANCE` at once. That demand would be
    premature rather than true: the interrupted turn's exchanges are still in the transcript and the
    next working `Stop` captures them, because `watermark.entries_after` starts from the mark and not
    from "this turn". So a `session_id` that differs from the stored one starts the count at 0.

    `session_id` is "a stable identifier for this session", and the caller chooses it. The per-turn
    hook should read `session_id` from its event payload: that field name is *not* observed in a real
    `UserPromptSubmit` payload, but it is in a real `Stop` one (`{"session_id": ..., "cwd": ...,
    "hook_event_name": "Stop", ...}`, recorded 2026-10-01 from a trace log the user had pasted into
    their terminal) alongside the other common envelope fields, and `claude/hooks/pre_compact.py` has
    read `event.get("session_id", "default")` in production since before this branch. `None` means
    the caller could not tell — `lts doctor` reads this file and never writes it — and is not a new
    session: the count continues, because a reset on every unknown would disable the only
    mid-session detector of a dead `Stop` hook. A caller with no id in hand but a per-session path in
    hand (`transcript_path` is in that same observed payload) can pass that instead; this function
    only ever compares the value with the stored one.
    """
    previous = previous if isinstance(previous, dict) else {}
    at = at or watermark.mark_at()["timestamp"]
    had_state = "capture_mark" in previous
    misses = previous.get("misses", 0)
    if not isinstance(misses, int) or isinstance(misses, bool) or misses < 0:
        misses = 0
    moved = capture_mark != previous.get("capture_mark")
    # A state file written before this field existed carries no id, so the first prompt after the
    # upgrade reads as a changed session and costs one miss of evidence. That is the conservative
    # direction; the alternative is a demand raised against a count whose session nobody recorded.
    new_session = session_id is not None and session_id != previous.get("session_id")
    shown = previous.get("shown")
    return {
        "at": at,
        "prev_at": previous.get("at"),
        "capture_mark": capture_mark,
        "session_id": session_id,
        "misses": 0 if (moved or not had_state or new_session) else misses + 1,
        "shown": shown if isinstance(shown, dict) else {},
    }


def write(path: Path, state: dict) -> None:
    """Replace the state file, atomically. Never raises.

    A temp file plus `os.replace` rather than a direct write, for the reason `journal._trim` gives:
    a crash inside a whole-file rewrite leaves a truncated file behind, and the next read would then
    start a fresh count — losing exactly the evidence this file exists to carry.
    """
    tmp = None
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        except Exception:
            pass
