"""Diagnose where memory actually lives, and find sidecars that sprouted in the wrong place.

A stray `.ai_memory/` is memory the rest of the system will never look at again: `/sleep`
reads the buffer at the project root, so anything captured elsewhere is silently orphaned.
"""

from __future__ import annotations

import os
from pathlib import Path

from lts import paths
from lts.config import Config, is_lts_config

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


def collect(cfg: Config) -> dict:
    root = cfg.project_root
    strays: list[Path] = []
    nested_roots: list[Path] = []
    if cfg.configured:
        for sidecar in find_sidecars(root):
            owner = sidecar.parent
            # a sidecar next to its own lts config.toml is a nested project, not an accident
            (nested_roots if is_lts_config(owner / "config.toml") else strays).append(sidecar)

    return {
        "root": str(root),
        "configured": cfg.configured,
        "project": cfg.project,
        "sidecar": str(paths.sidecar_root(cfg)),
        "stray_sidecars": [str(p) for p in strays],
        "nested_project_roots": [str(p.parent) for p in nested_roots],
        "ok": cfg.configured and not strays,
    }


def render(report: dict) -> str:
    lines = [f"Project root: {report['root']}"]
    if not report["configured"]:
        lines += [
            "  ✗ no config.toml with a [vault] section at or above this directory.",
            "    Memory writes will refuse rather than create a sidecar here.",
            "    Fix: run `install.py --target <project>`, or restore a moved config.toml.",
        ]
        return "\n".join(lines)

    lines += [
        f"  ✓ configured — project '{report['project']}'",
        f"  Sidecar: {report['sidecar']}",
    ]
    for nested in report["nested_project_roots"]:
        lines.append(f"  ! nested lts project root: {nested}")
    if report["stray_sidecars"]:
        lines.append("  ✗ stray sidecars (orphaned memory — /sleep will never read these):")
        lines += [f"      {p}" for p in report["stray_sidecars"]]
        lines.append("    Merge anything you need into the root buffer, then delete them.")
    else:
        lines.append("  ✓ no stray sidecars")
    return "\n".join(lines)