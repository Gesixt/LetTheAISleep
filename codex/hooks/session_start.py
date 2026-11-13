from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from lts.hookimpl import session_start as _impl
# Re-export helpers for existing importers; the implementation remains shared.
globals().update({k: v for k, v in vars(_impl).items() if not k.startswith("__")})

def main() -> None:
    client = clients.from_argv(sys.argv[1:] + ["--client", "codex"])
    try:
        event = json.load(sys.stdin)
        result = run(event, client=client)
    except Exception:
        log_error(__file__)
        result = {}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
