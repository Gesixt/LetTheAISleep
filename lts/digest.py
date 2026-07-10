"""What changed in the vault since my last sleep, and who changed it.

Authorship is the point. Only git knows it, so git is the primary source; a plain `mtime` scan is
the degradation path for a vault that is not a repository (the single-developer case, where
authorship is meaningless anyway). `mtime` cannot attribute, and `git pull`/`checkout` rewrites
mtimes, so it would report false positives in a team — it is a fallback, never a preference.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lts import anchor, naming, paths, vaultgit
from lts.config import Config

_UNKNOWN_BASELINE = "no anchor yet, so there is no baseline to compare against"


def _parse_since(since: str) -> datetime | None:
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(since, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(since)
    except ValueError:
        return None


def _unavailable(reason: str) -> dict:
    return {
        "available": False, "reason": reason, "since": None, "source": None,
        "knowledge_base": [], "session_memory": [], "git": None, "empty": True,
    }


def _from_git(vault: Path, since: str) -> dict[str, list[str]]:
    """path -> author names, excluding this developer's own commits."""
    me = vaultgit.local_identity(vault)
    changes: dict[str, list[str]] = {}
    for author, files in vaultgit.commits_since(vault, since):
        if me and author == me:
            continue
        for rel in files:
            if not rel.endswith(".md"):
                continue
            authors = changes.setdefault(rel, [])
            if author not in authors:
                authors.append(author)
    return changes


def _from_mtime(vault: Path, cutoff: datetime) -> dict[str, list[str]]:
    changes: dict[str, list[str]] = {}
    for note in vault.rglob("*.md"):
        if ".git" in note.parts:
            continue
        if datetime.fromtimestamp(note.stat().st_mtime) > cutoff:
            changes[str(note.relative_to(vault))] = []
    return changes


def _entries(changes: dict[str, list[str]], prefix: str) -> list[dict]:
    out = [
        {"note": Path(rel).stem, "path": rel, "authors": authors}
        for rel, authors in changes.items()
        if rel.startswith(f"{prefix}/")
    ]
    return sorted(out, key=lambda e: e["note"])


def collect(cfg: Config, *, since: str | None = None) -> dict:
    vault = paths.vault_root(cfg)
    if not vault.is_dir():
        return _unavailable(f"vault directory not found at {vault}")

    since = since or anchor.read_anchor(paths.anchor_file(cfg)).get("updated")
    if not since:
        return _unavailable(_UNKNOWN_BASELINE)

    git_block = None
    if vaultgit.is_git_repo(vault):
        source = "git"
        changes = _from_git(vault, since)
        ab = vaultgit.ahead_behind(vault)
        git_block = {
            "upstream": vaultgit.upstream_name(vault),
            "ahead": ab[0] if ab else None,
            "behind": ab[1] if ab else None,
            "uncommitted": vaultgit.dirty_count(vault),
        }
    else:
        source = "mtime"
        cutoff = _parse_since(since)
        if cutoff is None:
            return _unavailable(f"cannot parse baseline timestamp {since!r}")
        changes = _from_mtime(vault, cutoff)

    kb = _entries(changes, naming.KNOWLEDGE_DIR)
    sm = _entries(changes, naming.SESSION_DIR)
    quiet_git = git_block is None or not (
        git_block["uncommitted"] or git_block["ahead"] or git_block["behind"]
    )
    return {
        "available": True, "reason": None, "since": since, "source": source,
        "knowledge_base": kb, "session_memory": sm, "git": git_block,
        "empty": not kb and not sm and quiet_git,
    }


def _with_authors(entries: list[dict]) -> str:
    return ", ".join(
        f"{e['note']} ({', '.join(e['authors'])})" if e["authors"] else e["note"]
        for e in entries
    )


def _by_author(entries: list[dict]) -> str:
    counts: dict[str, int] = {}
    for e in entries:
        for a in e["authors"] or ["unknown"]:
            counts[a] = counts.get(a, 0) + 1
    if list(counts) == ["unknown"]:
        n = counts["unknown"]
        return f"{n} note{'s' if n != 1 else ''}"
    return ", ".join(
        f"{n} note{'s' if n != 1 else ''} by {a}" for a, n in sorted(counts.items())
    )


def _git_line(git: dict) -> str:
    bits = []
    if git["behind"]:
        bits.append(f"{git['behind']} commits behind {git['upstream']}")
    if git["ahead"]:
        bits.append(f"{git['ahead']} commits ahead of {git['upstream']}")
    if git["uncommitted"]:
        n = git["uncommitted"]
        bits.append(f"{n} uncommitted file{'s' if n != 1 else ''}")
    return ", ".join(bits)


def render(report: dict) -> str:
    if not report["available"] or report["empty"]:
        return ""
    lines = [f"Since your last sleep ({report['since']}):"]
    if report["knowledge_base"]:
        lines.append(f"  knowledge-base:  {_with_authors(report['knowledge_base'])}")
    if report["session_memory"]:
        lines.append(f"  session-memory:  {_by_author(report['session_memory'])}")
    git = report["git"]
    if git and (line := _git_line(git)):
        lines.append(f"  git:             {line}")
        lines.append("                   (relative to the last fetched remote ref; no network access)")
    return "\n".join(lines)