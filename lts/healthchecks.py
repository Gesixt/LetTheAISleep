"""The checks themselves: one function per check, and the helpers each of them owns.

The checks come in two kinds, kept distinct on purpose. **Inventory** asks whether each part is
present and reachable; it is cheap, and it catches the outage class that has not happened yet but is
one `mv` away, since every project points at `~/tools/LetTheAISleep` by absolute path. **Invariants**
ask whether the system's own statements hold. A third kind, **trend**, is not a separate mechanism:
it is an ordinary check that was handed the last few journal records as an input.

One rule governs them all, and it is the reason the comments below are as long as they are: **a
check that quietly did not run must never be reported as passing**, and no message may assert more
than was measured. Review found three separate instances of that defect here, each in code written
to prevent it — `hooks` printed "all 4 events wired, scripts present" when a hook command's parser
returned None and the None was dropped; `capture` printed "within 10 min" with nothing to compare
against; `marks` printed "marks consistent" with one of the two marks unread. So every comparison is
classified before it is made, and a state that cannot be measured is `skip`, never `ok`.

`lts.health` builds the report out of these and renders it; see its docstring for why the subsystem
exists at all.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lts import anchor, doctor, naming, paths, sync, transcript, watermark
from lts.config import Config, is_lts_config

_QUOTED = re.compile(r'"([^"]+)"')

# Clock skew we forgive before calling a timestamp impossible.
_SKEW = timedelta(seconds=60)
# How far the transcript may run past the capture mark before the Stop hook looks dead. One
# exchange behind is the measured normal (the turn's last message is not flushed when Stop reads
# the file), so both a count and a time threshold must be exceeded.
_CAPTURE_TOLERANCE = timedelta(minutes=10)
# How many consecutive prompts may pass with the capture mark unmoved *while the transcript holds
# something newer than it* before the `Stop` hook is called dead. One is not evidence: a turn the user
# interrupts runs no `Stop` hook at all, and that is a thing people do on purpose. Two in a row is.
# The two states where a working hook legitimately leaves the mark alone — an unreadable transcript,
# and a turn that produced no exchange (`stop.py:65-75`) — are excluded by the "something newer"
# half of the question rather than by this tolerance.
_CAPTURE_MISS_TOLERANCE = 2
# The anchor's `updated` is minute-granular local time, so a sleep can look up to a minute older
# than the note it just wrote.
_ANCHOR_SKEW = timedelta(minutes=2)
# How many journalled runs a trend looks at. Five compactions is days of work in a continuous
# session — long enough that a one-off is not a trend, short enough to notice within a day. Public,
# and re-exported by `lts.health`: the callers that must hand `history` to `all_checks` have to read
# exactly this many records, so this is part of the interface, not an internal.
TREND_WINDOW = 5
# The block name `_check_anchor_delivery` looks for in a journal record: a constant because the hook
# calling `record` decides the names it passes, and a typo there would fail the check for ever.
# `record`'s docstring spells the whole vocabulary for that caller. Public for the same reason as
# `TREND_WINDOW` — the `SessionStart` hook has to name this block, so the name is an interface.
ANCHOR_BLOCK = "anchor"
# The directories `doctor.find_sidecars` does not enter, spelled out for the one message that makes
# an absence claim: a vendored repository with its own `.ai_memory` is invisible to that walk, and
# "no stray sidecars" said nothing about the limit.
_PRUNED = "the pruned directories (" + ", ".join(sorted(doctor.SKIP_DIRS)) + ")"
# The date `naming.session_note_name` puts at the end of every session note's title. `_note_time`
# reads it instead of the note's mtime, which a `git pull` rewrites.
_SESSION_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}_\d{4})$")


@dataclass(frozen=True)
class Check:
    id: str
    level: str          # "ok" | "warn" | "fail" | "skip"
    message: str
    fix: str | None = None


# The checks cheap enough to run on every turn: everything that does not walk a directory tree.
#
# Measured 2026-10-01 through `health.run(cfg, ids=[one])`, minimum of five runs each, on the
# LetTheAISleep / ppss / nextcloud-development projects (all vaults on the same fuseblk mount, each
# given its largest real transcript — 13,399,354 / 208,988,417 / 89,022,622 B). The two excluded
# walks: `sidecars` 3.391 / 130.884 / 605.770 ms and `vault` 0.300 / 24.272 / 0.415 ms. Everything in
# the set below, worst column each: `config` 0.003, `hooks` 0.167, `marks` 0.316, `pressure` 0.004,
# `anchor_fresh` 1.121, `anchor_delivery` 0.005, `capture_progress` 0.006, `capture_live` 0.263 ms —
# so 1.121 ms is the ceiling of the whole set, against 605.770 ms for one check left out of it.
# (The spec's §3.1 table measured the same two walks at 3.703 / 131.526 / 599.686 and 0.294 / 23.426
# / 0.393 ms on the same day; it had no figure at all for `anchor_delivery`, whose probe raised, and
# the two above are this measurement's own.)
#
# `capture` is excluded for the same reason by a different route: it parses the whole transcript, and
# the same run measured it at 69.445 / 1463.639 / 643.384 ms. `capture_live` answers the part of its
# question that matters per turn from a bounded tail scan instead, which is what buys its 0.263 ms.
#
# The membership is a measurement, not a taste, and it is not configurable — a reliability guarantee
# an operator can quietly switch off is not one.
#
# `pressure` is in the set because the transcript stopped being expensive once the tail parse landed:
# measured in the same run, `status.collect` costs 0.546 / 0.373 / 0.451 ms with a transcript against
# 0.361 / 0.247 / 0.262 ms without one. The transcript adds 0.12-0.19 ms and the addition does not
# grow with the file — the 209 MB one is the cheapest of the three — which is the property the tail
# parse bought. The caller passes the result in as `metrics`, so that cost is paid once per turn and
# not once per check.
PER_TURN_IDS = (
    "config", "hooks", "marks", "pressure",
    "anchor_fresh", "anchor_delivery", "capture_progress", "capture_live",
)


def all_checks(
    cfg: Config,
    *,
    transcript_path: Path | None = None,
    metrics: dict | None = None,
    history: list[dict] | None = None,
    turn: dict | None = None,
    ids: Sequence[str] | None = None,
    now: datetime | None = None,
) -> list[Check]:
    """Every check this module can make about `cfg`, in the order the report states them.

    The one public entry point for making a check. The checks and the helpers they own stay private,
    so this module's whole surface is `Check`, this function, and the three constants its callers must
    name to use it: `TREND_WINDOW`, the number of journal records a trend needs, `ANCHOR_BLOCK`, the
    block name `_check_anchor_delivery` matches in those records, and `PER_TURN_IDS`, the subset a
    caller on the per-turn path passes as `ids`. All five are re-exported by `lts.health`, which is
    the module its consumers already import — `lts.cli` and the `SessionStart` hook used to reach in
    here for the two constants by their private names, which made that surface larger than the
    sentence above it admitted.

    `ids` selects a subset, and it does so before the checks run: see the comment on the registry
    below, which is the only reason the registry is a list of thunks rather than a list of results.
    `turn` is the state the per-turn hook left at the previous prompt (`lts.turnstate`), and
    `_check_capture_live` is the only check that reads it.

    `now` is normalised here because every check that compares anything mixes a caller's `now` with
    a mark parsed out of a file, and one naive operand raises rather than answering. The order is
    fixed here too: it is the order the rendered report and the journal record share.

    `history` arrives as a plain list of dicts, never a `lts.journal` handle: the trend checks live
    here now, and this module must not import `journal` — the journal is best-effort by design and
    a health check that could break on it would be a new way for memory to die silently.

    A configured project is assumed. Without one, the config verdict is the only thing that can be
    established — there is nothing for the other ten to measure — so that is all this returns, and
    `lts.health` says what the rest of the report reads like in that case.

    Every check is called through `_answered`, so one that raises costs its own answer and not the
    others.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    config_check = _answered("config", lambda: _check_config(cfg))
    if not cfg.configured:
        return [config_check]
    # `(id, thunk)` pairs rather than a list of results: `ids` must be able to leave a check out
    # *before* it runs. Filtering results afterwards would still pay for the walk — `sidecars`
    # measured 599.686 ms on nextcloud-development and `vault` 23.426 ms on ppss (2026-10-01) —
    # which is the whole reason a subset exists.
    registry: list[tuple[str, Callable[[], Check]]] = [
        ("sidecars", lambda: _check_sidecars(cfg)),
        ("hooks", lambda: _check_hooks(cfg)),
        ("vault", lambda: _check_vault(cfg)),
        ("marks", lambda: _check_marks(cfg, now)),
        ("capture", lambda: _check_capture(cfg, transcript_path, now)),
        ("pressure", lambda: _check_pressure(metrics)),
        ("anchor_fresh", lambda: _check_anchor_fresh(cfg)),
        ("anchor_delivery", lambda: _check_anchor_delivery(cfg, history)),
        ("capture_progress", lambda: _check_capture_progress(history)),
        ("capture_live", lambda: _check_capture_live(cfg, turn, transcript_path)),
    ]
    wanted = None if ids is None else set(ids)
    checks = [config_check] if wanted is None or "config" in wanted else []
    checks += [_answered(check_id, thunk) for check_id, thunk in registry
               if wanted is None or check_id in wanted]
    return checks


