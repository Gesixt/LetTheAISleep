from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import clients, paths, pending, watermark
from lts.config import load_config
from lts.hooklog import log_error, log_note


def run(event: dict, *, root: Path | None = None, now: datetime | None = None,
        client: str = clients.DEFAULT) -> dict:
    """Snapshot the un-consolidated tail of the transcript, so a compact loses nothing.

    Only the tail: a Claude Code transcript is append-only across `--resume` and can span
    months, so dumping the whole file made every snapshot mostly a copy of chapters that
    were already in long-term notes — and made SessionStart demand a sleep that was not
    owed. When nothing follows the sleep mark or the snapshot mark, whichever is later, no
    snapshot is written at all.
    """
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        log_note("pre_compact.py", f"no lts project at or above {cfg.project_root}; no snapshot")
        return {}
    # Resolved here and not in `main()`: an unknown client must raise where `main()`'s `except`
    # can turn it into a logged line and a well-formed `{}`, never into an exception the harness
    # sees coming out of a hook. See `clients.resolve` for why it refuses rather than falls back.
    adapter = clients.resolve(client)
    paths.ensure_sidecar(cfg, client)
    transcript_path = Path(event.get("transcript_path") or "")
    mark_file = paths.sleep_mark_file(cfg)

    flag = paths.sleep_flag_file(cfg, client)
    if watermark.is_armed(flag):
        boundary = watermark.armed_mark(flag, now)
        if boundary:
            # Compact may precede Stop. Both use the SAME fixed boundary; new work after
            # it still needs a snapshot. Leave the flag for Stop to advance capture too.
            for target in (mark_file, paths.consolidation_mark_file(cfg, client)):
                watermark.write_mark(target, watermark.later_of(watermark.read_mark(target), boundary))
        else:
            stale = watermark.armed_at(flag)
            watermark.disarm(flag)
            log_note("pre_compact.py", f"sleep flag armed at {stale or 'an unrecorded time'} is past "
                                       f"{watermark.SLEEP_FLAG_MAX_AGE}; snapshotting as usual")

    snapshot_file = paths.snapshot_mark_file(cfg, client)
    # The tail that neither a sleep nor an earlier snapshot already holds. Two marks rather than
    # one because they are two different claims: the sleep mark says notes exist, the snapshot mark
    # says a pending snapshot does. Writing the second into the first is what let an ordinary
    # compaction advance the consolidation frontier with nothing consolidated.
    frontier = watermark.later_of(
        watermark.read_mark(snapshot_file), watermark.read_mark(
            paths.consolidation_mark_file(cfg, client)
            if client == "codex" or len(cfg.enabled_clients) > 1 else mark_file)
    )
    # Parsed once and kept: `new` is the tail to snapshot, `exchanges` is what the new mark is
    # measured against. The parse is the running client's own, and this line is the only place in
    # this hook the adapter touches, and the rest of it is shaped so that stays true.
    exchanges = adapter.read_exchanges(transcript_path)
    new = watermark.exchanges_after(exchanges, frontier)
    if not new:
        log_note("pre_compact.py", "no material since the last sleep or snapshot; no snapshot")
        return {}

    pending.dump_snapshot(
        paths.pending_dir(cfg),
        event.get("session_id", "default"),
        watermark.render_exchanges(new),
    )
    # The tail is now captured — in a snapshot, not in notes. This mark names the last exchange
    # actually written out, never the instant: a snapshot can only hold what was flushed, and an
    # instant would step over a message that arrived in the file late.
    watermark.write_mark(snapshot_file, watermark.mark_of_exchanges(exchanges))
    return {}
