from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import anchor, digest, paths, pending, stm
from lts.config import load_config
from lts.hooklog import log_error

_FORCE_MSG = (
    "## Unfinished sleep detected\n"
    "There is un-consolidated memory from a previous session (STM buffer or a "
    "PreCompact snapshot). Before doing anything else, run the `/sleep` skill to "
    "finish sleeping this material into long-term notes, then continue."
)

_UNCONFIGURED_MSG = (
    "## Memory is not configured here\n"
    "No `config.toml` with a `[vault]` section was found at or above `{root}`, so nothing "
    "will be captured this session and no `.ai_memory/` will be created. If this project "
    "used to have memory, its `config.toml` was moved or deleted. Run `lts doctor` to see "
    "where memory is expected to live, and tell the user."
)


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        return _UNCONFIGURED_MSG.format(root=cfg.project_root)
    pending_present = pending.has_pending(paths.pending_dir(cfg))
    stm_present = not stm.is_empty(paths.stm_file(cfg))
    if pending_present or stm_present:
        # One demand at a time: finishing the sleep comes before reading anyone else's notes.
        return _FORCE_MSG

    blocks = [
        anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))),
        digest.render(digest.collect(cfg)),
    ]
    return "\n\n".join(b for b in blocks if b)


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
    try:
        event = json.load(sys.stdin)
        result = run(event)
    except Exception:
        log_error(__file__)
        result = {}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