def _answered(check_id: str, check: Callable[[], Check]) -> Check:
    """`check()`'s verdict, or the `fail` that says why this check has none.

    Individual checks already guard the malformed inputs they know about, each after a raise was
    found in one of them: `_check_pressure` on a measurement that is not numbers, `_check_hooks` on
    a settings.json of the wrong shape, `_check_anchor_fresh` on a note that vanished mid-scan,
    `_readable` on a journal field of the wrong type. Each of those guards was written after the
    raise had already taken the whole report down, which is the argument for this one: a raise
    nobody has met yet costs every other check in the run the same way, and a report that does not
    appear is the worst possible outcome for the subsystem whose job is to say whether memory is
    lying. In `SessionStart` the loss is silent and permanent — the surrounding `try` keeps the anchor
    and the digest, so the only visible effect is that the health block stops appearing and no journal
    record is written, which then starves the trend checks of the history they need and disables
    them too.

    `fail`, not `skip`: this is not "could not measure", it is a component of the health subsystem
    that is broken, and it needs attention as much as anything it would have reported. The
    exception's own text is carried, because "internal error" names nothing anybody can act on.
    """
    try:
        return check()
    except Exception as exc:     # noqa: BLE001 - deliberate: the alternative is losing the rest
        return Check(
            check_id, "fail",
            f"this check itself raised {type(exc).__name__}: {exc} — its answer is missing from "
            "this report, and nothing here says the thing it checks is healthy",
            "this is a bug in the health check, not necessarily in what it measures; the other "
            "checks in this report are unaffected",
        )


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
    """Sidecars under the project root, split into strays and nested lts projects.

    Only the strays decide the level: a sidecar sitting next to its own config.toml belongs to a
    nested project and that project's own /sleep reads it, so it is legitimate. It is still named.
    `doctor.collect` used to print "! nested lts project root: ..." and this check inherited the
    discrimination without the report, which quietly dropped an operator-facing fact: a second
    buffer under the tree is worth knowing about even when it is nobody's fault.

    "No stray sidecars" is an absence claim, and an absence claim may reach no further than the
    walk that produced it. Two things bound that walk: directories it could not enter, which it now
    returns instead of swallowing, and the fixed list it prunes, which the `ok` message names. The
    first cannot be reported as an absence at all — a real stray under a directory with mode 000
    used to leave this check `ok` and `lts doctor` exiting 0.
    """
    found, unreadable = doctor.find_sidecars(cfg.project_root)
    nested, strays = [], []
    for sidecar in found:
        (nested if is_lts_config(sidecar.parent / "config.toml") else strays).append(sidecar)
    also = ""
    if nested:
        also = (" — nested lts project root(s), each read by its own /sleep: "
                + ", ".join(str(p) for p in nested))
    blocked = ""
    if unreadable:
        many = len(unreadable) > 1
        blocked = (f" — {len(unreadable)} {'directories' if many else 'directory'} could not be "
                   f"entered, so nothing under {'them' if many else 'it'} was examined: "
                   + ", ".join(str(p) for p in unreadable))
    if strays:
        return Check(
            "sidecars", "fail",
            "orphaned memory that /sleep will never read: "
            + ", ".join(str(p) for p in strays) + also + blocked,
            "merge anything you need into the root buffer, then delete them",
        )
    if unreadable:
        # Not `ok`: the claim this check would make is an absence, and part of the tree was not
        # searched. Not `fail`: an unreadable directory is not itself orphaned memory.
        return Check(
            "sidecars", "skip",
            "no stray sidecars in the part of the tree this walk could read" + also + blocked,
            "make those directories readable and re-run, or check them by hand",
        )
    return Check("sidecars", "ok", f"no stray sidecars outside {_PRUNED}" + also)


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

    # Shape, before anything is read out of it. Claude Code's wiring is
    # `{"hooks": {<event>: [{"hooks": [{"command": str}]}]}}`, and this file is hand-edited: any
    # other valid-JSON shape used to raise `AttributeError` out of this loop, which `all_checks`
    # then propagated, so a single misplaced brace cost all ten checks their answer and, in
    # `SessionStart`, removed the health block and the journal record silently and for ever. The
    # `json.JSONDecodeError` above is the same class of fault and has always been a `fail`; so is
    # this. `_answered` now catches what these guards miss, and neither replaces the other: a guard
    # can name the shape it found, which a caught exception cannot.
    if not isinstance(data, dict):
        return Check("hooks", "fail",
                     f"{settings} holds a JSON {type(data).__name__}, not an object",
                     "fix or delete it, then run `lts update`")
    events = data.get("hooks") or {}
    if not isinstance(events, dict):
        return Check(
            "hooks", "fail",
            f"the `hooks` section of {settings} is a {type(events).__name__}, not an object "
            f"mapping event names to hook groups: {events!r}",
            "fix or delete it, then run `lts update`",
        )

    wired: dict[str, list[str]] = {}
    malformed: list[str] = []
    # The events among `malformed`, tracked rather than parsed back out of those sentences: an
    # event name is an arbitrary JSON key, and a fact this check reports should not depend on one
    # not containing the separator.
    unreadable_events: set[str] = set()
    for event, groups in events.items():
        if not isinstance(groups, list):
            malformed.append(f"{event}: a {type(groups).__name__}, not a list of hook groups")
            unreadable_events.add(event)
            continue
        for group in groups:
            if not isinstance(group, dict):
                malformed.append(f"{event}: a hook group that is a {type(group).__name__}, "
                                 f"not an object: {group!r}")
                unreadable_events.add(event)
                continue
            hooks = group.get("hooks") or []
            if not isinstance(hooks, list):
                malformed.append(f"{event}: `hooks` is a {type(hooks).__name__}, not a list: "
                                 f"{hooks!r}")
                unreadable_events.add(event)
                continue
            for hook in hooks:
                if not isinstance(hook, dict):
                    malformed.append(f"{event}: a hook that is a {type(hook).__name__}, not an "
                                     f"object: {hook!r}")
                    unreadable_events.add(event)
                    continue
                command = hook.get("command", "")
                if not isinstance(command, str):
                    # `_tokens` calls `shlex.split`, which raises on anything but a string.
                    malformed.append(f"{event}: `command` is a {type(command).__name__}, not a "
                                     f"string: {command!r}")
                    unreadable_events.add(event)
                    continue
                wired.setdefault(event, []).append(command)

    # An event whose wiring could not be read is not an event that is "not wired": that sentence
    # would be a claim this check cannot make about it, and the shape is reported instead.
    missing = [event for event in sync.HOOK_EVENTS
               if event not in wired and event not in unreadable_events]
    gone: list[str] = []
    wrong: list[str] = []
    unreadable: list[str] = []
    empty: list[str] = []
    for event, commands in wired.items():
        expected = sync.HOOK_EVENTS.get(event)
        if expected is None:
            continue
        for command in commands:
            # An empty or whitespace `command` is separated from a command that merely cannot be
            # parsed, because the two are different verdicts. Nothing runs when the command is
            # blank: that event is dead, which is established rather than unmeasurable, so it is a
            # `fail`. It used to land in `unreadable` and therefore in `skip`, and `lts doctor`
            # exited 0 on a hook wired at nothing while `/memory-status` was told only that some
            # check had skipped. `skip` stays for the genuinely unmeasurable case below — a
            # wrapper or shell one-liner naming no script this check can find.
            if not str(command).strip():
                empty.append(f"{event} -> {command!r}")
                continue
            script = _script_path(command)
            if script is None:
                # Quoted, the way `cli._unusable_transcript` quotes a path it could not use: a
                # command shown bare rendered as "no script to check in: Stop -> ", a sentence
                # that stops at an arrow and shows the reader nothing.
                unreadable.append(f"{event} -> {command!r}")
            elif script.name != expected:
                wrong.append(f"{event} -> {script.name}, expected {expected}")
            # `is_file`, not `exists`: a directory at the script's path bought "all 4 events wired,
            # scripts present", and `python3 <a directory>` runs nothing. `cli.py` separates the
            # two for the same reason when it validates `--transcript`.
            elif not script.is_file():
                gone.append(f"{event} -> {script}")

    parts = []
    if missing:
        parts.append("not wired: " + ", ".join(missing))
    if empty:
        parts.append("wired at an empty command, so nothing runs: " + "; ".join(sorted(empty)))
    if gone:
        parts.append("no script file at: " + "; ".join(sorted(gone)))
    if wrong:
        parts.append("wrong script: " + "; ".join(sorted(wrong)))
    if malformed:
        parts.append("unexpected wiring shape: " + "; ".join(sorted(malformed)))
    if unreadable:
        parts.append("no script to check in: " + "; ".join(sorted(unreadable)))
    if missing or empty or gone or wrong or malformed:
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
    return Check("vault", "ok", f"{len(notes)} note{'' if len(notes) == 1 else 's'} at {vault}")


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


