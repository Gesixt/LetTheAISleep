"""Watermarks: how far through a transcript memory has already been taken.

A Claude Code transcript is one long-lived, append-only file. `--resume` keeps writing to it,
so "the transcript" is the whole history of a project, not the current chapter — a session
file can span months. Without a mark, every PreCompact snapshot re-dumps everything that was
already consolidated, and the next SessionStart reports a sleep that is not actually owed.

Two marks use this module, both stored as `{"uuid", "timestamp", "count"}`:

* the **capture mark** — the last exchange the Stop hook put into the STM buffer;
* the **sleep mark** — the last exchange that is already consolidated into long-term notes
  or captured in a pending snapshot.

Resolution is deliberately layered: `uuid` is exact, `timestamp` survives a transcript that
was rewritten under us, and `count` is the last resort for transcripts that carry neither.

A mark placed at the *end of a turn* carries a timestamp only (`mark_at`), because the last
message of the turn may not be in the file yet when the hook reads it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from lts.transcript import read_exchanges


def mark_of(transcript_path: Path) -> dict:
    """The mark naming the last user/assistant exchange in `transcript_path` ({} if there is none)."""
    exchanges = read_exchanges(Path(transcript_path))
    if not exchanges:
        return {}
    last = exchanges[-1]
    return {
        "uuid": last.get("uuid"),
        "timestamp": last.get("timestamp"),
        "count": len(exchanges),
    }


def mark_at(when: datetime | None = None) -> dict:
    """A mark that is an instant, not a position — everything stamped at or before it is done.

    `mark_of` cannot be used at the end of a turn: a hook can read the transcript before
    Claude Code has written the turn's last message to it, so the mark lands one message
    short and that message leaks into the next snapshot. An instant covers it, because a
    message is stamped when it is produced and the hook always runs after that.
    """
    when = when or datetime.now(timezone.utc)
    stamp = when.astimezone(timezone.utc).isoformat(timespec="milliseconds")
    return {"timestamp": stamp.replace("+00:00", "Z")}


def entries_after(transcript_path: Path, mark: dict) -> list[dict]:
    """The exchanges of `transcript_path` that follow `mark` (all of them when it is empty)."""
    return exchanges_after(read_exchanges(Path(transcript_path)), mark)


def exchanges_after(exchanges: list[dict], mark: dict) -> list[dict]:
    """The same resolution, over exchanges already in hand.

    Split out so that a caller which must also ask whether the transcript yielded any exchanges at
    all — `healthchecks._check_capture`, which reports a skip rather than a clean bill when it
    yielded none — can answer both questions from one parse instead of reading the file twice.
    """
    if not mark:
        return exchanges

    uuid = mark.get("uuid")
    if uuid:
        for i, ex in enumerate(exchanges):
            if ex.get("uuid") == uuid:
                return exchanges[i + 1:]

    stamp = mark.get("timestamp")
    if stamp:
        # Claude Code writes one ISO-8601 shape, so lexicographic order is chronological.
        return [ex for ex in exchanges if (ex.get("timestamp") or "") > stamp]

    count = int(mark.get("count") or 0)
    # A count that overruns means this is a different (or truncated) transcript, not one we
    # have already read to the end — start from the beginning rather than swallowing it whole.
    return exchanges[count:] if count <= len(exchanges) else exchanges


def read_mark(path: Path) -> dict:
    """The mark stored at `path`, or {} when it is absent or unreadable."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_mark(path: Path, mark: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mark, ensure_ascii=False), encoding="utf-8")


def arm(flag_path: Path) -> None:
    """Announce that a sleep just finished, so the hooks discard their own noise."""
    flag_path = Path(flag_path)
    flag_path.parent.mkdir(parents=True, exist_ok=True)
    flag_path.write_text("", encoding="utf-8")


def is_armed(flag_path: Path) -> bool:
    return Path(flag_path).exists()


def disarm(flag_path: Path) -> None:
    Path(flag_path).unlink(missing_ok=True)


def render_exchanges(exchanges: list[dict]) -> str:
    """A pending snapshot as readable prose.

    Raw JSONL was unreadable in practice — a snapshot of a months-long session ran to tens of
    thousands of lines, so it got skimmed rather than consolidated. Text is kept verbatim
    (unlike the STM buffer, nothing is truncated here); tool calls and thinking are dropped.
    """
    return "\n\n".join(f"[{ex['role']}] {ex['text']}" for ex in exchanges)
