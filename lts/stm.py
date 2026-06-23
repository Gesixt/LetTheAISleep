from __future__ import annotations

from pathlib import Path


def append(stm_path: Path, text: str) -> None:
    stm_path.parent.mkdir(parents=True, exist_ok=True)
    with stm_path.open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def read(stm_path: Path) -> str:
    if not stm_path.exists():
        return ""
    return stm_path.read_text(encoding="utf-8")


def is_empty(stm_path: Path) -> bool:
    return read(stm_path).strip() == ""


def clear(stm_path: Path) -> None:
    stm_path.unlink(missing_ok=True)
