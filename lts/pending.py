from __future__ import annotations

from pathlib import Path

from lts.paths import safe_session_id


def dump_snapshot(pending_dir: Path, session_id: str, content: str) -> Path:
    pending_dir.mkdir(parents=True, exist_ok=True)
    safe = safe_session_id(session_id)
    n = 1
    while (pending_dir / f"{safe}-{n}.md").exists():
        n += 1
    target = pending_dir / f"{safe}-{n}.md"
    target.write_text(content, encoding="utf-8")
    return target


def list_snapshots(pending_dir: Path) -> list[Path]:
    if not pending_dir.exists():
        return []
    return sorted(pending_dir.glob("*.md"))


def has_pending(pending_dir: Path) -> bool:
    return len(list_snapshots(pending_dir)) > 0


def clear_all(pending_dir: Path) -> None:
    for snap in list_snapshots(pending_dir):
        snap.unlink(missing_ok=True)
