from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import paths, pending
from lts.config import load_config
from lts.hooklog import log_error, log_note


def run(event: dict, *, root: Path | None = None) -> dict:
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        log_note("pre_compact.py", f"no lts project at or above {cfg.project_root}; no snapshot")
        return {}
    paths.ensure_sidecar(cfg)
    transcript_path = Path(event.get("transcript_path", ""))
    content = ""
    if transcript_path.exists():
        content = transcript_path.read_text(encoding="utf-8", errors="ignore")
    pending.dump_snapshot(paths.pending_dir(cfg), event.get("session_id", "default"), content)
    return {}


def main() -> None:
    try:
        event = json.load(sys.stdin)
        result = run(event)
    except Exception:
        log_error(__file__)
        result = {}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