# What a mark file turned out to hold, established before anything is compared against it. Only
# `_MARK_READ` may take part in a comparison, and only a comparison may be reported as passing.
_MARK_MISSING = "missing"          # no file: nothing has ever been written here
_MARK_UNREADABLE = "unreadable"    # a file, but not a mark: truncated, or not JSON at all
_MARK_NO_STAMP = "no-stamp"        # a mark, but with no timestamp value in it
_MARK_BAD_STAMP = "bad-stamp"      # a timestamp that does not parse
_MARK_READ = "read"


def _classify_mark(path: Path) -> tuple[str, datetime | None, dict, object]:
    """`(state, when, mark, raw timestamp)` — what is actually in a mark file.

    `watermark.read_mark` answers `{}` both for a file that is absent and for one whose JSON is
    unreadable (`write_mark` is a plain, non-atomic write, so a hook killed mid-write leaves a
    partial file behind), and `mark_of` legitimately writes `timestamp: None` for an exchange that
    carried no stamp. Collapsed into a bare `None`, those states are indistinguishable from each
    other — and a `None` operand silently cancels the comparison it was meant to take part in,
    which was then reported as the comparison having passed. Hence: classify, then compare.
    """
    path = Path(path)
    if not path.exists():
        return _MARK_MISSING, None, {}, None
    mark = watermark.read_mark(path)
    if not mark:
        return _MARK_UNREADABLE, None, {}, None
    stamp = mark.get("timestamp")
    if not stamp:
        return _MARK_NO_STAMP, None, mark, stamp
    when = _stamp_time(stamp)
    if when is None:
        return _MARK_BAD_STAMP, None, mark, stamp
    return _MARK_READ, when, mark, stamp


