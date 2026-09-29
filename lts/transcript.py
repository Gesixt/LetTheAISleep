from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_WINDOW = 200_000

# Claude Code writes its own bookkeeping into the transcript as `user` records. A single
# `/compact` produces four of them (the bare command, an isMeta caveat, a <command-name>
# block and the <local-command-stdout>), plus an isCompactSummary record afterwards. None of
# it is material: left in, it fills the STM buffer and makes PreCompact snapshot pure
# scaffolding right after a sleep — an "unfinished sleep" that was in fact finished.
_SCAFFOLD_FLAGS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")
_SCAFFOLD_PREFIXES = ("<command-name>", "<command-message>", "<local-command-stdout>",
                      "<local-command-caveat>")
# A slash command with no arguments carries no content of its own; one with arguments does.
_BARE_SLASH_COMMAND = re.compile(r"^/[A-Za-z][\w:-]*$")


def is_scaffolding(record: dict, role: str, text: str) -> bool:
    """True when this transcript record is Claude Code's plumbing rather than the dialogue."""
    if any(record.get(flag) for flag in _SCAFFOLD_FLAGS):
        return True
    if text.startswith(_SCAFFOLD_PREFIXES):
        return True
    return role == "user" and bool(_BARE_SLASH_COMMAND.match(text))



def extract_text(content) -> str:
    """Human-readable text from a transcript message's content (str, or list of blocks).

    For block lists, only `text` blocks are kept — thinking and tool_use/tool_result are dropped.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


def read_exchanges(transcript_path: Path) -> list[dict]:
    """Ordered user/assistant exchanges with non-empty text.

    Each entry is `{"role", "text", "uuid", "timestamp"}`. The uuid/timestamp identify the
    exchange inside the transcript, which is what lets the sleep watermark say "consolidation
    reached here" in a file that keeps growing across `--resume`.
    """
    if not transcript_path.exists():
        return []
    out: list[dict] = []
    for line in transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        msg = d.get("message") or {}
        role = msg.get("role") or d.get("type")
        text = extract_text(msg.get("content"))
        if text and not is_scaffolding(d, role, text):
            out.append({
                "role": role,
                "text": text,
                "uuid": d.get("uuid"),
                "timestamp": d.get("timestamp"),
            })
    return out


def estimate_tokens(transcript_path: Path) -> int:
    if not transcript_path.exists():
        return 0
    return len(transcript_path.read_text(encoding="utf-8", errors="ignore")) // 4


# How a token figure was arrived at. The distinction is the point: `estimate_tokens` is
# `len(file) // 4` over a file that is append-only across `--resume`, so on a long-running project
# it describes the project's whole history and not the live window. Divided by a window it was
# never measured against, that is the 22,707% reading this branch keeps quoting — and a caller
# that cannot tell the two apart will present one as the other, which is how an empty file used to
# buy `✓ pressure: 0/1,000,000 tokens (0%)`.
USAGE = "usage"          # summed from a real assistant `usage` record
ESTIMATE = "estimate"    # derived from the file's size, because no usage record was found
NO_FILE = "no_file"      # nothing was read at all


def measure_context(transcript_path: Path) -> tuple[int, str]:
    """`(tokens, provenance)` — the number, and what kind of number it is.

    One parse: `context_tokens` is this function's first element, so a caller that wants the
    provenance pays nothing extra for it. `USAGE` is a measurement of the live window; `ESTIMATE`
    is a size-derived guess about a file that may span months; `NO_FILE` is neither.
    """
    if not transcript_path.exists():
        return 0, NO_FILE
    last = 0
    # Tracked apart from `last`, because a real record summing to zero is a measurement of an
    # empty window, not the absence of one, and `last or estimate` cannot tell those apart.
    measured = False
    for line in transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("type") != "assistant":
            continue
        usage = (d.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            measured = True
            last = (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            )
    if measured:
        return last, USAGE
    return estimate_tokens(transcript_path), ESTIMATE


def context_tokens(transcript_path: Path) -> int:
    """Current context size in tokens, however it was arrived at.

    The number alone. `measure_context` returns the same number with its provenance, and anything
    that will present the figure to a human or to the model asks for both halves — `lts status`,
    `lts pressure`, `_check_pressure` and `UserPromptSubmit` all do, because an estimate shown as
    a reading is the defect this pair exists to separate.
    """
    return measure_context(transcript_path)[0]


def pressure_level(tokens: int, window: int, warn: float, force: float) -> str:
    """"none" | "warn" | "force", or "unknown" when the measurement cannot be true.

    More tokens than the window holds is not a full window: it is a reading nothing stands behind,
    and every verdict about it would be invented. `none` would understate it and `force` would
    overstate it with equal confidence, so there is no level to report. `UserPromptSubmit` states
    the two numbers and emits no nudge in that case — one measurement must not produce a sentence
    calling it unknown and a demand derived from it in the same block.
    """
    if window <= 0:
        return "none"
    if tokens > window:
        return "unknown"
    ratio = tokens / window
    if ratio >= force:
        return "force"
    if ratio >= warn:
        return "warn"
    return "none"
