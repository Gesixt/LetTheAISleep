from __future__ import annotations

import re
from pathlib import Path

from lts.config import Config, require_project

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_session_id(session_id: str) -> str:
    cleaned = _UNSAFE.sub("_", session_id or "")
    return cleaned or "default"


def sidecar_root(cfg: Config) -> Path:
    return cfg.project_root / ".ai_memory"


def vault_root(cfg: Config) -> Path:
    """The Basic Memory vault directory: `[vault] path`, else `<project_root>/.ai_vault`.

    Unlike the sidecar this may live outside the project (a shared vault), and in team mode
    it is its own git repository, so it is resolved separately from `sidecar_root`.
    """
    if cfg.vault_path:
        return Path(cfg.vault_path).expanduser()
    return cfg.project_root / ".ai_vault"


def stm_file(cfg: Config) -> Path:
    """Single per-project STM buffer (no session dimension — keeps hooks and skills in sync)."""
    return sidecar_root(cfg) / "stm" / "buffer.md"


def anchor_file(cfg: Config) -> Path:
    return sidecar_root(cfg) / "anchor.json"


def sleep_mark_file(cfg: Config) -> Path:
    """How far consolidation has reached in the transcript (see `lts.watermark`)."""
    return sidecar_root(cfg) / "sleep-mark.json"


def capture_mark_file(cfg: Config) -> Path:
    """How far the Stop hook has read the transcript into the STM buffer."""
    return sidecar_root(cfg) / "stm" / "capture-mark.json"


def legacy_capture_offset_file(cfg: Config) -> Path:
    """Pre-watermark cursor: a bare exchange count. Honoured once, then removed."""
    return sidecar_root(cfg) / "stm" / ".lts-capture-offset"


def sleep_flag_file(cfg: Config) -> Path:
    """Set by `lts stm clear`: a sleep just finished, mid-turn. Cleared by the next hook."""
    return sidecar_root(cfg) / "stm" / ".lts-sleep-armed"


def pending_dir(cfg: Config) -> Path:
    return sidecar_root(cfg) / "pending_consolidation"


def health_journal_file(cfg: Config) -> Path:
    """Per-run health records (see `lts.journal`) — the input to the trend checks."""
    return sidecar_root(cfg) / "health.jsonl"


def ensure_sidecar(cfg: Config) -> None:
    """Create the sidecar — only ever at a configured project root, never at a bare cwd."""
    require_project(cfg)
    (sidecar_root(cfg) / "stm").mkdir(parents=True, exist_ok=True)
    pending_dir(cfg).mkdir(parents=True, exist_ok=True)
