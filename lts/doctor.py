"""Find sidecars that sprouted in the wrong place.

A stray `.ai_memory/` is memory the rest of the system will never look at again: `/sleep` reads
the buffer at the project root, so anything captured elsewhere is silently orphaned. `lts.health`
turns this into the `sidecars` check; the reporting that used to live here is now `health.render`.
"""

from __future__ import annotations

import os
from pathlib import Path

SIDECAR = ".ai_memory"
_SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", ".venv", "venv", "__pycache__"}


def find_sidecars(root: Path) -> list[Path]:
    """Every `.ai_memory/` under `root`, excluding the root's own."""
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and d != SIDECAR]
        here = Path(dirpath)
        if here != root and (here / SIDECAR).is_dir():
            found.append(here / SIDECAR)
    return sorted(found)
