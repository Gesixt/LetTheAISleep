"""Check whether memory is working, and whether what it says about itself is true.

Three failures have been found in this project, and every one was found by accident after running
for a long time: `SessionStart` withheld the anchor for ~2 months, `lts pressure` reported 9900%,
and the model stated a context percentage that no component had produced. A liveness poll — is the
hook wired, does the index answer — would have caught **none** of them: in all three cases every
component was alive and the *claims* were false.

So the checks come in two kinds, kept distinct on purpose. **Inventory** asks whether each part is
present and reachable; it is cheap, and it catches the outage class that has not happened yet but is
one `mv` away, since every project points at `~/tools/LetTheAISleep` by absolute path. **Invariants**
ask whether the system's own statements hold. A third kind, **trend**, is not a separate mechanism:
it is an ordinary check that was handed the last few journal records as an input.

A failure produces a *demand*, not a line of information. The memory map exists because quiet
information does not change behaviour — the anchor was available and never used, `/recall` was
documented and never called. A health report that is merely present would be ignored the same way.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lts import anchor, doctor, paths, status, sync, watermark
from lts.config import Config, is_lts_config

# Worst first. `skip` outranks `ok` because a check that did not run is not a check that passed.
_ORDER = ("fail", "warn", "skip", "ok")
_SYMBOL = {"fail": "✗", "warn": "!", "skip": "·", "ok": "✓"}

_IDS = (
    "config", "sidecars", "hooks", "vault",
    "marks", "capture", "pressure", "anchor_fresh",
    "anchor_delivery", "capture_progress",
)

_QUOTED = re.compile(r'"([^"]+)"')

# Clock skew we forgive before calling a timestamp impossible.
_SKEW = timedelta(seconds=60)
# How far the transcript may run past the capture mark before the Stop hook looks dead. One
# exchange behind is the measured normal (the turn's last message is not flushed when Stop reads
# the file), so both a count and a time threshold must be exceeded.
_CAPTURE_TOLERANCE = timedelta(minutes=10)
# The anchor's `updated` is minute-granular local time, so a sleep can look up to a minute older
# than the note it just wrote.
_ANCHOR_SKEW = timedelta(minutes=2)

_DEMAND_HEAD = (
    "## Memory health: {n} check{s} FAILED\n"
    "Memory may be lying to you. Tell the user before doing anything else, and do not\n"
    "treat memory as intact until this is resolved."
)


@dataclass(frozen=True)
class Check:
    id: str
    level: str          # "ok" | "warn" | "fail" | "skip"
    message: str
    fix: str | None = None


def worst(checks: list[Check]) -> str:
    """The most serious level present, or "ok" when there is nothing to report."""
    for level in _ORDER:
        if any(c.level == level for c in checks):
            return level
    return "ok"


def render(checks: list[Check]) -> str:
    rank = {level: i for i, level in enumerate(_ORDER)}
    lines = ["Memory health"]
    for check in sorted(checks, key=lambda c: rank.get(c.level, len(_ORDER))):
        lines.append(f"  {_SYMBOL.get(check.level, '?')} {check.id}: {check.message}")
        if check.level != "ok" and check.fix:
            lines.append(f"      Fix: {check.fix}")
    return "\n".join(lines)


def demand(checks: list[Check]) -> str:
    """The block a hook injects when memory is broken, or "" when nothing failed.

    Only `fail` reaches here. A warning that interrupts work gets trained away, and the failures
    are trained away with it.
    """
    failed = [c for c in checks if c.level == "fail"]
    if not failed:
        return ""
    lines = [_DEMAND_HEAD.format(n=len(failed), s="" if len(failed) == 1 else "s")]
    for check in failed:
        lines.append(f"- {check.id}: {check.message}")
        if check.fix:
            lines.append(f"  Fix: {check.fix}")
    return "\n".join(lines)


# --- inventory -------------------------------------------------------------------------------


def _check_config(cfg: Config) -> Check:
    if cfg.configured:
        return Check("config", "ok", f"project '{cfg.project}' at {cfg.project_root}")
    return Check(
        "config", "fail",
        f"no config.toml with a [vault] section at or above {cfg.project_root}",
        "run `install.py --target <project>`, or restore a moved config.toml",
    )


def _check_sidecars(cfg: Config) -> Check:
    strays = [
        sidecar for sidecar in doctor.find_sidecars(cfg.project_root)
        if not is_lts_config(sidecar.parent / "config.toml")
    ]
    if not strays:
        return Check("sidecars", "ok", "no stray sidecars")
    return Check(
        "sidecars", "fail",
        "orphaned memory that /sleep will never read: " + ", ".join(str(p) for p in strays),
        "merge anything you need into the root buffer, then delete them",
    )


def _tokens(command: str) -> list[str]:
    """Every token of a hook command, quoted groups unwrapped and bare words kept."""
    try:
        return shlex.split(command or "")
    except ValueError:  # unbalanced quotes: take the quoted groups, then the words around them
        text = command or ""
        return _QUOTED.findall(text) + _QUOTED.sub(" ", text).split()


def _script_path(command: str) -> Path | None:
    """The `.py` file a hook command runs, or None when the command names none.

    `sync` writes `python3 "<abs path>"`, but a wiring someone edited by hand can put the script
    anywhere on the line — behind a quoted interpreter, or ahead of a flag — so every token is a
    candidate. Reading only the first quoted group or only the last word returned None for those,
    and a None was dropped: the hook could be dead and the check still said everything was fine.
    """
    for token in _tokens(command):
        if token.endswith(".py"):
            return Path(token)
    return None


def _check_hooks(cfg: Config) -> Check:
    settings = cfg.project_root / ".claude" / "settings.json"
    if not settings.exists():
        return Check("hooks", "fail", f"no {settings}",
                     "run `lts update` to write the hook wiring")
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return Check("hooks", "fail", f"{settings} is unreadable: {exc}",
                     "fix or delete it, then run `lts update`")

    wired: dict[str, list[str]] = {}
    for event, groups in (data.get("hooks") or {}).items():
        for group in groups or []:
            for hook in group.get("hooks") or []:
                wired.setdefault(event, []).append(hook.get("command", ""))

    missing = [event for event in sync.HOOK_EVENTS if event not in wired]
    gone: list[str] = []
    wrong: list[str] = []
    unreadable: list[str] = []
    for event, commands in wired.items():
        expected = sync.HOOK_EVENTS.get(event)
        if expected is None:
            continue
        for command in commands:
            script = _script_path(command)
            if script is None:
                unreadable.append(f"{event} -> {command}")
            elif script.name != expected:
                wrong.append(f"{event} -> {script.name}, expected {expected}")
            elif not script.exists():
                gone.append(f"{event} -> {script}")

    parts = []
    if missing:
        parts.append("not wired: " + ", ".join(missing))
    if gone:
        parts.append("script missing: " + "; ".join(sorted(gone)))
    if wrong:
        parts.append("wrong script: " + "; ".join(sorted(wrong)))
    if unreadable:
        parts.append("no script to check in: " + "; ".join(sorted(unreadable)))
    if missing or gone or wrong:
        return Check(
            "hooks", "fail", "; ".join(parts),
            "`git -C ~/tools/LetTheAISleep pull` then `lts update`; re-run install.py if the "
            "clone moved — every project points at it by absolute path",
        )
    if unreadable:
        # Not `ok`: the hook may be dead. Not `fail`: a wrapper command is conceivable wiring.
        # `skip` is this module's word for state that could not be verified.
        return Check(
            "hooks", "skip", "; ".join(parts),
            "wire the event at its hook script with `lts update`, or verify the wrapper by hand",
        )
    return Check("hooks", "ok", f"all {len(sync.HOOK_EVENTS)} events wired, scripts present")


def _check_vault(cfg: Config) -> Check:
    vault = paths.vault_root(cfg)
    if not vault.is_dir():
        return Check("vault", "fail", f"vault directory missing: {vault}",
                     "create it, or fix `[vault] path` in config.toml")
    notes = list(vault.rglob("*.md"))
    if not notes:
        return Check("vault", "warn", f"vault is empty: {vault}",
                     "expected for a new project; /sleep writes the first notes")
    return Check("vault", "ok", f"{len(notes)} notes at {vault}")


# --- invariants -----------------------------------------------------------------------------


def _utc(when: datetime | None) -> datetime | None:
    """`when` as timezone-aware UTC. A naive timestamp is read as UTC, not as local time.

    Every comparison in this module mixes a caller's `now` with a mark parsed out of a file, and
    one naive operand raises rather than answering — the check would then not run at all.
    """
    if when is None:
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when.astimezone(timezone.utc)


def _stamp_time(stamp: object) -> datetime | None:
    """A Claude Code ISO-8601 `...Z` timestamp as UTC, or None when it is absent or unparseable."""
    if not stamp:
        return None
    try:
        return _utc(datetime.fromisoformat(str(stamp).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _check_marks(cfg: Config, now: datetime) -> Check:
    """The two marks must tell a consistent story about what has been read and consolidated."""
    capture = _stamp_time(watermark.read_mark(paths.capture_mark_file(cfg)).get("timestamp"))
    sleep = _stamp_time(watermark.read_mark(paths.sleep_mark_file(cfg)).get("timestamp"))
    if capture is None and sleep is None:
        return Check("marks", "skip", "no marks yet — nothing has been captured or consolidated")
    for name, when in (("capture", capture), ("sleep", sleep)):
        if when is not None and when > now + _SKEW:
            return Check("marks", "fail", f"the {name} mark is in the future: {when.isoformat()}",
                         "check the system clock; the next Stop hook will then correct it")
    # Equal marks are normal: a sleep writes both at one instant.
    if capture is not None and sleep is not None and sleep > capture:
        return Check(
            "marks", "fail",
            f"the sleep mark ({sleep.isoformat()}) is ahead of the capture mark "
            f"({capture.isoformat()}) — consolidation claims material the capture never read",
            "run /sleep to re-consolidate this material before it is dropped",
        )
    return Check("marks", "ok", "marks consistent")


def _check_capture(cfg: Config, transcript_path: Path | None, now: datetime) -> Check:
    """Is the transcript running away from the capture mark?

    Exactly one exchange behind is the measured normal — the Stop hook reads the transcript before
    Claude Code has flushed the turn's last message, and `mark_at` deliberately covers only what
    was there. So a count threshold *and* a time threshold must both be exceeded; either alone
    would cry wolf on every ordinary turn.
    """
    if transcript_path is None:
        return Check("capture", "skip",
                     "no transcript given — run `lts doctor --transcript <path>` to check this")
    path = Path(transcript_path)
    if not path.exists():
        return Check("capture", "skip", f"transcript not found: {path}")
    mark = watermark.read_mark(paths.capture_mark_file(cfg))
    backlog = watermark.entries_after(path, mark)
    if len(backlog) <= 1:
        return Check("capture", "ok",
                     f"{len(backlog)} exchange(s) behind the capture mark — the normal flush lag")
    marked = _stamp_time(mark.get("timestamp"))
    newest = _stamp_time(backlog[-1].get("timestamp"))
    if marked is not None and newest is not None and newest - marked > _CAPTURE_TOLERANCE:
        minutes = int((newest - marked).total_seconds() // 60)
        return Check(
            "capture", "fail",
            f"{len(backlog)} exchanges behind the capture mark, the newest {minutes} min past it "
            "— the Stop hook is not capturing",
            "check /tmp/lts-hook-errors.log, and the hooks check above",
        )
    tolerance = int(_CAPTURE_TOLERANCE.total_seconds() // 60)
    return Check("capture", "ok",
                 f"{len(backlog)} exchanges behind the capture mark, within {tolerance} min")


def _check_pressure(metrics: dict | None) -> Check:
    """Measured tokens must fit the window they are measured against.

    `lts pressure` once reported 9900% on a real project: 45,413,420 tokens against a
    1,000,000-token window, a character estimate divided by a hardcoded window. A percentage
    above 100 is never real, so it is an assertion on the output rather than a bug hunt.
    """
    measured = (metrics or {}).get("pressure")
    if not measured:
        return Check("pressure", "skip",
                     "no transcript given — run `lts doctor --transcript <path>` to check this")
    tokens = int(measured.get("tokens") or 0)
    window = int(measured.get("window") or 0)
    if window <= 0:
        return Check("pressure", "fail", f"the context window is {window}",
                     "set `[context] window` in config.toml")
    if tokens > window:
        return Check(
            "pressure", "fail",
            f"measured {tokens:,} tokens against a {window:,}-token window "
            f"({round(tokens / window * 100)}%)",
            "a percentage above 100 is never real — either the measurement or "
            "`[context] window` is wrong",
        )
    return Check("pressure", "ok", f"{tokens:,}/{window:,} tokens ({round(tokens / window * 100)}%)")


def _check_anchor_fresh(cfg: Config) -> Check:
    """The anchor is the entry point a new session reads; it must not lag the notes it points at."""
    entry = anchor.read_anchor(paths.anchor_file(cfg))
    sessions_dir = paths.vault_root(cfg) / "session-memory"
    # rglob, not glob: team mode nests notes under session-memory/<author>/.
    sessions = list(sessions_dir.rglob("*.md")) if sessions_dir.is_dir() else []
    if not entry:
        if sessions:
            return Check("anchor_fresh", "fail",
                         f"no anchor, but {len(sessions)} session note(s) exist",
                         "run `lts anchor write ...` — /sleep step 6 does this")
        return Check("anchor_fresh", "skip", "no anchor and no session notes yet")
    if not sessions:
        return Check("anchor_fresh", "ok", "anchor present; no session notes to compare against")
    newest = max(sessions, key=lambda p: p.stat().st_mtime)
    # Both sides are naive local times: `updated` is minute-granular local, the mtime is local.
    note_at = datetime.fromtimestamp(newest.stat().st_mtime)
    try:
        anchor_at = datetime.strptime(str(entry.get("updated", "")), "%Y-%m-%d %H:%M")
    except ValueError:
        return Check("anchor_fresh", "warn",
                     f"the anchor has no readable `updated`: {entry.get('updated')!r}",
                     "rewrite it with `lts anchor write`")
    if anchor_at + _ANCHOR_SKEW < note_at:
        return Check(
            "anchor_fresh", "warn",
            f"anchor updated {entry['updated']}, but {newest.name} is newer — the entry points "
            "are a session behind",
            "a /sleep wrote a note without updating the anchor; run `lts anchor write`",
        )
    return Check("anchor_fresh", "ok", f"anchor updated {entry['updated']}")


def run(
    cfg: Config,
    *,
    transcript_path: Path | None = None,
    metrics: dict | None = None,
    now: datetime | None = None,
) -> list[Check]:
    """Every check, in `_IDS` order.

    `metrics` is a `status.collect` result. It is taken as an argument rather than recomputed so
    the transcript is parsed at most once per run, and so `status.collect` stays the single place
    that counts memory load.

    An unconfigured root short-circuits: without a project there is nothing to check, and saying
    `ok` about a check that never ran is the exact failure this module exists to prevent.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    config_check = _check_config(cfg)
    if not cfg.configured:
        return [config_check] + [
            Check(check_id, "skip", "no lts project here") for check_id in _IDS[1:]
        ]
    if metrics is None:
        metrics = status.collect(cfg, transcript_path=transcript_path)
    return [
        config_check,
        _check_sidecars(cfg),
        _check_hooks(cfg),
        _check_vault(cfg),
        _check_marks(cfg, now),
        _check_capture(cfg, transcript_path, now),
        _check_pressure(metrics),
        _check_anchor_fresh(cfg),
    ]
