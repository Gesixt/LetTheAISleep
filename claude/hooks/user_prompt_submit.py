from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import memorymap, transcript
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
    """The memory map, then the sleep-pressure nudge — whichever of the two has anything to say.

    The map goes first because it is context for answering the prompt that follows; the nudge is
    about what to do next, so it sits closest to the prompt. This hook is the only one that runs
    on every turn, which is why it carries the map: between two compactions nothing else tells
    the model that a vault of notes exists.
    """
    cfg = load_config(root or event.get("cwd"))
    tokens = transcript.context_tokens(Path(event.get("transcript_path", "")))
    level = transcript.pressure_level(
        tokens, cfg.context_window, cfg.pressure_warn, cfg.pressure_force
    )
    pressure = _FORCE if level == "force" else (_WARN if level == "warn" else "")
    return "\n\n".join(b for b in (memorymap.render(cfg), pressure) if b)


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
