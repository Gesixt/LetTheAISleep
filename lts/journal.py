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
import os
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


def _unterminated(path: Path) -> bool:
    """True when the file does not end in a newline — i.e. its last write did not finish.

    A hook killed mid-write leaves the record it was writing without its final `\n`, which is
    precisely the byte that did not make it. That state is indistinguishable from "a record is
    still being written", and this module has one writer, so it is the former.
    """
    try:
        if not path.stat().st_size:
            return False
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _trim(path: Path) -> None:
    """Keep the most recent `_KEEP` records, replacing the file in one step.

    Write-then-rename, not `path.write_text`: the plain rewrite truncated the journal to zero
    before the first byte of the replacement was written, so a crash inside it lost the history
    that `_KEEP` exists to preserve — the same fault as a truncated append, one level up.
    `os.replace` is atomic on a POSIX filesystem, so a reader sees either the old file or the new.

    Best-effort, like `append`: a trim that fails leaves the untrimmed file, which is correct and
    merely too long, and takes its temporary file with it.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        kept = _records(path)[-_KEEP:]
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept)
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def append(path: Path, record: dict) -> None:
    """Add one record, trimming the file when it grows past `_MAX_BYTES`.

    Best-effort on purpose, like `hooklog`: every exception is swallowed. A journal that could
    break a hook would be a new way for memory to die silently, which is the opposite of the point.

    A line this process did not finish is closed before the new record is written, never continued.
    Appending into an unterminated line fused the fragment and the new record into one invalid JSON
    line, and `_records` then discarded **both** — so a truncated write cost the *next* record as
    well as itself, which is the opposite of what `_records` promises.
    """
    try:
        path = Path(path)
        entry = dict(record)
        entry.setdefault("at", watermark.mark_at()["timestamp"])
        path.parent.mkdir(parents=True, exist_ok=True)
        broken = _unterminated(path)
        with path.open("a", encoding="utf-8") as fh:
            if broken:
                fh.write("\n")
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        if path.stat().st_size > _MAX_BYTES:
            _trim(path)
    except Exception:
        pass


def tail(path: Path, n: int) -> list[dict]:
    """The last `n` records, oldest first. Empty when the file is absent or unreadable."""
    if n <= 0:
        return []
    return _records(path)[-n:]
