"""Best-effort error logging for hooks, so silent failures are debuggable."""

from __future__ import annotations

import traceback
from pathlib import Path

ERROR_LOG = Path("/tmp/lts-hook-errors.log")


def log_error(hook_name: str) -> None:
    try:
        with ERROR_LOG.open("a", encoding="utf-8") as fh:
            fh.write(f"--- {hook_name} ---\n{traceback.format_exc()}\n")
    except Exception:
        pass