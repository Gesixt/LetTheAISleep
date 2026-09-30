"""Check whether memory is working, and whether what it says about itself is true.

Three failures have been found in this project, and every one was found by accident after running
for a long time: `SessionStart` withheld the anchor for ~2 months, `lts pressure` reported a
context percentage in the thousands (the exact figure it printed is not recoverable — see
`healthchecks._check_pressure`), and the model stated a context percentage that no component had
produced. A liveness poll — is the
hook wired, does the index answer — would have caught **none** of them: in all three cases every
component was alive and the *claims* were false.

So a liveness poll is not enough, and the checks that replace it live in `lts.healthchecks`, one
function per check; this module decides which of them run, orders the result, and renders it. The
rule those checks are held to — a check that quietly did not run is never reported as passing — is
stated there, with the three instances of that defect review found in them.

A failure produces a *demand*, not a line of information. The memory map exists because quiet
information does not change behaviour — the anchor was available and never used, `/recall` was
documented and never called. A health report that is merely present would be ignored the same way.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from lts import healthchecks, paths, status, watermark
from lts.config import Config
# `Check` lives with the checks that build it; the import is one-way, so there is no cycle.
# `TREND_WINDOW` and `ANCHOR_BLOCK` are re-exported deliberately, not incidentally: a caller of
# `run` has to read exactly `TREND_WINDOW` journal records for the trend checks to measure anything,
# and a caller of `record` has to name the anchor block `ANCHOR_BLOCK` for `_check_anchor_delivery`
# to find it. Both facts belong to this module's contract, so they are reachable from here instead
# of through `healthchecks`' private names, which `lts.cli` and the `SessionStart` hook were both
# reaching into.
from lts.healthchecks import ANCHOR_BLOCK, Check, TREND_WINDOW

# Worst first. `skip` outranks `ok` because a check that did not run is not a check that passed.
_ORDER = ("fail", "warn", "skip", "ok")
_SYMBOL = {"fail": "✗", "warn": "!", "skip": "·", "ok": "✓"}

_IDS = (
    "config", "sidecars", "hooks", "vault",
    "marks", "capture", "pressure", "anchor_fresh",
    "anchor_delivery", "capture_progress",
)

_DEMAND_HEAD = (
    "## Memory health: {n} check{s} FAILED\n"
    "Memory may be lying to you. Tell the user before doing anything else, and do not\n"
    "treat memory as intact until this is resolved."
)


def _uninterpretable(check: Check) -> bool:
    """True when this check's level is not one of the four this module can read.

    A level outside `_ORDER` is this subsystem's own defect class turned on itself: `worst`
    iterated the four known levels, matched none, and fell through to `"ok"`, so a typo in a level
    string — `Check("x", "boom", ...)` — reported a healthy system and `lts doctor` exited 0. The
    same value produced `demand() == ""` and sorted *below* the passing checks in `render`.

    So an unreadable level ranks as the most severe thing present, not the least: nothing about
    such a check has been established, least of all that what it measures is fine.
    """
    return check.level not in _ORDER


def worst(checks: list[Check]) -> str:
    """The most serious level present, or "ok" when there is nothing to report.

    A level this module cannot interpret answers `"fail"` — the most serious level in its
    vocabulary — rather than the level's own text, because every caller compares the answer against
    that vocabulary: `lts.cli` derives the exit code from `== "fail"`, and handing it a word it has
    never heard of would exit 0 on a report it could not read.
    """
    if any(_uninterpretable(c) for c in checks):
        return "fail"
    for level in _ORDER:
        if any(c.level == level for c in checks):
            return level
    return "ok"


def render(checks: list[Check]) -> str:
    rank = {level: i for i, level in enumerate(_ORDER)}
    lines = ["Memory health"]
    # `-1` for a level outside `_ORDER`: worst first means an uninterpretable one leads the report,
    # where it used to be printed last, under the checks that passed.
    for check in sorted(checks, key=lambda c: rank.get(c.level, -1)):
        lines.append(f"  {_SYMBOL.get(check.level, '?')} {check.id}: {check.message}")
        if check.level != "ok" and check.fix:
            lines.append(f"      Fix: {check.fix}")
    return "\n".join(lines)


def demand(checks: list[Check]) -> str:
    """The block a hook injects when memory is broken, or "" when nothing failed.

    Only `fail` reaches here, plus a check whose level cannot be read at all — `worst` already
    calls that a failure, and a demand that disagreed with the exit code would be a second way for
    this report to say two things at once. A warning that interrupts work gets trained away, and
    the failures are trained away with it.
    """
    failed = [c for c in checks if c.level == "fail" or _uninterpretable(c)]
    if not failed:
        return ""
    lines = [_DEMAND_HEAD.format(n=len(failed), s="" if len(failed) == 1 else "s")]
    for check in failed:
        lines.append(f"- {check.id}: {check.message}")
        if check.fix:
            lines.append(f"  Fix: {check.fix}")
    return "\n".join(lines)


def run(
    cfg: Config,
    *,
    transcript_path: Path | None = None,
    metrics: dict | None = None,
    history: list[dict] | None = None,
    now: datetime | None = None,
) -> list[Check]:
    """Every check, in `_IDS` order, assembled by `healthchecks.all_checks`.

    `metrics` is a `status.collect` result, taken as an argument so that health never recomputes
    memory load and `status.collect` stays the single place that counts it. It is not a parse
    budget: with a transcript, the file is parsed exactly twice, for two different things —
    `status.collect` reads its usage counters through `transcript.measure_context`, and
    `_check_capture` reads its exchanges through `read_exchanges`. Traced 2026-09-30 by counting
    `Path.read_text` against the transcript across a whole `run(transcript_path=...)`, in both of
    the states that differ: two parses for a transcript with `usage` records and two for one
    without. There is no third parse in the degenerate uuid branch — that branch works from the
    exchanges `_check_capture` already holds, which is what `watermark.exchanges_after` taking a
    list rather than a path is for. There *was* a third, in a state this sentence never named: a
    transcript with no `usage` record made `measure_context` call `estimate_tokens(path)`, which
    re-opened the file it had just read — ~1.5 s wasted on the 208 MB transcript, in precisely the
    state where the figure is an estimate anyway. It now estimates from the text in hand, so the
    count is two either way. Supplying `metrics` moves the first parse to the caller rather than
    removing it, so there is nothing here to optimise away. The callers are `lts doctor`, which
    passes a transcript when it is given one, and the `SessionStart` hook, which passes none — not
    because a parse is too slow, which was asserted here for a while and is false, but because it is
    not worth its share of the budget. Measured 2026-09-29 on a 208,142,001-byte transcript (the
    same file was recorded at 187.5 MB four days earlier, so the size is a snapshot of a growing
    file, not a property): `context_tokens` 1.46 s and 1.57 s, `read_exchanges` 1.48 s and 1.52 s.
    One parse fits the 3–5 s session-load budget of ТЗ §6 with room to spare; the two that
    `--transcript` performs come to ~3.0 s, on top of what `SessionStart` already spends, on every
    `/compact` — and both failure classes are covered more cheaply elsewhere: the capture class by
    `healthchecks._check_capture_progress`, which reads five journal records and no transcript at
    all, and the impossible-percentage class by the invariant in
    `claude/hooks/user_prompt_submit._context_line`. That is the whole argument; it used to point at
    a design document that lives outside this repository, which a reader holding only the repo
    cannot open.

    `history` is the last few `lts.journal` records — a trend check is an ordinary check that was
    given history as an input, not a separate mechanism. It stays a plain list of dicts; the reason
    is with the checks that read it, in `healthchecks.all_checks`.

    An unconfigured root short-circuits: without a project there is nothing to check, and saying
    `ok` about a check that never ran is the exact failure this module exists to prevent.
    """
    if not cfg.configured:
        # `all_checks` can establish the config verdict and nothing else without a project; the
        # nine skips are this module's vocabulary, so they are written here.
        return healthchecks.all_checks(cfg, now=now) + [
            Check(check_id, "skip", "no lts project here") for check_id in _IDS[1:]
        ]
    if metrics is None:
        metrics = status.collect(cfg, transcript_path=transcript_path)
    return healthchecks.all_checks(cfg, transcript_path=transcript_path, metrics=metrics,
                                   history=history, now=now)


def record(
    cfg: Config,
    checks: list[Check],
    blocks: list[str],
    *,
    metrics: dict | None = None,
    now: datetime | None = None,
) -> dict:
    """One journal record per run.

    `blocks` is the load-bearing field: it states which blocks the calling hook emitted, which is
    not recoverable from the filesystem afterwards and is the only way `_check_anchor_delivery`
    can work at all. Its vocabulary is fixed, because that check matches names: the `SessionStart`
    hook passes `health_demand`, `sleep_demand`, `anchor` (`ANCHOR_BLOCK`) and `digest`, one per
    block it emitted, and it is the only caller. A name outside that vocabulary is recorded and
    never read.
    """
    if metrics is None:
        metrics = status.collect(cfg) if cfg.configured else {}
    stm_info = (metrics or {}).get("stm") or {}
    pending_info = (metrics or {}).get("pending") or {}
    vault = paths.vault_root(cfg)
    return {
        "at": watermark.mark_at(now)["timestamp"],
        "event": "session_start",
        "blocks": list(blocks),
        "checks": {check.id: check.level for check in checks},
        "stm_entries": stm_info.get("lines", 0),
        "stm_bytes": stm_info.get("bytes", 0),
        "capture_mark": watermark.read_mark(paths.capture_mark_file(cfg)).get("timestamp"),
        "sleep_mark": watermark.read_mark(paths.sleep_mark_file(cfg)).get("timestamp"),
        "pending": pending_info.get("snapshots", 0),
        "notes": len(list(vault.rglob("*.md"))) if vault.is_dir() else 0,
    }
