from __future__ import annotations

import re
from pathlib import Path

from lts.config import Config

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_session_id(session_id: str) -> str:
    cleaned = _UNSAFE.sub("_", session_id or "")
    return cleaned or "default"


def sidecar_root(cfg: Config) -> Path:
    return cfg.project_root / ".ai_memory"


def stm_file(cfg: Config, session_id: str) -> Path:
    return sidecar_root(cfg) / "stm" / f"{safe_session_id(session_id)}.md"


def anchor_file(cfg: Config) -> Path:
    return sidecar_root(cfg) / "anchor.json"


def pending_dir(cfg: Config) -> Path:
    return sidecar_root(cfg) / "pending_consolidation"


def ensure_sidecar(cfg: Config) -> None:
    (sidecar_root(cfg) / "stm").mkdir(parents=True, exist_ok=True)
    pending_dir(cfg).mkdir(parents=True, exist_ok=True)
