from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lts import anchor, digest, paths, pending, stm
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
    blocks = []
    if snapshots or stm_entries:
        blocks.append(_FORCE_MSG.format(stm=stm_entries, snapshots=len(snapshots)))

    # The digest scans the vault (e.g. note.stat()), which can raise on a dangling symlink or
    # a mid-scan race. That must never suppress the anchor, so degrade the digest to "".
    try:
        digest_block = digest.render(digest.collect(cfg))
    except Exception:
        digest_block = ""
    blocks += [
        anchor.render_anchor(anchor.read_anchor(paths.anchor_file(cfg))),
        digest_block,
    ]
    return "\n\n".join(b for b in blocks if b)


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
