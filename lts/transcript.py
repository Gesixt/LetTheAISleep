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


def context_tokens(transcript_path: Path) -> int:
    """Real current context size from the last assistant message's `usage` counters.

    Sums input + cache-read + cache-creation tokens (what actually occupies the window).
    Falls back to the rough char estimate only if no usage data is present.
    """
    if not transcript_path.exists():
        return 0
    last = 0
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
            last = (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            )
    return last if last else estimate_tokens(transcript_path)


def pressure_level(tokens: int, window: int, warn: float, force: float) -> str:
    if window <= 0:
        return "none"
    ratio = tokens / window
    if ratio >= force:
        return "force"
    if ratio >= warn:
        return "warn"
    return "none"
