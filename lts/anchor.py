from __future__ import annotations

import json
from pathlib import Path


def read_anchor(anchor_path: Path) -> dict:
    if not anchor_path.exists():
        return {}
    try:
        return json.loads(anchor_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return {}


def write_anchor(
    anchor_path: Path,
    *,
    updated: str,
    last_session: str,
    active_topics: list[str],
    active_notes: list[str],
    next_task: str | None = None,
) -> None:
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated": updated,
        "last_session": last_session,
        "active_topics": active_topics,
        "active_notes": active_notes,
    }
    if next_task:
        payload["next_task"] = next_task
    anchor_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def render_anchor(anchor: dict) -> str:
    if not anchor:
        return ""
    topics = ", ".join(anchor.get("active_topics", []))
    notes = ", ".join(anchor.get("active_notes", []))
    return (
        "## Session Anchor (memory entry points)\n"
        f"- Last session: {anchor.get('last_session', '')}\n"
        f"- Active topics: {topics}\n"
        f"- Active notes: {notes}\n"
        "Pull bodies on demand via Basic Memory `read_note`/`search`; do not load everything."
    )
