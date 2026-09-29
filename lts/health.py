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
from dataclasses import dataclass
from pathlib import Path

from lts import doctor, paths, sync
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


def _script_path(command: str) -> Path | None:
    """The `.py` file a hook command runs, or None when the command names none.

    `sync` writes `python3 "<abs path>"`, so the quoted form is the normal case; the unquoted
    fallback covers a wiring someone edited by hand.
    """
    words = (command or "").split()
    found = _QUOTED.search(command or "")
    token = found.group(1) if found else (words[-1] if words else "")
    return Path(token) if token.endswith(".py") else None


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
    gone = [
        f"{event} -> {script}"
        for event, commands in wired.items() if event in sync.HOOK_EVENTS
        for script in (_script_path(c) for c in commands)
        if script is not None and not script.exists()
    ]
    if missing or gone:
        parts = []
        if missing:
            parts.append("not wired: " + ", ".join(missing))
        if gone:
            parts.append("script missing: " + "; ".join(sorted(gone)))
        return Check(
            "hooks", "fail", "; ".join(parts),
            "`git -C ~/tools/LetTheAISleep pull` then `lts update`; re-run install.py if the "
            "clone moved — every project points at it by absolute path",
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


def run(cfg: Config) -> list[Check]:
    """Every check, in `_IDS` order.

    An unconfigured root short-circuits: without a project there is nothing to check, and saying
    `ok` about a check that never ran is the exact failure this module exists to prevent.
    """
    config_check = _check_config(cfg)
    if not cfg.configured:
        return [config_check] + [
            Check(check_id, "skip", "no lts project here") for check_id in _IDS[1:]
        ]
    return [
        config_check,
        _check_sidecars(cfg),
        _check_hooks(cfg),
        _check_vault(cfg),
    ]
