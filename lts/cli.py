from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from lts import (anchor, digest, health, memorymap, naming, paths, pending, status,
                 stm, sync, transcript, watermark)
from lts.config import NotAnLtsProject, load_config, require_project
from lts.sync import NotASource


def _cfg(root: str | None):
    return load_config(Path(root) if root else None)


def _add_root(sp) -> None:
    sp.add_argument("--root", default=None)


def _unusable_transcript(path: Path) -> str | None:
    """Why `path` cannot be read as a transcript, or None when it can."""
    if not path.exists():
        return f"transcript not found: {path}"
    if not path.is_file():
        return f"transcript is not a file: {path}"
    if not os.access(path, os.R_OK):
        return f"transcript is not readable: {path}"
    return None


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
    p_doctor.add_argument("--json", action="store_true")
    p_doctor.add_argument(
        "--transcript",
        help="transcript path, enabling the capture and pressure checks (the planned hook "
             "caller will omit it: parsing a months-long transcript is too slow for the "
             "critical path)",
    )
    _add_root(p_doctor)

    p_digest = sub.add_parser("digest")
    _add_root(p_digest)
    p_digest.add_argument("--since", default=None)
    p_digest.add_argument("--json", action="store_true")

    p_update = sub.add_parser("update")
    p_update.add_argument("--target", default=None, help="project to refresh (default: cwd)")
    p_update.add_argument("--source", default=None,
                          help="clone to copy from (default: the one this `lts` runs from)")
    p_update.add_argument("--check", action="store_true",
                          help="report what is stale without writing; exit 1 if anything is")
    p_update.add_argument("--json", action="store_true")

    p_map = sub.add_parser("memory-map")
    _add_root(p_map)

    p_session = sub.add_parser("session-name")
    _add_root(p_session)
    p_session.add_argument("--at", default=None, help='timestamp "YYYY-MM-DD HH:MM" (default: now)')
    p_session.add_argument("--json", action="store_true")

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
            # Step 6 of /sleep runs mid-turn: tell the hooks that everything up to now is
            # consolidated, or the Stop hook refills the buffer with the sleep's own
            # narration and the following /compact snapshots a chapter already in notes.
            paths.ensure_sidecar(cfg)
            watermark.arm(paths.sleep_flag_file(cfg))
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
        # Must match what the hook reports. This used to measure `len(file) / 4` against a
        # hardcoded 200k window: a transcript is append-only across `--resume`, so its size is
        # the whole history of the project. On a real one that read 45,413,420 tokens (9900%)
        # while the session was at 28%.
        tokens = transcript.context_tokens(Path(args.transcript))
        print(transcript.pressure_level(
            tokens, cfg.context_window, cfg.pressure_warn, cfg.pressure_force
        ))
    elif args.cmd == "status":
        tp = Path(args.transcript) if args.transcript else None
        metrics = status.collect(cfg, transcript_path=tp)
        if args.json:
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
        else:
            print(status.render(metrics))
    elif args.cmd == "doctor":
        # Refuse rather than report without it: an absent transcript is not an absent measurement
        # downstream but a green one — `context_tokens` returns 0 for a file that is not there and
        # `pressure` prints "0 tokens (0%)" — so a typo would buy a clean bill of health, which is
        # the one outcome this subsystem exists to prevent.
        unusable = args.transcript and _unusable_transcript(Path(args.transcript))
        if unusable:
            print(f"lts: {unusable}", file=sys.stderr)
            return 2
        checks = health.run(
            cfg, transcript_path=Path(args.transcript) if args.transcript else None
        )
        if args.json:
            print(json.dumps(
                {
                    "worst": health.worst(checks),
                    "checks": [
                        {"id": c.id, "level": c.level, "message": c.message, "fix": c.fix}
                        for c in checks
                    ],
                },
                ensure_ascii=False, indent=2,
            ))
        else:
            print(health.render(checks))
        return 1 if health.worst(checks) == "fail" else 0
    elif args.cmd == "digest":
        report = digest.collect(cfg, since=args.since)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            text = digest.render(report)
            if text:
                print(text)
    elif args.cmd == "update":
        # `--target` names the project, not the sidecar root: load_config walks up from it the
        # same way every other command does, so running this from a subdirectory still works.
        target_cfg = _cfg(args.target)
        report = sync.sync(sync.source_root(args.source), target_cfg, check=args.check)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(sync.render(report))
        return 1 if (args.check and report["stale"]) else 0
    elif args.cmd == "memory-map":
        print(memorymap.render(cfg))
    elif args.cmd == "session-name":
        when = datetime.strptime(args.at, "%Y-%m-%d %H:%M") if args.at else None
        title = naming.session_note_name(cfg, when)
        if args.json:
            print(json.dumps({"title": title, "directory": naming.session_directory(cfg)}))
        else:
            print(title)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return _run(args)
    except (NotAnLtsProject, NotASource) as exc:
        print(f"lts: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())