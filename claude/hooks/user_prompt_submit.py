from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import memorymap, status, transcript
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


def _context_line(tokens: int, window: int, source: str = transcript.USAGE) -> str:
    """The measured occupancy of the context window, or an honest refusal when it cannot be true.

    Below the warn threshold this hook used to say nothing, and no skill passes `--transcript`
    to `lts status`, so the one real number in the system was never quotable. With nothing to
    cite, the model produced a figure of its own. "window" is in the label on purpose: the STM
    buffer is a different meter, and the two were being conflated.

    A percentage above 100 is never real, so it is not printed. `lts pressure` produced one on a
    real project: a `len(file) // 4` estimate over an append-only transcript, divided by a
    hardcoded 200,000 rather than the configured window — 45,413,420 tokens, 22,707% of it, while
    the live session held 301,343 of its 1,000,000. (`healthchecks._check_pressure` carries the
    full account, including the "9900%" this branch quoted, which follows from no window.) That
    the cause took three attempts to state correctly is the argument for asserting on the output
    instead: whatever produced the pair, the sentence it printed could not be true. Here the same
    state prints both numbers and no percentage, which costs nothing — both figures are already
    in hand, and `pressure_level` returns "unknown" so no nudge is derived from them either.

    `source` is the other half of the same rule. Without a `usage` record the figure is
    `len(file) // 4`, and this is the one surface the model reads and quotes, so an unmarked
    estimate here is the mistake `lts status` and `lts doctor` were just corrected for. Real
    sessions carry usage counters, so the marked line is rare — which is a reason to state it,
    not a reason to leave it looking like a measurement.
    """
    if not tokens or window <= 0:
        return ""
    estimated = "" if source == transcript.USAGE else f" ({status.ESTIMATED})"
    if tokens > window:
        return (
            f"Context window: {tokens:,} tokens against a {window:,}-token window{estimated} — "
            "this is impossible, so the measurement or the configured window is wrong. "
            "Treat the figure as unknown."
        )
    return (f"Context window: {tokens:,}/{window:,} tokens "
            f"({round(tokens / window * 100)}%){estimated}")


def build_context(event: dict, *, root: Path | None = None) -> str:
    """The memory map, then the sleep-pressure nudge — whichever of the two has anything to say.

    The map goes first because it is context for answering the prompt that follows; the nudge is
    about what to do next, so it sits closest to the prompt. This hook is the only one that runs
    on every turn, which is why it carries the map: between two compactions nothing else tells
    the model that a vault of notes exists.
    """
    cfg = load_config(root or event.get("cwd"))
    tokens, source = transcript.measure_context(Path(event.get("transcript_path", "")))
    level = transcript.pressure_level(
        tokens, cfg.context_window, cfg.pressure_warn, cfg.pressure_force
    )
    # "unknown" (tokens > window) falls through to "": a nudge derived from a figure the line
    # above disowns would be two verdicts on one measurement, the second contradicting the first.
    pressure = _FORCE if level == "force" else (_WARN if level == "warn" else "")
    blocks = (memorymap.render(cfg),
              _context_line(tokens, cfg.context_window, source), pressure)
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
