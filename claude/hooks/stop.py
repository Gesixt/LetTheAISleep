from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import paths, stm, watermark
from lts.config import load_config
from lts.hooklog import log_error, log_note

# Cap per captured message so the STM buffer stays a working set, not a transcript clone.
_MAX_CHARS = 1000


def _capture_mark(cfg) -> dict:
    """Where the last capture stopped, migrating the pre-watermark offset file if present."""
    mark = watermark.read_mark(paths.capture_mark_file(cfg))
    if mark:
        return mark
    legacy = paths.legacy_capture_offset_file(cfg)
    if legacy.exists():
        try:
            count = int(legacy.read_text(encoding="utf-8").strip() or "0")
        except (OSError, ValueError):
            count = 0
        legacy.unlink(missing_ok=True)
        return {"count": count}
    return {}


def capture(event: dict, *, root: Path | None = None) -> int:
    """Automatically append any new user/assistant exchanges to the STM buffer.

    Deterministic (hook-driven): no model decision. A watermark records the last exchange
    already captured, so turns are never duplicated or missed — and, unlike a bare count,
    it stays correct when the next session opens a different, shorter transcript.

    When `/sleep` has just armed the sleep flag, this turn *is* the sleep: its narration is
    discarded rather than written into the buffer it just emptied, and the sleep mark is
    advanced so the `/compact` that follows has nothing left to snapshot.

    Returns the number of newly captured exchanges (0 if there is no project here —
    we never create a sidecar outside a configured root).
    """
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        log_note("stop.py", f"no lts project at or above {cfg.project_root}; captured nothing")
        return 0
    paths.ensure_sidecar(cfg)
    transcript_path = Path(event.get("transcript_path", ""))

    if watermark.is_armed(paths.sleep_flag_file(cfg)):
        here = watermark.mark_of(transcript_path)
        watermark.write_mark(paths.capture_mark_file(cfg), here)
        watermark.write_mark(paths.sleep_mark_file(cfg), here)
        watermark.disarm(paths.sleep_flag_file(cfg))
        log_note("stop.py", "sleep just finished; dropped this turn instead of refilling STM")
        return 0

    new = watermark.entries_after(transcript_path, _capture_mark(cfg))
    buf = paths.stm_file(cfg)
    for ex in new:
        text = " ".join(ex["text"].split())  # flatten to one line per exchange
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS].rstrip() + " …[truncated]"
        stm.append(buf, f"[{ex['role']}] {text}")
    here = watermark.mark_of(transcript_path)
    if here:  # an unreadable/empty transcript must not reset the mark and replay everything
        watermark.write_mark(paths.capture_mark_file(cfg), here)
    return len(new)


def main() -> None:
    try:
        event = json.load(sys.stdin)
        capture(event)
    except Exception:
        log_error("stop.py")
    print("{}")


if __name__ == "__main__":
    main()
