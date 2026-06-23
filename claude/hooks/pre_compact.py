from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import paths, pending
from lts.config import load_config


def run(event: dict, *, root: Path | None = None) -> dict:
    cfg = load_config(root)
    paths.ensure_sidecar(cfg)
    transcript_path = Path(event.get("transcript_path", ""))
    content = ""
    if transcript_path.exists():
        content = transcript_path.read_text(encoding="utf-8", errors="ignore")
    pending.dump_snapshot(paths.pending_dir(cfg), event.get("session_id", "default"), content)
    return {}


def main() -> None:
    event = json.load(sys.stdin)
    print(json.dumps(run(event)))


if __name__ == "__main__":
    main()
