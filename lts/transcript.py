from __future__ import annotations

import json
from pathlib import Path

DEFAULT_WINDOW = 200_000


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
    """Ordered user/assistant exchanges with non-empty text: [{"role", "text"}, ...]."""
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
        if text:
            out.append({"role": role, "text": text})
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
