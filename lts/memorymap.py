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

from lts import anchor, paths
from lts.config import Config

_FOOTER = "If the question touches any of these, read the note instead of re-deriving it."
_TAIL = "… +{n} more (use /recall)"
_ACTIVE = "active"

# knowledge-base carries the most meaning per title, so it is read first and truncated last;
# session notes are episodic and go at the end.
_FIRST, _MIDDLE, _LAST = 0, 1, 2


def _tier(group: str) -> int:
    if group == "knowledge-base":
        return _FIRST
    if group == "session-memory":
        return _LAST
    return _MIDDLE


def _address(rel: Path) -> str:
    """A note's address in the form the anchor stores it: `knowledge-base/Memory Map`."""
    return rel.with_suffix("").as_posix()


def _rank(group: str, notes: list[Path]) -> list[Path]:
    """Order one group's notes by the best signal that group actually has.

    A session title *is* its date — exact, and it survives a `git clone`, which resets every
    mtime in the vault to the checkout time; editing an old session note to fix a typo must not
    make it look like the latest chapter. A topical title says nothing about relevance, so there
    recency leads and the alphabet only breaks ties — which, after a fresh clone, is all there is.
    """
    if _tier(group) == _LAST:
        return sorted(notes, key=lambda n: n.stem, reverse=True)
    return sorted(notes, key=lambda n: (-int(n.stat().st_mtime), n.stem))


def _collect(vault: Path) -> list[tuple[str, list[tuple[str, str]]]]:
    """`(group, [(address, title)])` by top-level vault directory, groups and notes in display order.

    Basic Memory indexes every `.md` and ignores `.bmignore` (established by experiment, see
    the `Basic Memory Behaviour` note), so nothing is excluded here either — the map lists
    exactly what is findable. Team mode's `session-memory/<author>/` collapses into one group.
    """
    groups: dict[str, list[Path]] = {}
    for note in vault.rglob("*.md"):
        if not note.is_file():
            continue
        rel = note.relative_to(vault)
        group = rel.parts[0] if len(rel.parts) > 1 else "(root)"
        groups.setdefault(group, []).append(note)
    ordered = []
    for group in sorted(groups, key=lambda g: (_tier(g), g)):
        ranked = _rank(group, groups[group])
        ordered.append((group, [(_address(n.relative_to(vault)), n.stem) for n in ranked]))
    return ordered


def _active(cfg: Config, known: set[str]) -> list[str]:
    """The anchor's entry points, in anchor order, minus the ones that no longer exist.

    An anchor outlives the notes it points at; advertising a deleted one costs a wasted
    `read_note` and teaches the model that the map lies.
    """
    entries = anchor.read_anchor(paths.anchor_file(cfg)).get("active_notes") or []
    live: list[str] = []
    for entry in entries:
        address = _address(Path(str(entry).strip().lstrip("/")))
        if address in known and address not in live:
            live.append(address)
    return live


def _sections(cfg: Config, ordered: list[tuple[str, list[tuple[str, str]]]]) -> list[tuple[str, list[str]]]:
    """The map's lines, most useful first: the anchor's notes, then the group tiers.

    The anchor is the one ranking signal we have that the model did not produce this turn — the
    last sleep chose those notes itself. It is hoisted above the tiers because its entry points
    cross them: the last session note lives in `session-memory`, the first group truncation drops.
    Anchored notes are listed by full address (what `read_note` wants) and dropped from their
    group, so the budget never pays for the same title twice.
    """
    active = _active(cfg, {address for _, notes in ordered for address, _ in notes})
    sections = [(_ACTIVE, active)] if active else []
    taken = set(active)
    for group, notes in ordered:
        titles = [title for address, title in notes if address not in taken]
        if titles:
            sections.append((group, titles))
    return sections


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
    total = sum(len(notes) for _, notes in ordered)
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
    for label, entries in _sections(cfg, ordered):
        if not full:
            break
        line = f"{label}:"
        for entry in entries:
            candidate = f"{line} {entry}" if line.endswith(":") else f"{line} · {entry}"
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
