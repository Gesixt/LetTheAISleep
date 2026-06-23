from __future__ import annotations

import argparse
import json
from pathlib import Path

from lts import anchor, paths, pending, status, stm, transcript
from lts.config import load_config


def _cfg(root: str | None):
    return load_config(Path(root) if root else None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stm = sub.add_parser("stm")
    stm_sub = p_stm.add_subparsers(dest="op", required=True)
    for op in ("append", "read", "clear"):
        sp = stm_sub.add_parser(op)
        sp.add_argument("--root", default=None)
        if op == "append":
            sp.add_argument("--text", required=True)

    p_anchor = sub.add_parser("anchor")
    anchor_sub = p_anchor.add_subparsers(dest="op", required=True)
    sp = anchor_sub.add_parser("render")
    sp.add_argument("--root", default=None)

    p_pending = sub.add_parser("pending")
    pending_sub = p_pending.add_subparsers(dest="op", required=True)
    for op in ("dump", "has"):
        sp = pending_sub.add_parser(op)
        sp.add_argument("--root", default=None)
        if op == "dump":
            sp.add_argument("--session", required=True)
            sp.add_argument("--text", required=True)

    p_pressure = sub.add_parser("pressure")
    p_pressure.add_argument("--root", default=None)
    p_pressure.add_argument("--transcript", required=True)

    p_status = sub.add_parser("status")
    p_status.add_argument("--root", default=None)
    p_status.add_argument("--transcript", default=None)
    p_status.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    root = getattr(args, "root", None)
    cfg = _cfg(root)

    if args.cmd == "stm":
        f = paths.stm_file(cfg)
        if args.op == "append":
            stm.append(f, args.text)
            print("ok")
        elif args.op == "read":
            print(stm.read(f), end="")
        elif args.op == "clear":
            stm.clear(f)
    elif args.cmd == "anchor":
        print(anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))))
    elif args.cmd == "pending":
        d = paths.pending_dir(cfg)
        if args.op == "dump":
            print(pending.dump_snapshot(d, args.session, args.text))
        elif args.op == "has":
            print("yes" if pending.has_pending(d) else "no")
    elif args.cmd == "pressure":
        tokens = transcript.estimate_tokens(Path(args.transcript))
        print(transcript.pressure_level(
            tokens, transcript.DEFAULT_WINDOW, cfg.pressure_warn, cfg.pressure_force
        ))
    elif args.cmd == "status":
        tp = Path(args.transcript) if args.transcript else None
        metrics = status.collect(cfg, transcript_path=tp)
        if args.json:
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
        else:
            print(status.render(metrics))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
