from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import anchor, digest, health, journal, notify, paths, pending, status, stm
from lts.config import load_config
from lts.hooklog import log_error

_FORCE_MSG = (
    "## Unfinished sleep detected\n"
    "Un-consolidated memory is waiting: {stm} STM entries, {snapshots} pending snapshot(s).\n"
    "This is new material only — everything already consolidated sits behind the sleep mark "
    "and was never written here, so it is not a duplicate of notes you already hold. Do not "
    "spend a turn checking. Before doing anything else, run the `/sleep` skill to "
    "finish sleeping this material into long-term notes, then continue."
)

_UNCONFIGURED_MSG = (
    "## Memory is not configured here\n"
    "No `config.toml` with a `[vault]` section was found at or above `{root}`, so nothing "
    "will be captured this session and no `.ai_memory/` will be created. If this project "
    "used to have memory, its `config.toml` was moved or deleted. Run `lts doctor` to see "
    "where memory is expected to live, and tell the user."
)


def build_blocks(event: dict, *, root: Path | None = None) -> tuple[str, str]:
    """`(context, system_message)` — what the model is given, and what the user is shown.

    One function because the user's line is derived from the same `checks` and `metrics` this hook
    already computes, and this is the hook that pays for the two directory walks: `sidecars` measured
    599.686 ms on a Nextcloud checkout on 2026-10-01. Computing them twice to keep two entry points
    tidy would double the most expensive thing this hook does.

    `additionalContext` reaches the model and `systemMessage` reaches the user — observed, not
    documented, on 2026-10-01 against `UserPromptSubmit` (spec §2.1.1). The same field on
    `SessionStart` is documented but **unobserved**: the event fires only at session start and on
    `/compact`, so it could not be probed in the session that probed the other one. What is proven
    here is what this hook returns, not what the harness puts on the screen; the first `/compact`
    after rollout is where the second half gets confirmed.
    """
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        # Nothing for the user's channel: `notify` speaks about checks and metrics, and an
        # unconfigured project has neither — `status.collect` has no sidecar to read and no check
        # ran. "Memory is not configured here" is also none of the three subjects spec §6.1 lists,
        # so inventing a fourth one here would be a design decision taken in a hook.
        return _UNCONFIGURED_MSG.format(root=cfg.project_root), ""
    snapshots = pending.list_snapshots(paths.pending_dir(cfg))
    stm_text = stm.read(paths.stm_file(cfg))
    stm_entries = len([ln for ln in stm_text.splitlines() if ln.strip()])

    # The sleep demand is one block among several, never a replacement for them. It used to
    # return early — "one demand at a time" — which assumed sessions end often. A session that
    # runs for months on /sleep + /compact never has an empty buffer, so the anchor and the
    # digest were withheld permanently and the model had no idea which notes existed.
    # Each block is carried with the name `health.record` knows it by, so that what is journalled
    # is derived from what is emitted rather than restated alongside it.
    blocks: list[tuple[str, str]] = []
    if snapshots or stm_entries:
        blocks.append(
            ("sleep_demand", _FORCE_MSG.format(stm=stm_entries, snapshots=len(snapshots)))
        )

    # The digest scans the vault (e.g. note.stat()), which can raise on a dangling symlink or
    # a mid-scan race. That must never suppress the anchor, so degrade the digest to "".
    try:
        digest_block = digest.render(digest.collect(cfg))
    except Exception:
        digest_block = ""
    blocks += [
        # `health.ANCHOR_BLOCK` rather than a retyped "anchor": `_check_anchor_delivery` matches
        # this name against the journal, and a typo here would fail that check forever.
        (health.ANCHOR_BLOCK,
         anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg)))),
        ("digest", digest_block),
    ]

    # No transcript, deliberately — and not because a parse is too slow. Measured 2026-09-29 on a
    # 208,142,001-byte transcript: `context_tokens` 1.46 s and 1.57 s, `read_exchanges` 1.48 s and
    # 1.52 s, so one parse fits the 3-5 s session-load budget of ТЗ §6 with room to spare. The
    # reason is that `--transcript` costs both parses, ~3.0 s on top of what this hook already
    # spends, on every /compact, while both failure classes are covered more cheaply: the capture
    # class by `capture_progress`, which reads five journal records and no transcript at all, and
    # the impossible-percentage class by the invariant in `user_prompt_submit._context_line`.
    # `lts.health.run` carries the same argument with the same figures.
    #
    # `health.TREND_WINDOW` is the window the trend checks require: handing them fewer records makes
    # them skip, and more is history they discard.
    #
    # Wrapped for the same reason the digest is, and more urgently: `main()` turns any exception
    # into `{}`, so a raise here would withhold the anchor, the digest and the sleep demand at
    # once — the two-month bug, reproduced by the code written to detect it. The raise is
    # reachable, not theoretical: `status.collect` stats every pending snapshot, and a concurrent
    # /sleep calls `pending.clear_all` between the listing and the stat. A failure in the health
    # path must cost the health output and nothing else.
    # Before the `try`, not inside it: `main()` turns any exception into `{}`, so the user's channel
    # has to have a value even when the block below never reaches its last statement. Undefined here
    # would mean a raise in the health path withholds the anchor again — 5bba7f2's bug, reintroduced
    # by the code added to report it.
    system_message = ""
    try:
        # One `status.collect` per run: `health.run` and `health.record` each compute their own when
        # none is given, so `metrics` saves the second one. What that second one costs is a full STM
        # read, a listing plus a `stat` of every pending snapshot, and a read of the anchor — 0.365
        # ms, measured 2026-09-30 on this project (23-note vault, 122 STM entries, no pending
        # snapshots). It is not a vault walk: `lts.status.collect` never walks the vault, it only
        # joins the vault path into a string. This comment used to say "a vault rglob twice", which
        # was a claim about work `status.collect` does not do.
        #
        # The vault *is* rglobbed twice per run, and `metrics` does nothing about it: once in
        # `healthchecks._check_vault` to count notes, once in `health.record` to record the same
        # count, plus the `session-memory` subtree in `_check_anchor_fresh`. Measured the same day:
        # 0.290 ms for the whole vault at 23 notes and 0.107 ms for the subtree, and 0.392 ms for a
        # synthetic 250-note vault on a local ext4 filesystem — this vault sits on a fuse mount
        # whose per-entry cost measured ~4x that, so ~1.6 ms extrapolated at 250 notes. Not worth
        # fixing: the duplicate walk is ~0.05% of the 3-5 s session-load budget of ТЗ §6, and
        # removing it would mean threading a check's internal note count into the journal record.
        metrics = status.collect(cfg)
        journal_file = paths.health_journal_file(cfg)
        checks = health.run(cfg, metrics=metrics,
                            history=journal.tail(journal_file, health.TREND_WINDOW))
        failure = health.demand(checks)
        if failure:
            # First, because it is the one block that says the others may be untrustworthy.
            blocks.insert(0, ("health_demand", failure))

        # After the blocks are chosen, never before: the record's whole value is stating what was
        # actually emitted, which is the only input `_check_anchor_delivery` has. `journal.append`
        # swallows every exception, so a broken journal costs the record and never the blocks.
        emitted = [name for name, body in blocks if body]
        journal.append(journal_file, health.record(cfg, checks, emitted, metrics=metrics))

        # `{}` as the previous state, deliberately — not an oversight and not a missing read of
        # `turn-state.json`: a new session states what is outstanding even if the previous session's
        # last turn already said it. The per-turn hook owns the cadence, because it is the one that
        # holds the fingerprints; this hook owns the opening statement, because a new session is a
        # new screen and nothing on it carries over. The returned fingerprints are therefore
        # discarded rather than stored — writing them would make this hook's statement suppress the
        # per-turn hook's first one, which is the opposite of the intent.
        #
        # Last in the guarded block, after `journal.append`, so a raise here costs the user's line
        # and not the health record as well.
        system_message = notify.message(checks, metrics, {})[0]
    except Exception:
        pass
    return "\n\n".join(body for _name, body in blocks if body), system_message


def build_context(event: dict, *, root: Path | None = None) -> str:
    """The model's half of `build_blocks`, kept as the name every test already calls."""
    return build_blocks(event, root=root)[0]


def run(event: dict, *, root: Path | None = None) -> dict:
    context, system_message = build_blocks(event, root=root)
    out: dict = {}
    if system_message:
        out["systemMessage"] = system_message
    if context:
        out["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": context,
        }
    return out


def main() -> None:
    try:
        event = json.load(sys.stdin)
        result = run(event)
    except Exception:
        log_error(__file__)
        result = {}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
