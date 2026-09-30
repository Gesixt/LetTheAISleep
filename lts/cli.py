from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from lts import (anchor, digest, health, healthchecks, journal, memorymap, naming, paths,
                 pending, status, stm, sync, transcript, watermark)
from lts.config import NotAnLtsProject, load_config, require_project
from lts.sync import NotASource


def _cfg(root: str | None):
    return load_config(Path(root) if root else None)


def _add_root(sp) -> None:
    sp.add_argument("--root", default=None)


def _unusable_transcript(value: str) -> str | None:
    """Why `value` cannot be read as a transcript, or None when it can.

    The value is quoted in every message: a whitespace argument otherwise prints as an empty tail
    and the reader cannot see what was wrong with it.
    """
    # An empty or blank value is a bad path, not an absent flag. It is what an unset shell
    # variable expands to — and `Path("")` is `.`, so letting it through reached the filesystem
    # as the current directory: `pressure` raised IsADirectoryError, `status` silently dropped
    # its context line.
    if not value.strip():
        return f"transcript path is empty: {value!r}"
    path = Path(value)
    if not path.exists():
        return f"transcript not found: {str(path)!r}"
    if not path.is_file():
        return f"transcript is not a file: {str(path)!r}"
    if not os.access(path, os.R_OK):
        return f"transcript is not readable: {str(path)!r}"
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
        help="transcript path, enabling the capture and pressure checks (the SessionStart hook "
             "omits it: one parse of a large transcript measured ~1.5 s, and the two these "
             "checks need would consume most of the session-load budget on every run)",
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

    # One rule for every command that takes `--transcript`, read once here rather than per
    # command: downstream, an unreadable transcript is not an absent measurement but a green one.
    # `transcript.context_tokens` returns 0 for a file that is not there, so `lts pressure` prints
    # "none", `status`'s pressure block reads 0%, and `doctor`'s `pressure` check lands on `ok` —
    # each a confident answer from a read that never happened. All three take the path from a
    # human at a terminal, where a typo is the ordinary case; nothing shells out to them.
    # Only `None` — the flag omitted — skips the guard. An omitted flag asks a narrower question,
    # and the commands that allow it already say which measurements they did not make; an empty
    # string is a path that was given and is unusable, and `_unusable_transcript` says so.
    given = getattr(args, "transcript", None)
    unusable = _unusable_transcript(given) if given is not None else None
    if unusable:
        print(f"lts: {unusable}", file=sys.stderr)
        return 2

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
        # Must match what the hook reports. This used to measure `len(file) // 4` against a
        # hardcoded 200,000-token window: a transcript is append-only across `--resume`, so its
        # size is the whole history of the project. On ppss that estimate was 45,413,420 tokens —
        # 22,707% of that window, computed from the recorded count — while the live session held
        # 301,343 tokens of its configured 1,000,000. (The "9900%" this branch quoted alongside
        # the same token count follows from no window; `_check_pressure` says what reconciles.)
        # A transcript with no `usage` record yet is a real state — a session before its first
        # assistant message — and there the size estimate is all there is, so the fallback stays.
        # What it must not do is read like a measurement: the level leads the line, and the line
        # says when the figure behind it was estimated. Same wording as `lts status`.
        tokens, source = transcript.measure_context(Path(args.transcript))
        level = transcript.pressure_level(
            tokens, cfg.context_window, cfg.pressure_warn, cfg.pressure_force
        )
        print(level if source == transcript.USAGE else f"{level}  ({status.ESTIMATED})")
    elif args.cmd == "status":
        tp = Path(args.transcript) if args.transcript else None
        metrics = status.collect(cfg, transcript_path=tp)
        if args.json:
            print(json.dumps(metrics, ensure_ascii=False, indent=2))
        else:
            print(status.render(metrics))
    elif args.cmd == "doctor":
        # The journal is read here for the same reason the hook reads it: `anchor_delivery` and
        # `capture_progress` are the two checks with no filesystem evidence to work from, so
        # without history they skip — and they skipped on every human-run `doctor`, in the one
        # command whose job is to report health.
        checks = health.run(
            cfg,
            transcript_path=Path(args.transcript) if args.transcript else None,
            history=journal.tail(paths.health_journal_file(cfg), healthchecks._TREND_WINDOW)
            if cfg.configured else None,
        )
        if args.json:
            print(json.dumps(
                {
                    "worst": health.worst(checks),
                    "checks": [
                        # No `default=` encoder: `Check`'s four fields are `str | None` by
                        # construction, so a field that is not one should raise here rather than
                        # be stringified into a shape the caller cannot parse back.
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