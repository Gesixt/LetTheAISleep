from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import (anchor, digest, health, healthchecks, journal, paths, pending,
                 status, stm)
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


def build_context(event: dict, *, root: Path | None = None) -> str:
    cfg = load_config(root or event.get("cwd"))
    if not cfg.configured:
        return _UNCONFIGURED_MSG.format(root=cfg.project_root)
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
        # `_ANCHOR_BLOCK` rather than a retyped "anchor": `_check_anchor_delivery` matches this
        # name against the journal, and a typo here would fail that check forever.
        (healthchecks._ANCHOR_BLOCK,
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
    # `_TREND_WINDOW` is the window the trend checks require: handing them fewer records makes
    # them skip, and more is history they discard.
    #
    # Wrapped for the same reason the digest is, and more urgently: `main()` turns any exception
    # into `{}`, so a raise here would withhold the anchor, the digest and the sleep demand at
    # once — the two-month bug, reproduced by the code written to detect it. The raise is
    # reachable, not theoretical: `status.collect` stats every pending snapshot, and a concurrent
    # /sleep calls `pending.clear_all` between the listing and the stat. A failure in the health
    # path must cost the health output and nothing else.
    try:
        # One `status.collect` per run: `health.run` and `health.record` each compute their own
        # when none is given, which is a full STM read, a pending stat and a vault rglob twice on
        # every /compact. `metrics` is the argument that exists to prevent exactly that.
        metrics = status.collect(cfg)
        journal_file = paths.health_journal_file(cfg)
        checks = health.run(cfg, metrics=metrics,
                            history=journal.tail(journal_file, healthchecks._TREND_WINDOW))
        failure = health.demand(checks)
        if failure:
            # First, because it is the one block that says the others may be untrustworthy.
            blocks.insert(0, ("health_demand", failure))

        # After the blocks are chosen, never before: the record's whole value is stating what was
        # actually emitted, which is the only input `_check_anchor_delivery` has. `journal.append`
        # swallows every exception, so a broken journal costs the record and never the blocks.
        emitted = [name for name, body in blocks if body]
        journal.append(journal_file, health.record(cfg, checks, emitted, metrics=metrics))
    except Exception:
        pass
    return "\n\n".join(body for _name, body in blocks if body)


def run(event: dict, *, root: Path | None = None) -> dict:
    text = build_context(event, root=root)
    if not text:
        return {}
    return {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
    }


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
