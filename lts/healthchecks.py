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
# The anchor's `updated` is minute-granular local time, so a sleep can look up to a minute older
# than the note it just wrote.
_ANCHOR_SKEW = timedelta(minutes=2)
# How many journalled runs a trend looks at. Five compactions is days of work in a continuous
# session — long enough that a one-off is not a trend, short enough to notice within a day.
_TREND_WINDOW = 5
# The block name `_check_anchor_delivery` looks for in a journal record: a constant because the hook
# calling `record` decides the names it passes, and a typo there would fail the check for ever.
# `record`'s docstring spells the whole vocabulary for that caller.
_ANCHOR_BLOCK = "anchor"


@dataclass(frozen=True)
class Check:
    id: str
    level: str          # "ok" | "warn" | "fail" | "skip"
    message: str
    fix: str | None = None


def all_checks(
    cfg: Config,
    *,
    transcript_path: Path | None = None,
    metrics: dict | None = None,
    history: list[dict] | None = None,
    now: datetime | None = None,
) -> list[Check]:
    """Every check this module can make about `cfg`, in the order the report states them.

    The one public entry point. The checks and the helpers they own stay private, so the interface
    between this module and `lts.health` is `Check` plus this function — a surface that is free to
    settle now, while nothing outside the tests calls it.

    `now` is normalised here because every check that compares anything mixes a caller's `now` with
    a mark parsed out of a file, and one naive operand raises rather than answering. The order is
    fixed here too: it is the order the rendered report and the journal record share.

    `history` arrives as a plain list of dicts, never a `lts.journal` handle: the trend checks live
    here now, and this module must not import `journal` — the journal is best-effort by design and
    a health check that could break on it would be a new way for memory to die silently.

    A configured project is assumed. Without one, the config verdict is the only thing that can be
    established — there is nothing for the other nine to measure — so that is all this returns, and
    `lts.health` says what the rest of the report reads like in that case.
    """
    now = _utc(now) or datetime.now(timezone.utc)
    config_check = _check_config(cfg)
    if not cfg.configured:
        return [config_check]
    return [
        config_check,
        _check_sidecars(cfg),
        _check_hooks(cfg),
        _check_vault(cfg),
        _check_marks(cfg, now),
        _check_capture(cfg, transcript_path, now),
        _check_pressure(metrics),
        _check_anchor_fresh(cfg),
        _check_anchor_delivery(history),
        _check_capture_progress(history),
    ]


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
    """
    nested, strays = [], []
    for sidecar in doctor.find_sidecars(cfg.project_root):
        (nested if is_lts_config(sidecar.parent / "config.toml") else strays).append(sidecar)
    also = ""
    if nested:
        also = (" — nested lts project root(s), each read by its own /sleep: "
                + ", ".join(str(p) for p in nested))
    if not strays:
        return Check("sidecars", "ok", "no stray sidecars" + also)
    return Check(
        "sidecars", "fail",
        "orphaned memory that /sleep will never read: "
        + ", ".join(str(p) for p in strays) + also,
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


def _check_anchor_fresh(cfg: Config) -> Check:
    """The anchor is the entry point a new session reads; it must not lag the notes it points at."""
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
    # One `stat` per note, kept: statting again for the mtime can raise on a note that vanished
    # between the two calls, and an exception here takes the whole report down with it.
    stamped = []
    for note in sessions:
        try:
            stamped.append((note.stat().st_mtime, note))
        except OSError:
            continue  # a note that is no longer there cannot be the newest one
    if not stamped:
        # No `fix`: a `skip` carries one only when there is an action that would make the check
        # measurable. There is none here — re-running the same check is not a fix, and `render`
        # printing "Fix:" under a check that did not run reads as though something was diagnosed.
        return Check(
            "anchor_fresh", "skip",
            f"none of the {len(sessions)} session note(s) could be read to compare the anchor "
            "against — something else may be rewriting the vault",
        )
    mtime, newest = max(stamped, key=lambda pair: pair[0])
    # Both sides are naive local times: `updated` is minute-granular local, the mtime is local.
    note_at = datetime.fromtimestamp(mtime)
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
    if usable == _TREND_WINDOW:
        return f"the last {_TREND_WINDOW} sessions"
    return f"the {usable} of the last {_TREND_WINDOW} sessions whose records could be read"


def _trend_window(
    history: list[dict] | None, check_id: str, *fields: str
) -> tuple[list[dict], Check | None]:
    """The records of the last `_TREND_WINDOW` that are readable on `fields`, or the `skip` saying
    why there is no trend to read.

    `history is None` and an empty journal are different facts and must not share a message: if the
    wiring passes no `history`, "0 of 5 runs journalled" is a claim about a file nobody read, and it
    would stand for ever while the journal filled up. That skip carries no `fix`: it is a wiring
    fault, and there is nothing the reader of the report can do about it.

    An unreadable record is dropped, not fatal to the check: skipping the whole trend would blind
    it for `_TREND_WINDOW` sessions over one corrupt line, which breaks `lts.journal`'s promise
    that a bad line costs itself and never the history. The caller states how many records it
    measured, via `_trend_scope`, so the claim never covers records that were not read.
    """
    if history is None:
        return [], Check(check_id, "skip",
                         "no journal history was supplied to this run — the trend was not measured")
    runs = list(history)
    if len(runs) < _TREND_WINDOW:
        return [], Check(check_id, "skip",
                         f"{len(runs)} of {_TREND_WINDOW} runs journalled — the trend needs "
                         f"{_TREND_WINDOW}")
    recent = runs[-_TREND_WINDOW:]
    usable = [run for run in recent if all(_readable(run, field) for field in fields)]
    if len(usable) < _TREND_MIN:
        # No `fix`: nothing the reader can do makes these records readable, and the next runs write
        # well-formed ones by themselves. The fields are named so the reader knows what was unusable.
        named = ", ".join(f"`{field}`" for field in fields)
        return [], Check(check_id, "skip",
                         f"only {len(usable)} of the last {_TREND_WINDOW} journal records could be "
                         f"read on {named} — a trend needs at least {_TREND_MIN}")
    return usable, None


def _check_anchor_delivery(history: list[dict] | None) -> Check:
    """Did the anchor actually reach the model recently?

    This is the two-month bug expressed as a check. It cannot be answered from the filesystem: at
    every instant the anchor file existed and had content. Only the record of what each run emitted
    shows that it was never handed over.
    """
    usable, no_trend = _trend_window(history, "anchor_delivery", "blocks")
    if no_trend is not None:
        return no_trend
    scope = _trend_scope(len(usable))
    if any(_ANCHOR_BLOCK in (run.get("blocks") or []) for run in usable):
        return Check("anchor_delivery", "ok", f"the anchor reached the model within {scope}")
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
        # A fresh project's first runs record no mark at all: same direction, but blaming the Stop
        # hook misattributes it — nothing was captured because nothing has happened yet.
        return Check("capture_progress", "fail",
                     f"no capture mark was recorded in any of {scope} and the buffer has not "
                     "grown — nothing has been captured here yet",
                     "expected before the first exchange; if these sessions had exchanges, check "
                     "/tmp/lts-hook-errors.log and the hooks check above")
    return Check("capture_progress", "fail",
                 f"the capture mark has been frozen at {next(iter(marks))!r} and the buffer has "
                 f"not grown in {scope} — the Stop hook is not capturing",
                 "check /tmp/lts-hook-errors.log, and the hooks check above")
