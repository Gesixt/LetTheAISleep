"""Find sidecars that sprouted in the wrong place.

A stray `.ai_memory/` is memory the rest of the system will never look at again: `/sleep` reads
the buffer at the project root, so anything captured elsewhere is silently orphaned. This list
becomes the `sidecars` check in `lts.healthchecks._check_sidecars`, which `lts.health` orders
and renders; the reporting that used to live here is now `health.render`.
"""

from __future__ import annotations

import os
from pathlib import Path

SIDECAR = ".ai_memory"
# Directories this walk does not enter. They are named in the check's own message, because a
# sidecar inside one of them is invisible here and "no stray sidecars" would otherwise claim more
# than the walk can see — a vendored repository carrying its own `.ai_memory` is the realistic
# carrier. Public for that reason: the caller states the scope it was actually given.
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__"}


def find_sidecars(root: Path) -> tuple[list[Path], list[Path]]:
    """`(sidecars, unreadable)`: every `.ai_memory/` under `root` bar the root's own, and every
    directory the walk could not enter.

    The second list is the load-bearing half. `os.walk` swallows every error it meets unless it is
    handed `onerror`, so a subtree it cannot read yields nothing and any sidecar under it simply
    disappears — while the caller went on to report "no stray sidecars", an absence claim about a
    tree the walk had never seen. The unreadable directories are returned rather than logged
    because only the caller can decide the level, and this one must not be reported as an absence.
    """
    found: list[Path] = []
    unreadable: list[Path] = []

    def blocked(error: OSError) -> None:
        # `os.walk` reports the directory it could not scan as the error's `filename`; it calls this
        # instead of yielding that directory, so nothing under it is examined.
        unreadable.append(Path(getattr(error, "filename", None) or root))

    for dirpath, dirnames, _ in os.walk(root, onerror=blocked):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and d != SIDECAR]
        here = Path(dirpath)
        if here != root and (here / SIDECAR).is_dir():
            found.append(here / SIDECAR)
    return sorted(found), sorted(unreadable)