def _resolves_by_uuid(exchanges: list[dict], uuid: str) -> bool:
    """Is `uuid` still among the transcript's exchanges — i.e. does the mark resolve exactly?

    `entries_after` tries the uuid branch first and returns `exchanges[i+1:]` on a match, without
    ever consulting the timestamp, so a mark whose stamp is unusable is still exact while its uuid
    is there. It *falls through* to the lexicographic timestamp branch when the uuid is not
    found (a rotated transcript) — which is where an unparseable stamp is dangerous again.
    That difference decides between a demand and a skip, so it is measured, not assumed.
    """
    return any(ex.get("uuid") == uuid for ex in exchanges)


def _check_marks(cfg: Config, now: datetime) -> Check:
    """The two marks must tell a consistent story about what has been read and consolidated.

    Both marks are classified before either is used. "Marks consistent" is a claim about a
    comparison, and with one mark unreadable the comparison short-circuited on `None` while this
    check still returned it — about a mark it had never read. A corrupt sleep mark is the single
    state in which "consolidation claims material the capture never read" *cannot* be ruled out,
    so it is the last state that may be reported as the healthy one.
    """
    capture_file = paths.capture_mark_file(cfg)
    sleep_file = paths.sleep_mark_file(cfg)
    capture_state, capture, capture_mark, capture_stamp = _classify_mark(capture_file)
    sleep_state, sleep, sleep_mark, sleep_stamp = _classify_mark(sleep_file)
    if capture_state == _MARK_MISSING and sleep_state == _MARK_MISSING:
        return Check("marks", "skip", "no marks yet — nothing has been captured or consolidated")

    both = (
        ("capture", capture_state, capture_stamp, capture_mark, capture_file),
        ("sleep", sleep_state, sleep_stamp, sleep_mark, sleep_file),
    )
    unusable = (
        "— the two marks cannot be compared, and a corrupt sleep mark is exactly the state in "
        "which consolidation may be claiming material the capture never read"
    )
    # Every failing class is tested across both marks before any `skip` is returned for either, so
    # a `skip` about one mark can never stand in for a `fail` about the other: corruption first,
    # then the clock, and only then the shapes this check cannot compare.
    for name, state, stamp, mark, mark_file in both:
        if state == _MARK_UNREADABLE:
            return Check(
                "marks", "fail",
                f"the {name} mark file is there but holds no readable mark ({mark_file}) "
                f"{unusable}",
                f"delete the {name} mark file, then run /sleep to re-consolidate this material",
            )
        if state in (_MARK_NO_STAMP, _MARK_BAD_STAMP) and not mark.get("uuid"):
            return Check(
                "marks", "fail",
                f"the {name} mark has no usable timestamp and no uuid to fall back on: {stamp!r} "
                f"{unusable}",
                f"delete the {name} mark file, then run /sleep to re-consolidate this material",
            )
    # The clock next, and before any `skip`: a mark ahead of `now` is a fault of the mark that
    # parsed, so the *other* mark being positional cannot excuse reporting it. A positional skip
    # used to be returned first, and a sleep mark hours in the future then went unreported.
    for name, when in (("capture", capture), ("sleep", sleep)):
        if when is not None and when > now + _SKEW:
            return Check("marks", "fail", f"the {name} mark is in the future: {when.isoformat()}",
                         "check the system clock; the next Stop hook will then correct it")

    # A mark carrying a uuid still resolves: `entries_after` tries the uuid branch first and never
    # looks at the timestamp on a match, so this is a legitimate `mark_of` shape, not corruption.
    # This check has no transcript and cannot tell whether the uuid is still there, so it says only
    # what it knows: the two marks cannot be compared *on time*. A demand here would be a false
    # alarm, and a false demand is what trains demands away.
    for name, state, stamp, mark, _ in both:
        if state in (_MARK_NO_STAMP, _MARK_BAD_STAMP):
            return Check(
                "marks", "skip",
                f"the {name} mark is positional: it identifies its exchange by uuid "
                f"({mark.get('uuid')!r}) and its timestamp is {stamp!r}, so the two marks cannot "
                "be compared on time — nothing here implies corruption",
            )

    if sleep_state == _MARK_MISSING:
        # Captured and not yet consolidated: the ordinary state of a working project, and there is
        # no comparison to make — so it is not reported as one.
        return Check("marks", "ok",
                     f"capture mark at {capture.isoformat()}; no sleep mark yet — nothing has "
                     "been consolidated, so there is nothing to compare it against")
    if capture_state == _MARK_MISSING:
        return Check(
            "marks", "skip",
            f"a sleep mark ({sleep.isoformat()}) but no capture mark file — the two cannot be "
            "compared",
            "the next Stop hook writes a capture mark; if none appears, see the hooks check above",
        )
    # Equal marks are normal: a sleep writes both at one instant, measured as the same millisecond.
    if sleep > capture:
        return Check(
            "marks", "fail",
            f"the sleep mark ({sleep.isoformat()}) is ahead of the capture mark "
            f"({capture.isoformat()}) — consolidation claims material the capture never read",
            "run /sleep to re-consolidate this material before it is dropped",
        )
    return Check("marks", "ok",
                 f"marks consistent: the sleep mark ({sleep.isoformat()}) is at or behind the "
                 f"capture mark ({capture.isoformat()})")


