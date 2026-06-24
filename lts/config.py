from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULTS = {
    "vault_mode": "per_project",   # per_project | global
    "project": "lts-default",
    "vault_path": None,
    "pressure_warn": 0.60,
    "pressure_force": 0.80,
    "context_window": 1_000_000,   # model context window; override in config.toml (e.g. 200_000 for Sonnet)
}


@dataclass(frozen=True)
class Config:
    project_root: Path
    vault_mode: str
    project: str
    vault_path: str | None
    pressure_warn: float
    pressure_force: float
    context_window: int


def find_project_root(start: Path) -> Path:
    """The lts project root is marked solely by `config.toml` (walking up from `start`).

    We deliberately do NOT use `.git`: a project may not be a git repo at all, and a
    multi-repo project can have nested sub-repos (a service's own `.git`) that would be
    the wrong root. If no `config.toml` is found, the start directory itself is the root
    (this is the pre-install case — `install.py` writes `config.toml` there).
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        if (candidate / "config.toml").exists():
            return candidate
    return start


def load_config(start: Path | None = None) -> Config:
    root = find_project_root(Path(start) if start else Path.cwd())
    values = dict(DEFAULTS)
    cfg_file = root / "config.toml"
    if cfg_file.exists():
        data = tomllib.loads(cfg_file.read_text(encoding="utf-8"))
        vault = data.get("vault", {})
        sleep = data.get("sleep", {})
        if "mode" in vault:
            values["vault_mode"] = vault["mode"]
        if "project" in vault:
            values["project"] = vault["project"]
        if "path" in vault:
            values["vault_path"] = vault["path"]
        if "pressure_warn" in sleep:
            values["pressure_warn"] = float(sleep["pressure_warn"])
        if "pressure_force" in sleep:
            values["pressure_force"] = float(sleep["pressure_force"])
        if "context_window" in sleep:
            values["context_window"] = int(sleep["context_window"])
    return Config(project_root=root, **values)
