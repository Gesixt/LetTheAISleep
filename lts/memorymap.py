"""The memory map: a table of contents for the vault, cheap enough to show on every turn.

The model was not refusing to recall — it had nothing to aim at. `SessionStart` returned the
sleep demand *instead of* the anchor, and in a long-lived session the STM buffer is almost never
empty, so the anchor never arrived; `UserPromptSubmit` carried nothing but sleep pressure. Between
two compactions, nothing told the model the vault existed at all.

This module answers only "what notes are there", in titles, within a character budget. Deciding
which of them to read stays with the model — the `Architecture` note puts curation on the soft
side of the soft/hard line on purpose. The hook's job is to make the entry points impossible to
miss, not to choose for it.
"""

from __future__ import annotations

from pathlib import Path

from lts import paths
from lts.config import Config

_FOOTER = "If the question touches any of these, read the note instead of re-deriving it."
_TAIL = "… +{n} more (use /recall)"

# knowledge-base carries the most meaning per title, so it is read first and truncated last;
# session notes are episodic and go at the end.
_FIRST, _MIDDLE, _LAST = 0, 1, 2


def _tier(group: str) -> int:
    if group == "knowledge-base":
        return _FIRST
    if group == "session-memory":
        return _LAST
    return _MIDDLE


def _collect(vault: Path) -> list[tuple[str, list[str]]]:
    """Note titles grouped by top-level vault directory, in display order.

    Basic Memory indexes every `.md` and ignores `.bmignore` (established by experiment, see
    the `Basic Memory Behaviour` note), so nothing is excluded here either — the map lists
    exactly what is findable. Team mode's `session-memory/<author>/` collapses into one group.
    """
    groups: dict[str, list[str]] = {}
    for note in vault.rglob("*.md"):
        if not note.is_file():
            continue
        rel = note.relative_to(vault)
        group = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        groups.setdefault(group, []).append(note.stem)
    ordered = []
    for group in sorted(groups, key=lambda g: (_tier(g), g)):
        # Session names are `Session_YYYY-MM-DD_HHMM`, so a reverse string sort is newest-first.
        titles = sorted(groups[group], reverse=_tier(group) == _LAST)
        ordered.append((group, titles))
    return ordered


def _header(total: int) -> str:
    noun = "note" if total == 1 else "notes"
    return f"## Memory map ({total} {noun}) — read before re-deriving"


def render(cfg: Config) -> str:
    """The map, or "" when there is nothing worth saying.

    Silence matters as much as the content: a map is injected on every single turn, so an
    absent vault, an empty one, or a budget of 0 must produce no output at all rather than a
    header with nothing under it.
    """
    if not cfg.configured or cfg.map_budget <= 0:
        return ""
    vault = paths.vault_root(cfg)
    if not vault.is_dir():
        return ""
    ordered = _collect(vault)
    total = sum(len(titles) for _, titles in ordered)
    if not total:
        return ""

    header = _header(total)
    room = cfg.map_budget - len(header) - len(_FOOTER) - 2  # the two newlines around the body
    reserve = len(_TAIL.format(n=total))
    if room <= reserve:
        return ""

    lines: list[str] = []
    shown = 0
    full = True
    for group, titles in ordered:
        if not full:
            break
        line = f"{group}:"
        for title in titles:
            candidate = f"{line} {title}" if line.endswith(":") else f"{line} · {title}"
            used = sum(len(ln) + 1 for ln in lines) + len(candidate) + 1
            if used > room - reserve:
                full = False
                break
            line = candidate
            shown += 1
        if not line.endswith(":"):
            lines.append(line)

    if not shown:
        return ""
    if shown < total:
        lines.append(_TAIL.format(n=total - shown))
    return "\n".join([header, *lines, _FOOTER])