def _check_capture(cfg: Config, transcript_path: Path | None, now: datetime) -> Check:
    """Is the transcript running away from the capture mark?

    Exactly one exchange behind is the measured normal — the Stop hook reads the transcript before
    Claude Code has flushed the turn's last message, and `mark_at` deliberately covers only what
    was there. So a count threshold *and* a time threshold must both be exceeded; either alone
    would cry wolf on every ordinary turn.

    The mark is classified before the backlog is read, because the backlog means nothing on its
    own: `entries_after` returns the whole transcript when the mark is empty, and an empty list
    when the mark's timestamp does not parse. Both would otherwise arrive here as a plain count
    and be reported as a healthy lag.
    """
    if transcript_path is None:
        return Check("capture", "skip",
                     "no transcript given — run `lts doctor --transcript <path>` to check this")
    path = Path(transcript_path)
    if not path.exists():
        return Check("capture", "skip", f"transcript not found: {path}")

    # Read once, and before the mark is classified: every verdict below compares the transcript
    # against the mark, and a file that yielded nothing to compare cannot produce any of them.
    exchanges = transcript.read_exchanges(path)
    if not exchanges:
        # The old `ok` read an empty backlog as "you are caught up" and explained it with the
        # flush lag — a specific benign claim about a file that held no exchange at all. An empty
        # file and a short note that is not a transcript both bought it. `skip`, because nothing
        # here is proven broken: what is missing is the measurement.
        return Check(
            "capture", "skip",
            f"the transcript yielded no exchanges ({path}), so there is nothing to measure the "
            "capture mark against",
            "point `--transcript` at a live session transcript — the one Claude Code is writing "
            "for this session",
        )

    mark_file = paths.capture_mark_file(cfg)
    state, marked, mark, stamp = _classify_mark(mark_file)
    if state == _MARK_MISSING:
        # Not `fail`: a brand-new project legitimately has no mark and a transcript full of
        # exchanges, and a false demand is what trains demands away. The same failure class is
        # still caught, more cheaply: the `capture_progress` trend check sees an absent or frozen
        # mark across several journalled runs with a flat buffer.
        return Check(
            "capture", "skip",
            "no capture mark yet — there is nothing to measure the transcript against",
            "the next Stop hook writes one; if none appears, see the hooks check above",
        )
    if state == _MARK_UNREADABLE:
        # "No mark yet" would be a false statement with the file sitting right there, and its fix
        # — wait for the next Stop hook — would send the reader past the file to delete.
        return Check(
            "capture", "skip",
            f"the capture mark file could not be read as a mark ({mark_file}) — there is nothing "
            "to measure the transcript against",
            "delete the mark file so the next Stop hook writes a fresh one",
        )
    if state in (_MARK_NO_STAMP, _MARK_BAD_STAMP):
        # Unlike `_check_marks`, this check holds the transcript, so it can establish which branch
        # of `entries_after` actually ran instead of assuming the worst.
        uuid = mark.get("uuid")
        if uuid and _resolves_by_uuid(exchanges, uuid):
            # The uuid branch matched and returned `exchanges[i+1:]` without ever reading the
            # timestamp: the count is exact. The instant is not, so no time claim is made — and no
            # `fix` either, because this mark is functional and deleting it would re-capture
            # everything behind it.
            backlog = watermark.exchanges_after(exchanges, mark)
            return Check(
                "capture", "skip",
                f"{len(backlog)} exchange(s) behind the capture mark, resolved by uuid ({uuid!r}) "
                f"— the mark's timestamp is {stamp!r}, so there is no instant to measure against",
            )
        if state == _MARK_BAD_STAMP:
            # Either no uuid, or a uuid the transcript no longer holds: `entries_after` falls
            # through to its lexicographic filter, no real stamp sorts above an unparseable one and
            # the backlog comes back empty — the system would claim to be perfectly current.
            found = "" if not uuid else f"its uuid {uuid!r} is not in the transcript, and "
            return Check(
                "capture", "fail",
                f"{found}the capture mark timestamp does not parse: {stamp!r} — the backlog "
                "cannot be trusted, and an unreadable mark makes the transcript look captured",
                "delete the capture mark file so the next Stop hook writes a fresh one",
            )
        # No stamp at all, so there is no lexicographic trap: `entries_after` would reach its count
        # branch and the backlog would be positional guesswork. It is not read here, so the message
        # stays in the conditional — nothing has been resolved at this point.
        where = f" (its uuid {uuid!r} is not in the transcript)" if uuid else ""
        return Check(
            "capture", "skip",
            f"the capture mark has no timestamp to measure against: {stamp!r}{where}, so a backlog "
            f"would be resolved by count alone ({mark_file})",
            "delete the mark file so the next Stop hook writes a fresh one",
        )

    backlog = watermark.exchanges_after(exchanges, mark)
    # A mark newer than every exchange in the file was not written from this file. The lexicographic
    # filter in `entries_after` then drops everything, the backlog is 0, and the `ok` below reports
    # "the normal flush lag" — a specific benign claim about a transcript the mark has nothing to do
    # with. `d0a9771` closed the neighbouring state, a file that yielded no exchanges at all; this is
    # the state where the exchanges are there and belong to some other session. `--transcript` is
    # typed by a human at a terminal, where a typo is the ordinary case, so this is the likely way
    # in. A uuid that still resolves proves the opposite — the mark does name an exchange in this
    # file — and a transcript rewritten under such a mark is the negative-lag `fail` further down,
    # which this must not stand in front of.
    stamps = [when for when in (_stamp_time(ex.get("timestamp")) for ex in exchanges) if when]
    newest_in_file = max(stamps, default=None)
    uuid = mark.get("uuid")
    if (newest_in_file is not None and marked > newest_in_file + _SKEW
            and not (uuid and _resolves_by_uuid(exchanges, uuid))):
        ahead = int((marked - newest_in_file).total_seconds() // 60)
        return Check(
            "capture", "skip",
            f"the capture mark ({marked.isoformat()}) is newer than every exchange in this "
            f"transcript — the newest of its {len(exchanges)} is {newest_in_file.isoformat()}, "
            f"{ahead} min earlier — so the mark does not belong to this file and there is nothing "
            "here to measure it against",
            "point `--transcript` at this session's transcript — the one Claude Code is writing "
            "for the session you are in",
        )
    if len(backlog) <= 1:
        return Check("capture", "ok",
                     f"{len(backlog)} exchange(s) behind the capture mark — the normal flush lag")
    # One lag, one reference: the newest exchange when it carries a readable stamp, `now` when it
    # does not. The claim below can only quote a number this line actually produced.
    newest = _stamp_time(backlog[-1].get("timestamp"))
    reference, measured_to = (
        (newest, "the newest exchange") if newest is not None
        else (now, "now (the newest exchange has no readable timestamp)")
    )
    lag = reference - marked
    # The distance, not the signed difference: `entries_after` resolves a mark by uuid without
    # looking at timestamps at all, so a rewritten transcript can put entries *older* than the
    # mark into the backlog. A negative lag slipped under the threshold and "within 10 min of the
    # newest exchange" was printed however far off the two really were.
    distance = abs(lag)
    tolerance = int(_CAPTURE_TOLERANCE.total_seconds() // 60)
    if distance > _CAPTURE_TOLERANCE:
        minutes = int(distance.total_seconds() // 60)
        if lag < timedelta(0):
            return Check(
                "capture", "fail",
                f"{len(backlog)} exchanges behind the capture mark, but {measured_to} is "
                f"{minutes} min older than the mark — the transcript was rewritten under it, so "
                "the backlog cannot be trusted",
                "delete the capture mark file so the next Stop hook writes a fresh one",
            )
        return Check(
            "capture", "fail",
            f"{len(backlog)} exchanges behind the capture mark, {minutes} min past it measured to "
            f"{measured_to} — the Stop hook is not capturing",
            "check /tmp/lts-hook-errors.log, and the hooks check above",
        )
    return Check(
        "capture", "ok",
        f"{len(backlog)} exchanges behind the capture mark, within {tolerance} min of "
        f"{measured_to}",
    )


def _check_pressure(metrics: dict | None) -> Check:
    """Measured tokens must fit the window they are measured against.

    `lts pressure` once reported a context percentage in the thousands on a real project. It
    estimated tokens as `len(file) // 4` over a transcript that is append-only across `--resume`
    — so its size is the project's whole history, not the live window — and divided by a
    hardcoded 200,000 instead of the configured window. On ppss that estimate was 45,413,420
    tokens, i.e. 22,707% of that window, while the live session held 301,343 tokens of its
    configured 1,000,000. The 22,707% is computed here from the recorded token count: the
    percentage recorded beside it, "9900%", follows from no window, and this branch quoted the
    two as one measurement for a while. The pair measured in the same pass on two other projects
    does follow the rule (18,793,961 -> 9397%, 6,373,099 -> 3187%), so it is that one figure that
    is wrong, not the account of the cause.

    A percentage above 100 is never real, whatever produced it, so this is an assertion on the
    output rather than a bug hunt — which is exactly why it survives a cause nobody can pin down.
    """
    measured = (metrics or {}).get("pressure")
    if not measured:
        return Check("pressure", "skip",
                     "no transcript given — run `lts doctor --transcript <path>` to check this")
    # A malformed measurement must not raise: an exception here propagates out of `run` and takes
    # every other check's answer with it, so the report that says whether memory is lying would not
    # appear at all. `_check_anchor_fresh` guards its `stat` for the same reason. And a measurement
    # that is not numbers is itself the impossible-percentage class — an output that cannot be
    # true.
    if not isinstance(measured, dict):
        return Check("pressure", "fail",
                     f"the pressure measurement is not a measurement: {measured!r}",
                     "this is `status.collect` output — the measurement itself is broken")
    try:
        tokens = int(measured.get("tokens") or 0)
        window = int(measured.get("window") or 0)
    except (TypeError, ValueError):
        return Check(
            "pressure", "fail",
            f"the pressure measurement does not hold numbers: tokens={measured.get('tokens')!r}, "
            f"window={measured.get('window')!r}",
            "this is `status.collect` output — the measurement itself is broken",
        )
    if window <= 0:
        return Check("pressure", "fail", f"the context window is {window}",
                     "set `[context] window` in config.toml")
    # A measurement with no `source` is read as measured, because `status.collect` is the only
    # producer of this dict and it always sets one: the alternative would skip this check for
    # every caller that hand-builds a metrics dict, including the tests that pin the impossible
    # reading. If a second producer ever appears, this default is where it must be revisited.
    source = measured.get("source", transcript.USAGE)
    if source != transcript.USAGE:
        # The figure is `len(file) // 4`, not a reading of the window, and comparing it to a
        # window it was never measured against is precisely what produced the 22,707%. An empty
        # file and a 48-byte note both used to land on `ok` here, because the estimate arrived as
        # an ordinary number. `skip` rather than `fail`: nothing is proven broken, the check
        # simply did not get a measurement, and this module never reports that as passing.
        why = ("no assistant `usage` record was found, so this figure is a size estimate"
               if source == transcript.ESTIMATE else "no transcript file was read")
        return Check("pressure", "skip", f"not measured: {why}",
                     "point `--transcript` at a live session transcript — Claude Code writes a "
                     "`usage` record into every assistant message")
    if tokens > window:
        return Check(
            "pressure", "fail",
            f"measured {tokens:,} tokens against a {window:,}-token window "
            f"({round(tokens / window * 100)}%)",
            "a percentage above 100 is never real — either the measurement or "
            "`[context] window` is wrong",
        )
    return Check("pressure", "ok", f"{tokens:,}/{window:,} tokens ({round(tokens / window * 100)}%)")


def _note_time(note: Path) -> datetime | None:
    """The local time a session note's own title states, or None when the title does not state one.

    `naming.session_note_name` guarantees the shape: `Session_[<author>_]YYYY-MM-DD_HHMM`, written
    by the same /sleep that wrote the note. Anything else in `session-memory/` — a hand-made note, a
    stray file — has no stamp here and falls back to the mtime.
    """
    match = _SESSION_STAMP.search(note.stem)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d_%H%M")
    except ValueError:   # a well-shaped stamp that is not a date, e.g. month 13
        return None


def _check_anchor_fresh(cfg: Config) -> Check:
    """The anchor is the entry point a new session reads; it must not lag the notes it points at.

    A note is dated by its title, not by its mtime. `lts.digest` says of this same vault that "`git
    pull`/`checkout` rewrites mtimes, so it would report false positives in a team — it is a
    fallback, never a preference", and the README ranks session notes by title "since the title is
    the date and, unlike an mtime, it survives a `git clone`". Dating them here by mtime contradicted
    both: after any vault pull, or a fresh clone on a second machine, every mtime becomes now and
    this check warned that the entry points were a session behind when they were not — on the
    documented team-mode path, i.e. exactly where it is least recoverable.

    The two stamps being compared are also of one kind now, which is the happier half of the same
    change: the anchor's `updated` is a minute-granular local stamp written by the /sleep that named
    the note, so title against `updated` compares two stamps produced by one step, where title
    against mtime compared a stamp to a filesystem fact. `_ANCHOR_SKEW` still covers the ordering
    inside that step — the note is written before the anchor — and still covers the mtime fallback,
    which is what a note whose title carries no stamp is dated by.
    """
    entry = anchor.read_anchor(paths.anchor_file(cfg))
    # `naming.SESSION_DIR`, not a literal: a renamed folder would find zero notes here and degrade
    # into the silent false pass this module exists to prevent.
    sessions_dir = paths.vault_root(cfg) / naming.SESSION_DIR
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
    # The title first, because it is the stamp /sleep wrote; the mtime only for a title that carries
    # none, and guarded, because statting a note that vanished mid-scan raises and an exception here
    # takes the whole report down with it.
    stamped: list[tuple[datetime, Path]] = []
    for note in sessions:
        titled = _note_time(note)
        if titled is not None:
            stamped.append((titled, note))
            continue
        try:
            stamped.append((datetime.fromtimestamp(note.stat().st_mtime), note))
        except OSError:
            continue  # a note that is no longer there cannot be the newest one
    if not stamped:
        # No `fix`: a `skip` carries one only when there is an action that would make the check
        # measurable. There is none here — re-running the same check is not a fix, and `render`
        # printing "Fix:" under a check that did not run reads as though something was diagnosed.
        return Check(
            "anchor_fresh", "skip",
            f"none of the {len(sessions)} session note(s) could be dated: no title carries a "
            "/sleep stamp and none could be statted — something else may be rewriting the vault",
        )
    # Both sides are naive local times: `updated` is minute-granular local, and so is a title stamp;
    # an mtime read through `fromtimestamp` is local too.
    note_at, newest = max(stamped, key=lambda pair: pair[0])
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


# What a journal record's fields must hold for a trend to be read off them. Records come off disk
# and `lts.journal` filters non-JSON and non-dict lines but not field types, so any JSON type can
# arrive; each of these used to raise instead, aborting all ten checks.
_TREND_FIELDS = {"blocks": (list, tuple), "capture_mark": (str, int, float),
                 "stm_entries": (int, float)}
# The fewest readable records a trend is measured on. Two is the stricter of the two requirements:
# `_check_capture_progress` compares one record against the next, so one record shows no change at
# all, while `_check_anchor_delivery` could answer from a single record. Holding both to two is
# deliberate — a demand raised on one surviving record out of five is the false-alarm class this
# module avoids, and the conservative direction is a skip that names how little it could read.
_TREND_MIN = 2


def _readable(run: dict, field: str) -> bool:
    """Can this trend read `run[field]`?

    A missing field is legitimate — nothing emitted, nothing captured — and reads as empty. A field
    of the wrong type is a record this trend cannot read, and it costs itself alone.

    A field name that is not in `_TREND_FIELDS` is unreadable too, rather than a `KeyError` out of
    `run`: `_trend_window` takes its field names from the caller, so a trend check added later and
    naming a field nobody entered in the table would otherwise abort all ten checks — the very
    failure this guard exists to prevent.
    """
    allowed = _TREND_FIELDS.get(field)
    if allowed is None:
        return False
    value = run.get(field)
    return value is None or isinstance(value, allowed)


def _trend_scope(usable: int) -> str:
    """The window a trend actually read, for a message that may claim no more than was measured."""
    if usable == TREND_WINDOW:
        return f"the last {TREND_WINDOW} sessions"
    return f"the {usable} of the last {TREND_WINDOW} sessions whose records could be read"


def _trend_window(
    history: list[dict] | None, check_id: str, *fields: str
) -> tuple[list[dict], Check | None]:
    """The records of the last `TREND_WINDOW` that are readable on `fields`, or the `skip` saying
    why there is no trend to read.

    `history is None` and an empty journal are different facts and must not share a message: if the
    wiring passes no `history`, "0 of 5 runs journalled" is a claim about a file nobody read, and it
    would stand for ever while the journal filled up. That skip carries no `fix`: it is a wiring
    fault, and there is nothing the reader of the report can do about it.

    An unreadable record is dropped, not fatal to the check: skipping the whole trend would blind
    it for `TREND_WINDOW` sessions over one corrupt line, which breaks `lts.journal`'s promise
    that a bad line costs itself and never the history. The caller states how many records it
    measured, via `_trend_scope`, so the claim never covers records that were not read.
    """
    if history is None:
        return [], Check(check_id, "skip",
                         "no journal history was supplied to this run — the trend was not measured")
    runs = list(history)
    if len(runs) < TREND_WINDOW:
        return [], Check(check_id, "skip",
                         f"{len(runs)} of {TREND_WINDOW} runs journalled — the trend needs "
                         f"{TREND_WINDOW}")
    recent = runs[-TREND_WINDOW:]
    usable = [run for run in recent if all(_readable(run, field) for field in fields)]
    if len(usable) < _TREND_MIN:
        # No `fix`: nothing the reader can do makes these records readable, and the next runs write
        # well-formed ones by themselves. The fields are named so the reader knows what was unusable.
        named = ", ".join(f"`{field}`" for field in fields)
        return [], Check(check_id, "skip",
                         f"only {len(usable)} of the last {TREND_WINDOW} journal records could be "
                         f"read on {named} — a trend needs at least {_TREND_MIN}")
    return usable, None


def _check_anchor_delivery(cfg: Config, history: list[dict] | None) -> Check:
    """Did the anchor actually reach the model recently?

    This is the two-month bug expressed as a check. It cannot be answered from the filesystem: at
    every instant the anchor file existed and had content. Only the record of what each run emitted
    shows that it was never handed over.

    The filesystem still supplies one fact the journal cannot: whether there was an anchor to hand
    over at all. `SessionStart` records the `anchor` block only when `render_anchor` returned
    something, so a project that has not run /sleep yet journals five runs without it and the
    records alone are identical to five runs that withheld a real anchor. Demanding attention for
    the first is a false demand, and a false demand is what trains demands away — days can pass
    before a project's first /sleep. Absence of an anchor is unambiguous, so it is the fact this
    check asks for; "every journalled run predates the anchor's `updated`" was considered and
    rejected, because it cannot tell a just-written first anchor from a hook that has been broken
    for weeks and a sleep that has just refreshed the anchor.
    """
    usable, no_trend = _trend_window(history, "anchor_delivery", "blocks")
    if no_trend is not None:
        return no_trend
    scope = _trend_scope(len(usable))
    if any(ANCHOR_BLOCK in (run.get("blocks") or []) for run in usable):
        return Check("anchor_delivery", "ok", f"the anchor reached the model within {scope}")
    if not anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))):
        # No `fix`: nothing is broken and nothing is owed here. `render` prints "Fix:" under every
        # level but `ok`, and a fix line under this state would read as a diagnosis.
        return Check(
            "anchor_delivery", "skip",
            f"there is no anchor to deliver, so {scope} could not have delivered one — /sleep "
            "writes the first anchor",
        )
    return Check("anchor_delivery", "fail", f"the anchor has not reached the model in {scope}",
                 "run `lts anchor render` — if it prints nothing the anchor file is empty")


