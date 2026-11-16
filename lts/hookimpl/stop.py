from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import clients, paths, stm, watermark
from lts.config import load_config
from lts.hooklog import log_error, log_note

# Cap per captured message so the STM buffer stays a working set, not a transcript clone.
_MAX_CHARS = 1000


def _capture_mark(cfg, client: str) -> dict:
    """Where the last capture stopped, migrating the pre-watermark offset file if present."""
    mark = watermark.read_mark(paths.capture_mark_file(cfg, client))
    if mark:
        return mark
    legacy = paths.legacy_capture_offset_file(cfg)
    if client == clients.DEFAULT and legacy.exists():
        try:
            count = int(legacy.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            count = 0
        legacy.unlink(missing_ok=True)
        return {"count": count}
    return {}


def capture(event: dict, *, root: Path | None = None, now: datetime | None = None,
            client: str = clients.DEFAULT) -> int:
    """Automatically append any new user/assistant exchanges to the STM buffer.

    Deterministic (hook-driven): no model decision. A watermark records the last exchange
    already captured, so turns are never duplicated or missed — and, unlike a bare count,
    it stays correct when the next session opens a different, shorter transcript.

    A sleep flag covers only material through its arming instant. Work after that instant
    is captured normally, even if the model continued working in the same turn as the sleep.

    Returns the number of newly captured exchanges (0 if there is no project here —
    we never create a sidecar outside a configured root).
    """
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        log_note("stop.py", f"no lts project at or above {cfg.project_root}; captured nothing")
        return 0
    # Resolved here and not in `main()`: an unknown client must raise where `main()`'s `except`
    # can turn it into a logged line and a well-formed `{}`, never into an exception the harness
    # sees coming out of a hook. Strict rather than falling back to the default, because the
    # wrong parser does not fail — it captures plausible nonsense; see `clients.resolve`.
    adapter = clients.resolve(client)
    paths.ensure_sidecar(cfg, client)
    transcript_path = Path(event.get("transcript_path") or "")

    frontier = _capture_mark(cfg, client)
    flag = paths.sleep_flag_file(cfg, client)
    boundary = watermark.armed_mark(flag, now)
    if watermark.is_armed(flag):
        if boundary:
            frontier = watermark.later_of(frontier, boundary)
            for mark_file in (paths.sleep_mark_file(cfg), paths.consolidation_mark_file(cfg, client)):
                watermark.write_mark(mark_file, watermark.later_of(watermark.read_mark(mark_file), boundary))
            # Keep the boundary even if the transcript cannot be read on this attempt.
            watermark.write_mark(paths.capture_mark_file(cfg, client), frontier)
            watermark.disarm(flag)
            log_note("stop.py", "sleep boundary recorded; capturing material after consolidation")
        else:
            # Unknown/expired flags retain the conservative normal capture path.
            stale = watermark.armed_at(flag)
            watermark.disarm(flag)
            log_note("stop.py", f"sleep flag armed at {stale or 'an unrecorded time'} is past "
                                f"{watermark.SLEEP_FLAG_MAX_AGE}; ignored it and captured normally")

    # Parsed once, by the running client's own reader, and kept: `new` is the tail to capture and
    # `exchanges` is what the new mark is measured against. Both from the same parse, so the mark
    # can only ever name an exchange that reached the buffer — a second read of the file could see
    # a message Claude Code appended in between and mark it captured when it was not.
    exchanges = adapter.read_exchanges(transcript_path)
    new = watermark.exchanges_after(exchanges, frontier)
    buf = paths.stm_file(cfg)
    for ex in new:
        text = " ".join(ex["text"].split())  # flatten to one line per exchange
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS].rstrip() + " …[truncated]"
        stm.append(buf, f"[{ex['role']}] {text}")
    here = watermark.mark_of_exchanges(exchanges)
    if boundary:
        here = watermark.later_of(frontier, here)
    # No new exchanges means no new position. In particular, a post-sleep retry must
    # not replace the fixed boundary with an older message merely because it is last on disk.
    if here and (new or boundary):
        watermark.write_mark(paths.capture_mark_file(cfg, client), here)
    return len(new)
