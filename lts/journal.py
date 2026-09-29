"""A bounded append-only JSONL log, used by `lts.health` to see change over time.

Some memory failures are invisible in a snapshot. The two-month bug where `SessionStart`
returned the sleep demand *instead of* the anchor left a filesystem that looked correct at every
instant — the anchor file existed and had content. What was wrong was a pattern across runs, and
the only way to see it is for the hook to record what it actually emitted, run after run.

This module deliberately knows nothing about health checks: it stores dicts and hands them back.
That is what lets the trend checks be tested against a literal list of records with no file at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from lts import watermark

# A record is 200-400 bytes, so _KEEP records sit just under _MAX_BYTES. 200 runs is 200
# compactions, which in a months-long session is months of history — enough for a trend, and
# bounded forever without a rotation policy anyone has to configure.
_MAX_BYTES = 64 * 1024
_KEEP = 200


def _records(path: Path) -> list[dict]:
    """Every readable record in the file. A bad line costs itself, never the history."""
    path = Path(path)
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict):
            out.append(record)
    return out


def append(path: Path, record: dict) -> None:
    """Add one record, trimming the file when it grows past `_MAX_BYTES`.

    Best-effort on purpose, like `hooklog`: every exception is swallowed. A journal that could
    break a hook would be a new way for memory to die silently, which is the opposite of the point.
    """
    try:
        path = Path(path)
        entry = dict(record)
        entry.setdefault("at", watermark.mark_at()["timestamp"])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if path.stat().st_size > _MAX_BYTES:
            kept = _records(path)[-_KEEP:]
            body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept)
            path.write_text(body, encoding="utf-8")
    except Exception:
        pass


def tail(path: Path, n: int) -> list[dict]:
    """The last `n` records, oldest first. Empty when the file is absent or unreadable."""
    if n <= 0:
        return []
    return _records(path)[-n:]
