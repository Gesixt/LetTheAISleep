from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import anchor, paths, pending, stm
from lts.config import load_config

_FORCE_MSG = (
    "## Unfinished sleep detected\n"
    "There is un-consolidated memory from a previous session (STM buffer or a "
    "PreCompact snapshot). Before doing anything else, run the `/sleep` skill to "
    "finish sleeping this material into long-term notes, then continue."
)


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root)
    paths.ensure_sidecar(cfg)
    session_id = event.get("session_id", "default")
    pending_present = pending.has_pending(paths.pending_dir(cfg))
    stm_present = not stm.is_empty(paths.stm_file(cfg, session_id))
    if pending_present or stm_present:
        return _FORCE_MSG
    return anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg)))


def run(event: dict, *, root: Path | None = None) -> dict:
    text = build_context(event, root=root)
    if not text:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }


def main() -> None:
    event = json.load(sys.stdin)
    print(json.dumps(run(event)))


if __name__ == "__main__":
    main()
