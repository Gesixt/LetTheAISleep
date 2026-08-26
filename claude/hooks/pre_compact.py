from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import paths, pending, watermark
from lts.config import load_config
from lts.hooklog import log_error, log_note


def run(event: dict, *, root: Path | None = None, now: datetime | None = None) -> dict:
    """Snapshot the un-consolidated tail of the transcript, so a compact loses nothing.

    Only the tail: a Claude Code transcript is append-only across `--resume` and can span
    months, so dumping the whole file made every snapshot mostly a copy of chapters that
    were already in long-term notes — and made SessionStart demand a sleep that was not
    owed. When nothing follows the sleep mark, no snapshot is written at all.
    """
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        log_note("pre_compact.py", f"no lts project at or above {cfg.project_root}; no snapshot")
        return {}
    paths.ensure_sidecar(cfg)
    transcript_path = Path(event.get("transcript_path", ""))
    mark_file = paths.sleep_mark_file(cfg)

    if watermark.is_armed(paths.sleep_flag_file(cfg)):
        # A sleep is finishing in this very turn (auto-compact can beat the Stop hook to it):
        # everything up to here is already consolidated. The flag stays armed — only Stop
        # knows when the turn ends, and disarming here left the rest of the sleep's own
        # narration to be captured into the buffer the sleep had just emptied.
        watermark.write_mark(mark_file, watermark.mark_at(now))
        log_note("pre_compact.py", "sleep in progress; nothing to snapshot")
        return {}

    new = watermark.entries_after(transcript_path, watermark.read_mark(mark_file))
    if not new:
        log_note("pre_compact.py", "no material since the last sleep; no snapshot")
        return {}

    pending.dump_snapshot(
        paths.pending_dir(cfg),
        event.get("session_id", "default"),
        watermark.render_exchanges(new),
    )
    # The tail is now captured (in a snapshot rather than in notes), so a second compact
    # before the next sleep snapshots only what came after it. This mark names the last
    # exchange actually written out, never the instant: a snapshot can only hold what was
    # flushed, and an instant would step over a message that arrived in the file late.
    watermark.write_mark(mark_file, watermark.mark_of(transcript_path))
    return {}


def main() -> None:
    try:
        event = json.load(sys.stdin)
        result = run(event)
    except Exception:
        log_error(__file__)
        result = {}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