def _check_capture_progress(history: list[dict] | None) -> Check:
    """Is the Stop hook still capturing? The cheap form of the `capture` check.

    Both conditions are needed. A session that sleeps every time keeps `stm_entries` at 0
    legitimately while its mark still moves, so a flat buffer alone proves nothing.
    """
    usable, no_trend = _trend_window(history, "capture_progress", "capture_mark", "stm_entries")
    if no_trend is not None:
        return no_trend
    scope = _trend_scope(len(usable))
    marks = {run.get("capture_mark") for run in usable}
    counts = [run.get("stm_entries") or 0 for run in usable]
    moved = len(marks) > 1
    grew = any(b > a for a, b in zip(counts, counts[1:]))
    if moved or grew:
        # Name the signal and the window. "STM capture is progressing" named neither, and a message
        # carrying no quantity leaves only the level for the regression net to check.
        signals = [name for name, seen in (("the capture mark moved", moved),
                                           ("the STM buffer grew", grew)) if seen]
        return Check("capture_progress", "ok", f"{' and '.join(signals)} in {scope}")
    if marks == {None}:
        # A fresh project's first runs record no mark at all — and so do the runs of a project
        # whose Stop hook has been dead since it was installed. From the journal the two are the
        # same five records: no mark anywhere and a flat buffer. The branch is right to separate
        # them from a frozen mark, which does prove a hook ran once; its old `fail` was not, and
        # neither was its fix line opening "expected before the first exchange" — a `fail` is a
        # demand that says memory may be lying, and this one asserted which of two indistinguishable
        # states it was looking at. `skip` is this module's word for a state it could not measure,
        # and the state that *is* measurable from here is the `hooks` check's, not this one's.
        return Check("capture_progress", "skip",
                     f"no capture mark was recorded in any of {scope} and the buffer has not "
                     "grown — either nothing has been captured here yet, or the Stop hook has "
                     "never written a mark; these records cannot tell the two apart",
                     "if these sessions had exchanges, see the hooks check above and "
                     "/tmp/lts-hook-errors.log")
    return Check("capture_progress", "fail",
                 f"the capture mark has been frozen at {next(iter(marks))!r} and the buffer has "
                 f"not grown in {scope} — the Stop hook is not capturing",
                 "check /tmp/lts-hook-errors.log, and the hooks check above")


