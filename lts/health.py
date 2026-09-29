"""Check whether memory is working, and whether what it says about itself is true.

Three failures have been found in this project, and every one was found by accident after running
for a long time: `SessionStart` withheld the anchor for ~2 months, `lts pressure` reported 9900%,
and the model stated a context percentage that no component had produced. A liveness poll — is the
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
from lts.healthchecks import Check

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
    budget: with a transcript, the file is parsed twice for two different things — `status.collect`
    reads its usage counters, `_check_capture` reads its exchanges — and once more in the degenerate
    uuid branch. Supplying `metrics` moves the first parse to the caller rather than removing it, so
    there is nothing here to optimise away. `lts doctor` is the only caller so far; the hook
    integration is a later task, and it is to pass no transcript at all. A transcript is
    append-only across `--resume`, so it accumulates the whole history of a project and runs to
    hundreds of megabytes — the largest on this machine measured 187.5 MB on 2026-09-25 and
    208,142,001 bytes on 2026-09-29, so any single figure is a snapshot, not a property — and
    parsing one on a hook's critical path would not fit the 3–5 s budget the spec sets for
    loading a new session's context (§6).

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
    hook is to pass `health_demand`, `sleep_demand`, `anchor` (`_ANCHOR_BLOCK`) and `digest`, one
    per block it emitted. Nothing calls `record` yet — the hook that writes these records is a
    later task — so that list is the contract its caller will be held to, not a description of
    traffic on disk. A name outside it is recorded and never read.
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
