from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "vault_mode": "per_project",   # per_project | global
    "project": "lts-default",
    "vault_path": None,
    "author": None,                # personal namespace; None => single-developer mode
    "pressure_warn": 0.60,
    "pressure_force": 0.80,
    "context_window": 1_000_000,   # model context window; override in config.toml (e.g. 200_000 for Sonnet)
    "map_budget": 1500,            # chars of memory map injected per turn; 0 disables it
}


class NotAnLtsProject(RuntimeError):
    """Raised instead of silently creating a sidecar outside a configured project."""

    def __init__(self, start: Path) -> None:
        super().__init__(
            f"no lts project at or above {start}. "
            "The .ai_memory sidecar is only ever created next to a config.toml that has a "
            "[vault] section. Run `install.py --target <project>` first, or pass "
            "`--root <project-root>`."
        )


@dataclass(frozen=True)
class Config:
    project_root: Path
    configured: bool          # False when no lts config.toml was found: reads work, writes refuse
    vault_mode: str
    project: str
    vault_path: str | None
    author: str | None        # None => single-developer mode; otherwise a slug-stable namespace
    pressure_warn: float
    pressure_force: float
    context_window: int
    map_budget: int           # characters; the map degrades to fit, and 0 turns it off


def is_lts_config(path: Path) -> bool:
    """True for a config.toml that is *ours* — i.e. declares a `[vault]` table.

    `config.toml` is a common filename (Hugo, Zola, mdBook, .cargo/, .streamlit/ …), so its
    mere presence must not mark a project root; a foreign one would silently become the root.
    """
    if not path.is_file():
        return False
    try:
        return "vault" in tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False


def find_project_root(start: Path) -> Path | None:
    """Nearest ancestor of `start` holding an lts `config.toml`, or None if there is none.

    We deliberately do NOT use `.git`: a project may not be a git repo at all, and a
    multi-repo project can have nested sub-repos (a service's own `.git`) that would be
    the wrong root. Returning None rather than falling back to `start` is what keeps a
    stray `.ai_memory/` from sprouting in whatever directory we happened to be run from.
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        if is_lts_config(candidate / "config.toml"):
            return candidate
    return None


def require_project(cfg: Config) -> None:
    """Gate every write: refuse to touch the filesystem outside a real project root."""
    if not cfg.configured:
        raise NotAnLtsProject(cfg.project_root)


def load_config(start: Path | None = None) -> Config:
    start = Path(start) if start else Path.cwd()
    root = find_project_root(start)
    configured = root is not None
    if root is None:
        root = start.resolve()
    values = dict(DEFAULTS)
    cfg_file = root / "config.toml"
    if configured:
        data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
        vault = data.get("vault", {})
        sleep = data.get("sleep", {})
        memory = data.get("memory", {})
        if "mode" in vault:
            values["vault_mode"] = vault["mode"]
        if "project" in vault:
            values["project"] = vault["project"]
        if "path" in vault:
            values["vault_path"] = vault["path"]
        if "author" in vault:
            values["author"] = vault["author"]
        if "pressure_warn" in sleep:
            values["pressure_warn"] = float(sleep["pressure_warn"])
        if "pressure_force" in sleep:
            values["pressure_force"] = float(sleep["pressure_force"])
        if "context_window" in sleep:
            values["context_window"] = int(sleep["context_window"])
        if "map_budget" in memory:
            values["map_budget"] = int(memory["map_budget"])
    return Config(project_root=root, configured=configured, **values)