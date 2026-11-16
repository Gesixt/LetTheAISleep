"""Watermarks: how far through a transcript memory has already been taken.

A Claude Code transcript is one long-lived, append-only file. `--resume` keeps writing to it,
so "the transcript" is the whole history of a project, not the current chapter — a session
file can span months. Without a mark, every PreCompact snapshot re-dumps everything that was
already consolidated, and the next SessionStart reports a sleep that is not actually owed.

Three marks use this module. Two name a position, `{"uuid", "timestamp", "count"}`:

* the **capture mark** — the last exchange the Stop hook put into the STM buffer, or the
  consolidation boundary when a sleep has saved everything through that instant;
* the **snapshot mark** — the last exchange a pending snapshot holds, which no note does.

The third, the **sleep mark**, is the instant consolidation into long-term notes reached, and
carries that timestamp alone: hooks use the flag's arming time, never their execution time.

The sleep and snapshot marks were one file until 2026-10-30, and that file therefore carried two
different claims: `PreCompact` wrote "a snapshot holds this", in the positional shape, into the
field `sleep_evidence` reads as "notes exist". Kept apart, the tail nothing holds yet starts at
`later_of` the two.

Resolution is deliberately layered: `uuid` is exact, `timestamp` survives a transcript that
was rewritten under us, and `count` is the last resort for transcripts that carry neither.

A sleep boundary carries a timestamp only (`mark_at`). Any message produced after it remains
owed, including messages flushed to the transcript after the first hook has read it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lts.transcript import read_exchanges

#: How long after `arm` a sleep flag may still be honoured.
#:
#: Measured 2026-10-19 over the six sleeps whose arming and consumption are both on record, in two
#: real projects: `lts stm clear` → the `Stop` that consumed it took **8.3, 9.3, 11.0, 13.6, 14.4 and
#: 16.9 seconds**. The latch is meant to be eaten by the very next `Stop`, so that is the whole
#: intended lifetime.
#:
#: Five minutes is an ~18× margin over the slowest of those six, and the margin is deliberate rather
#: than measured: a sleep whose tail runs long — a big vault reindex, team-mode reminders, a long
#: closing message — would extend the gap, and no such sleep is in the six. The margin is safe to
#: spend because the two errors are not symmetric. **Too generous and the sleep mark moves over
#: material nobody consolidated, silently, with every check green** — that is the 2026-10-16
#: incident, whose gap was hours, four orders of magnitude outside this bound. **Too tight and the
#: sleep's own narration lands in the buffer**, which the next sleep folds in and which `lts status`
#: shows. Noise is recoverable; a moved frontier is not.
SLEEP_FLAG_MAX_AGE = timedelta(minutes=5)


def mark_of_exchanges(exchanges: list[dict]) -> dict:
    """The mark naming the last of `exchanges` ({} when there are none).

    Split out of `mark_of` for a caller that has already parsed the transcript. `count` is the
    length of the **whole** list and never of a tail: `exchanges_after` uses it as its last-resort
    fallback, so a count measured against a slice would resolve silently to the wrong position.
    """
    if not exchanges:
        return {}
    last = exchanges[-1]
    return {
        "uuid": last.get("uuid"),
        "timestamp": last.get("timestamp"),
        "count": len(exchanges),
    }


def mark_of(transcript_path: Path) -> dict:
    """The mark naming the last user/assistant exchange in `transcript_path` ({} if there is none)."""
    return mark_of_exchanges(read_exchanges(Path(transcript_path)))


def mark_at(when: datetime | None = None) -> dict:
    """A mark that is an instant, not a position — everything stamped at or before it is done.

    Only use a timestamp for material already consolidated. Ordinary capture and snapshots
    use `mark_of_exchanges`, so a message that reaches the file late remains eligible.
    """
    when = when or datetime.now(timezone.utc)
    stamp = when.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return {"timestamp": stamp.replace("+00:00", "Z")}


def entries_after(transcript_path: Path, mark: dict) -> list[dict]:
    """The exchanges of `transcript_path` that follow `mark` (all of them when it is empty)."""
    return exchanges_after(read_exchanges(Path(transcript_path)), mark)


def exchanges_after(exchanges: list[dict], mark: dict) -> list[dict]:
    """The same resolution, over exchanges already in hand.

    Split out so that a caller which must also ask whether the transcript yielded any exchanges at
    all — `healthchecks._check_capture`, which reports a skip rather than a clean bill when it
    yielded none — can answer both questions from one parse instead of reading the file twice.
    """
    if not mark:
        return exchanges

    uuid = mark.get("uuid")
    if uuid:
        for i, ex in enumerate(exchanges):
            if ex.get("uuid") == uuid:
                return exchanges[i + 1:]

    stamp = mark.get("timestamp")
    if stamp:
        # Claude Code writes one ISO-8601 shape, so lexicographic order is chronological.
        return [ex for ex in exchanges if (ex.get("timestamp") or "") > stamp]

    count = int(mark.get("count") or 0)
    # A count that overruns means this is a different (or truncated) transcript, not one we
    # have already read to the end — start from the beginning rather than swallowing it whole.
    return exchanges[count:] if count <= len(exchanges) else exchanges


def later_of(a: dict, b: dict) -> dict:
    """Whichever of two marks stands further along; an empty mark is the beginning of time.

    `pre_compact.py` needs this because the tail it owes a snapshot starts at whichever is later of
    *the last sleep* and *the last snapshot*: a sleep means nothing behind it needs snapshotting,
    and so does a snapshot. The comparison did not exist while one file carried both claims.

    Comparison is lexicographic on the stored string, which is chronological only because every
    producer writes one shape — UTC, milliseconds, `Z`, as `mark_at` writes it. Every client
    adapter is required to normalise to that shape (`lts.clients`); a second shape would reorder
    marks silently rather than raising.

    A mark with no readable timestamp loses to one that has it. "No position" is the beginning of
    time, not the end of it — the opposite choice would let an unreadable file declare everything
    behind it already handled, which is the direction that forgets.
    """
    ta = (a or {}).get("timestamp")
    tb = (b or {}).get("timestamp")
    if not isinstance(ta, str):
        return b or {}
    if not isinstance(tb, str):
        return a or {}
    return a if ta >= tb else b


def read_mark(path: Path) -> dict:
    """The mark stored at `path`, or {} when it is absent or unreadable."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_mark(path: Path, mark: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mark, ensure_ascii=False), encoding="utf-8")