def _check_capture_live(cfg: Config, turn: dict | None, transcript_path: Path | None) -> Check:
    """Was the `Stop` hook given work between the last two prompts, and did it fail to do it?

    The only detector of a dead `Stop` that works **mid-session**. `_check_capture_progress` reads the
    health journal, which gains a record only at `SessionStart`, so between two compactions it answers
    `ok` from the same records while every exchange since the last one goes uncaptured.

    Two facts together, because either alone lies. The mark not moving is not enough: read
    2026-10-01, `stop.py:65-75` leaves it alone for an unreadable transcript (the `if here:` guard,
    so a reset cannot replay the file) and writes the same value back for a turn that produced no
    exchange (`mark_of` names the newest *exchange*, and `transcript.is_scaffolding` drops a bare
    slash command and an isMeta record). Something being newer than the mark is not enough either:
    that is the ordinary state between a turn ending and the next `Stop` running. Together they say
    the hook was given work and did not do it.

    There is deliberately **no sleep exemption**, though the design document asked for one. The armed
    branch of `stop.py:56-59` *writes* `watermark.mark_at(now)` to the capture mark, so during a sleep
    the mark moves and the count resets by itself — the exemption's stated reason was invented. And
    `stop.py` is what disarms the flag, so the one state in which it stays armed across prompts is a
    `Stop` hook that is not running: an exemption keyed on that flag would be blind in exactly the
    state this check exists to report.

    The evidence is dated in the message because the turn state is as old as the last prompt; from
    `lts doctor` that can be hours ago, and the present tense would be a claim this check cannot make.
    """
    if not turn:
        return Check(
            "capture_live", "skip",
            "no turn state yet — the `UserPromptSubmit` hook writes it, and there is no previous "
            "prompt to compare against",
        )
    misses = turn.get("misses")
    at = turn.get("at")
    if not isinstance(misses, int) or isinstance(misses, bool) or misses < 0:
        return Check(
            "capture_live", "skip",
            f"the turn state carries no usable miss count: {misses!r}",
            "delete the turn-state file; the next prompt writes a fresh one",
        )
    if misses < _CAPTURE_MISS_TOLERANCE:
        return Check(
            "capture_live", "ok",
            f"{misses} consecutive prompt(s) without a capture, under the tolerance of "
            f"{_CAPTURE_MISS_TOLERANCE} (as of {at})",
        )
    if transcript_path is None:
        return Check(
            "capture_live", "skip",
            f"the capture mark has not moved in {misses} consecutive prompts (as of {at}), but "
            "without a transcript there is no way to tell whether the Stop hook had anything to "
            "capture",
            "run `lts doctor --transcript <path>`, or read the per-turn report, which always has one",
        )
    mark = watermark.read_mark(paths.capture_mark_file(cfg)).get("timestamp")
    if not transcript.newest_exchange_after(transcript_path, mark):
        return Check(
            "capture_live", "ok",
            f"the capture mark has not moved in {misses} consecutive prompts (as of {at}), and "
            "nothing in the transcript is newer than it — there was nothing to capture",
        )
    return Check(
        "capture_live", "fail",
        f"the capture mark has not moved in {misses} consecutive prompts (as of {at}) while the "
        "transcript holds exchanges newer than it — the Stop hook is not capturing",
        "check /tmp/lts-hook-errors.log and the hooks check above",
    )
