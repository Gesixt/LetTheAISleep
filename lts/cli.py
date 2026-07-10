from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from lts import anchor, doctor, paths, pending, status, stm, transcript
from lts.config import NotAnLtsProject, load_config, require_project


def _cfg(root: str | None):
    return load_config(Path(root) if root else None)


def _add_root(sp) -> None:
    sp.add_argument("--root", default=None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lts")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_stm = sub.add_parser("stm")
    stm_sub = p_stm.add_subparsers(dest="op", required=True)
    for op in ("append", "read", "clear"):
        sp = stm_sub.add_parser(op)
        _add_root(sp)
        if op == "append":
            sp.add_argument("--text", required=True)

    p_anchor = sub.add_parser("anchor")
    anchor_sub = p_anchor.add_subparsers(dest="op", required=True)
    _add_root(anchor_sub.add_parser("render"))
    sp = anchor_sub.add_parser("write")
    _add_root(sp)
    sp.add_argument("--last-session", required=True)
    sp.add_argument("--topic", action="append", default=[])
    sp.add_argument("--note", action="append", default=[])
    sp.add_argument("--next-task", default=None)
    sp.add_argument("--updated", default=None)

    p_pending = sub.add_parser("pending")
    pending_sub = p_pending.add_subparsers(dest="op", required=True)
    for op in ("dump", "has", "list", "clear"):
        sp = pending_sub.add_parser(op)
        _add_root(sp)
        if op == "dump":
            sp.add_argument("--session", required=True)
            sp.add_argument("--text", required=True)

    p_pressure = sub.add_parser("pressure")
    _add_root(p_pressure)
    p_pressure.add_argument("--transcript", required=True)

    p_status = sub.add_parser("status")
    _add_root(p_status)
    p_status.add_argument("--transcript", default=None)
    p_status.add_argument("--json", action="store_true")

    p_doctor = sub.add_parser("doctor")
    _add_root(p_doctor)
    p_doctor.add_argument("--json", action="store_true")

    return parser


def _run(args) -> int:
    cfg = _cfg(getattr(args, "root", None))

    if args.cmd == "stm":
        if args.op in ("append", "clear"):
            require_project(cfg)
        f = paths.stm_file(cfg)
        if args.op == "append":
            stm.append(f, args.text)
            print("ok")
        elif args.op == "read":
            print(stm.read(f), end="")
        elif args.op == "clear":
            stm.clear(f)
    elif args.cmd == "anchor":
        if args.op == "render":
            print(anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))))
        elif args.op == "write":
            require_project(cfg)
            paths.ensure_sidecar(cfg)
            anchor.write_anchor(
                paths.anchor_file(cfg),
                updated=args.updated or datetime.now().strftime("%Y-%m-%d %H:%M"),
                last_session=args.last_session,
                active_topics=args.topic,
                active_notes=args.note,
                next_task=args.next_task,
            )
            print(paths.anchor_file(cfg))
    elif args.cmd == "pending":
        if args.op in ("dump", "clear"):
            require_project(cfg)
        d = paths.pending_dir(cfg)
        if args.op == "dump":
            print(pending.dump_snapshot(d, args.session, args.text))
        elif args.op == "has":
            print("yes" if pending.has_pending(d) else "no")
        elif args.op == "list":
            for snap in pending.list_snapshots(d):
                print(snap)
        elif args.op == "clear":
            pending.clear_all(d)
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
    elif args.cmd == "doctor":
        report = doctor.collect(cfg)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        else:
            print(doctor.render(report))
        return 0 if report["ok"] else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except NotAnLtsProject as exc:
        print(f"lts: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())