def arm(flag_path: Path, at: datetime | None = None) -> None:
    """Record the instant consolidation reached; later work must still be captured.

    The instant is written into the flag because the flag is a **latch on disk with no owner**:
    `lts stm clear` sets it and only the next `Stop` clears it, so nothing bounds how long it
    waits. Consumed 2026-10-16 by a `Stop` hours after it was set, it advanced the sleep mark
    ~20 hours over material that was in no note — the consolidation frontier moved in the
    direction of forgetting, with every health check green. See `armed_at`.
    """
    flag_path = Path(flag_path)
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text(json.dumps(mark_at(at), ensure_ascii=False), encoding="utf-8")


def is_armed(flag_path: Path) -> bool:
    return Path(flag_path).exists()


def armed_at(flag_path: Path) -> str | None:
    """When the flag was armed, or `None` when that cannot be established.

    `None` is not "now" and not "long ago" — it is ignorance, and it has one ordinary cause: every
    flag written before this field existed is an empty file, so each deployed project may hold one.
    The caller decides, and `stop.py`/`pre_compact.py` decide the same way: an age they cannot
    establish is treated as stale. The asymmetry is what settles it — honouring a latch of unknown
    age moves the sleep mark over material nobody consolidated, while ignoring one costs the few
    captured lines of the sleep's own narration, which the next sleep folds in anyway.
    """
    try:
        data = json.loads(Path(flag_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    stamp = data.get("timestamp")
    return stamp if isinstance(stamp, str) and stamp else None


def note_written_since(vault_root: Path, mark: dict) -> bool:
    """Does the vault hold a note modified after `mark` — i.e. did a sleep actually write anything?

    The one fact that distinguishes "a sleep finished here" from "the buffer was emptied here".
    `lts stm clear` arms the sleep latch, and the latch is a claim that everything behind it is in
    long-term notes; before 2026-10-19 that claim rested on nothing but the command having been
    run. On 2026-10-16 it was run in `nextcloud-development` on a day whose vault gained no file
    at all, and the sleep mark later jumped ~20 hours over material no note held.

    mtime rather than frontmatter: Basic Memory writes the file, so the filesystem's own record is
    the cheapest honest evidence, and it cannot be back-dated by a note's contents. The walk stops
    at the first note newer than the mark — on a vault of 250 notes the full walk measured
    23.84–29.28 ms on fuseblk (2026-10-01), and this runs once per sleep, not per turn.

    `False` for a vault that is absent or empty, and for a mark whose timestamp is unreadable the
    floor is the epoch, so *any* note counts — an unreadable mark is not grounds to refuse a sleep
    that did write something.
    """
    root = Path(vault_root)
    if not root.is_dir():
        return False
    floor = 0.0
    stamp = (mark or {}).get("timestamp") if isinstance(mark, dict) else None
    if isinstance(stamp, str) and stamp:
        try:
            floor = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
        except ValueError:
            floor = 0.0
    for note in root.rglob("*.md"):
        try:
            if note.stat().st_mtime > floor:
                return True
        except OSError:
            continue
    return False


def armed_recently(flag_path: Path, now: datetime | None = None,
                   max_age: timedelta = SLEEP_FLAG_MAX_AGE) -> bool:
    """Whether a flag has a usable consolidation boundary within the age limit."""
    return bool(armed_mark(flag_path, now, max_age))


def armed_mark(flag_path: Path, now: datetime | None = None,
               max_age: timedelta = SLEEP_FLAG_MAX_AGE) -> dict:
    """Read and validate one flag, returning its arming instant in canonical mark form.

    Never substitute the hook's time: the model may have resumed ordinary work after
    clearing STM. No stamp, invalid/future stamps and expired flags return an empty mark.
    Read once so validation and the returned boundary refer to the same flag contents.
    """
    stamp = armed_at(flag_path)
    if stamp is None:
        return {}
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return {}
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return mark_at(when) if timedelta(0) <= now - when <= max_age else {}


def disarm(flag_path: Path) -> None:
    Path(flag_path).unlink(missing_ok=True)


def render_exchanges(exchanges: list[dict]) -> str:
    """A pending snapshot as readable prose.

    Raw JSONL was unreadable in practice — a snapshot of a months-long session ran to tens of
    thousands of lines, so it got skimmed rather than consolidated. Text is kept verbatim
    (unlike the STM buffer, nothing is truncated here); tool calls and thinking are dropped.
    """
    return "\n\n".join(f"[{ex['role']}] {ex['text']}" for ex in exchanges)
