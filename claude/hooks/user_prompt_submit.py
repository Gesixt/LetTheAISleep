from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import memorymap, transcript
from lts.config import load_config
from lts.hooklog import log_error

# The nudges deliberately quote no percentage of their own. The old wording — "(~60%+)",
# "(~80%+)" — was the only figure the model ever saw, and stating a band invited it to report a
# specific number it had not measured: on 2026-09-25 it announced "Контекст ~75%" and recommended
# a /compact while the turn's own usage record said 276,013 tokens, i.e. 27.6%. The measured line
# below carries the number; these two only say what to do about it.
_WARN = (
    "Context is filling up. Consider running `/sleep` soon, or at the next "
    "natural break, so nothing is lost."
)
_FORCE = (
    "Context is nearly full. You should run `/sleep` now to consolidate this "
    "session into long-term notes before context is compacted."
)


def _context_line(tokens: int, window: int) -> str:
    """The measured occupancy of the context window, stated on every turn.

    Below the warn threshold this hook used to say nothing, and no skill passes `--transcript`
    to `lts status`, so the one real number in the system was never quotable. With nothing to
    cite, the model produced a figure of its own. "window" is in the label on purpose: the STM
    buffer is a different meter, and the two were being conflated.
    """
    if not tokens or window <= 0:
        return ""
    return f"Context window: {tokens:,}/{window:,} tokens ({round(tokens / window * 100)}%)"


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
    blocks = (memorymap.render(cfg), _context_line(tokens, cfg.context_window), pressure)
    return "\n\n".join(b for b in blocks if b)


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
