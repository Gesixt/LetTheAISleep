from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import paths, stm, transcript
from lts.config import load_config
from lts.hooklog import log_error

# Cap per captured message so the STM buffer stays a working set, not a transcript clone.
_MAX_CHARS = 1000


def _cursor_file(cfg) -> Path:
    # Tracks how many exchanges were already captured (avoids dup/missed turns).
    # Named to avoid confusion with the Cursor editor's `.cursor/` convention.
    return paths.sidecar_root(cfg) / "stm" / ".lts-capture-offset"


def capture(event: dict, *, root: Path | None = None) -> int:
    """Automatically append any new user/assistant exchanges to the STM buffer.

    Deterministic (hook-driven): no model decision. A cursor file tracks how many
    exchanges were already captured so turns are never duplicated or missed.
    Returns the number of newly captured exchanges.
    """
    cfg = load_config(root or event.get("cwd"))
    paths.ensure_sidecar(cfg)
    exchanges = transcript.read_exchanges(Path(event.get("transcript_path", "")))

    cursor = _cursor_file(cfg)
    seen = 0
    if cursor.exists():
        try:
            seen = int(cursor.read_text(encoding="utf-8").strip() or "0")
        except ValueError:
            seen = 0

    new = exchanges[seen:]
    buf = paths.stm_file(cfg)
    for ex in new:
        text = " ".join(ex["text"].split())  # flatten to one line per exchange
        if len(text) > _MAX_CHARS:
            text = text[:_MAX_CHARS].rstrip() + " …[truncated]"
        stm.append(buf, f"[{ex['role']}] {text}")
    cursor.write_text(str(len(exchanges)), encoding="utf-8")
    return len(new)


def main() -> None:
    raw = sys.stdin.read()
    # TEMP diagnostic: prove the hook is invoked and show the payload (cwd etc.).
    try:
        with open("/tmp/lts-hook-trace.log", "a", encoding="utf-8") as fh:
            fh.write("stop fired: " + raw[:600].replace("\n", " ") + "\n")
    except Exception:
        pass
    try:
        event = json.loads(raw) if raw.strip() else {}
        capture(event)
    except Exception:
        log_error("stop.py")
    print("{}")


if __name__ == "__main__":
    main()