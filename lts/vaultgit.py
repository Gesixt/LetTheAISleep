"""Read-only git questions about the vault directory.

Nothing here mutates a repository or touches the network: no fetch, pull, commit or push.
Publishing memory is the developer's decision, and a hook must never block on the network.
Consequently `ahead_behind` is relative to the last *fetched* remote ref, which callers must
say out loud rather than implying freshness they do not have.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_TIMEOUT = 10
_SEP = "\x00"


def _run(vault: Path, *args: str) -> str | None:
    """Run a git command in `vault`; return stdout, or None if git failed or is unavailable."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(vault), *args],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def is_git_repo(vault: Path) -> bool:
    # A bare `rev-parse --git-dir` succeeds from ANY subdir of a git repo, so the default
    # `<project_root>/.ai_vault` inside a code repo would be mistaken for a vault repo and the
    # digest would report the enclosing repo's source changes. Require the vault to be the
    # work-tree root itself (the team-mode case: a separate clone with its own inner `.git`).
    top = _run(vault, "rev-parse", "--show-toplevel")
    if top is None:
        return False
    return Path(top.strip()).resolve() == vault.resolve()


def local_identity(vault: Path) -> str | None:
    out = _run(vault, "config", "user.name")
    return out.strip() if out and out.strip() else None


def commits_since(vault: Path, since: str) -> list[tuple[str, list[str]]]:
    """[(author name, [changed paths])], newest commit first.

    A NUL sentinel separates commits because `--name-only` otherwise interleaves the header
    with the file list, and author names may contain anything. The sentinel is spelled
    `%x00` — git's own pretty-format escape for a literal NUL byte — rather than an actual
    `\\x00` character in the argv string; Python's subprocess refuses to exec an argument
    that itself contains an embedded NUL byte, so the byte must be produced by git in its
    output, not carried in as one of our own arguments.
    """
    out = _run(
        vault, "log", f"--since={since}", "--no-merges",
        "--name-only", "--pretty=format:%x00%an",
    )
    if not out:
        return []
    commits: list[tuple[str, list[str]]] = []
    for chunk in out.split(_SEP):
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            continue
        commits.append((lines[0], lines[1:]))
    return commits


def upstream_name(vault: Path) -> str | None:
    out = _run(vault, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    return out.strip() if out and out.strip() else None


def ahead_behind(vault: Path) -> tuple[int, int] | None:
    """(commits we have that the remote does not, commits the remote has that we do not)."""
    out = _run(vault, "rev-list", "--left-right", "--count", "HEAD...@{upstream}")
    if not out:
        return None
    parts = out.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def dirty_count(vault: Path) -> int:
    out = _run(vault, "status", "--porcelain")
    return len([ln for ln in (out or "").splitlines() if ln.strip()])