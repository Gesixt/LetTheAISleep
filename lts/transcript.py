from __future__ import annotations

from pathlib import Path

DEFAULT_WINDOW = 200_000


def estimate_tokens(transcript_path: Path) -> int:
    if not transcript_path.exists():
        return 0
    return len(transcript_path.read_text(encoding="utf-8", errors="ignore")) // 4


def pressure_level(tokens: int, window: int, warn: float, force: float) -> str:
    if window <= 0:
        return "none"
    ratio = tokens / window
    if ratio >= force:
        return "force"
    if ratio >= warn:
        return "warn"
    return "none"
