from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import transcript
from lts.config import load_config
from lts.hooklog import log_error

_WARN = (
    "Context is filling up (~60%+). Consider running `/sleep` soon, or at the next "
    "natural break, so nothing is lost."
)
_FORCE = (
    "Context is nearly full (~80%+). You should run `/sleep` now to consolidate this "
    "session into long-term notes before context is compacted."
)


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root or event.get("cwd"))
    tokens = transcript.context_tokens(Path(event.get("transcript_path", "")))
    level = transcript.pressure_level(
        tokens, cfg.context_window, cfg.pressure_warn, cfg.pressure_force
    )
    if level == "force":
        return _FORCE
    if level == "warn":
        return _WARN
    return ""


def run(event: dict, *, root: Path | None = None) -> dict:
    text = build_context(event, root=root)
    if not text:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
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
