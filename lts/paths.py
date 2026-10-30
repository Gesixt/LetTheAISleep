from __future__ import annotations

import re
from pathlib import Path

from lts.clients import DEFAULT as DEFAULT_CLIENT
from lts.config import Config, require_project

_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def safe_session_id(session_id: str) -> str:
    cleaned = _UNSAFE.sub("_", session_id or "")
    return cleaned or "default"


def sidecar_root(cfg: Config) -> Path:
    return cfg.project_root / ".ai_memory"


def vault_root(cfg: Config) -> Path:
    """The Basic Memory vault directory: `[vault] path`, else `<project_root>/.ai_vault`.

    Unlike the sidecar this may live outside the project (a shared vault), and in team mode
    it is its own git repository, so it is resolved separately from `sidecar_root`.
    """
    if cfg.vault_path:
        return Path(cfg.vault_path).expanduser()
    return cfg.project_root / ".ai_vault"


def stm_file(cfg: Config) -> Path:
    """Single per-project STM buffer (no session dimension — keeps hooks and skills in sync)."""
    return sidecar_root(cfg) / "stm" / "buffer.md"


def anchor_file(cfg: Config) -> Path:
    return sidecar_root(cfg) / "anchor.json"


def sleep_mark_file(cfg: Config) -> Path:
    """How far consolidation has reached in the transcript (see `lts.watermark`)."""
    return sidecar_root(cfg) / "sleep-mark.json"


def legacy_capture_offset_file(cfg: Config) -> Path:
    """Pre-watermark cursor: a bare exchange count. Honoured once, then removed."""
    return sidecar_root(cfg) / "stm" / ".lts-capture-offset"


def sleep_flag_file(cfg: Config) -> Path:
    """Set by `lts stm clear`: a sleep just finished, mid-turn. Cleared by the next hook."""
    return sidecar_root(cfg) / "stm" / ".lts-sleep-armed"


def pending_dir(cfg: Config) -> Path:
    return sidecar_root(cfg) / "pending_consolidation"


def client_state_dir(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """State that belongs to one client's transcript rather than to the project.

    Three things are about a transcript, so two clients wired to one project cannot share them:

    - the **capture mark** names an exchange by uuid *inside one transcript*. Resolved against
      another client's transcript the uuid never matches, and `watermark.exchanges_after` falls
      through to its timestamp branch, which returns only what is newer than the other client's
      position — every older exchange is dropped, permanently, with every health check green. That
      is the signature of the 2026-10-16 incident, and a shared mark would reintroduce it by
      construction rather than by accident;
    - the **turn state** counts prompts and misses within one client's turn sequence;
    - the **health journal** is read by the trend checks as a single history, and two interleaved
      clients make that history describe neither of them.

    The buffer, the anchor, the sleep mark, the sleep latch and the pending snapshots stay shared:
    they are the project's memory, and sharing them is the point of wiring both clients to one
    project. Snapshots are already keyed by session id, so they cannot collide.
    """
    return sidecar_root(cfg) / "clients" / client


def capture_mark_file(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """How far this client's Stop hook has read its transcript into the shared STM buffer."""
    return client_state_dir(cfg, client) / "capture-mark.json"


def snapshot_mark_file(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """How far this client's PreCompact hook has flushed its transcript into pending snapshots.

    Split out of the sleep mark, which was carrying two different claims in one file: "a sleep
    consolidated up to here, and notes exist" and "a snapshot holds this, and no note does". See
    `lts.watermark.later_of`.
    """
    return client_state_dir(cfg, client) / "snapshot-mark.json"


def turn_state_file(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """What the per-turn hook knew at the previous prompt (see `lts.turnstate`).

    Beside the health journal and deliberately not inside it: a record per turn would redefine the
    trend checks' `TREND_WINDOW` from five sessions to five turns.
    """
    return client_state_dir(cfg, client) / "turn-state.json"


def health_journal_file(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """Per-run health records (see `lts.journal`) — the input to the trend checks."""
    return client_state_dir(cfg, client) / "health.jsonl"


# Where these three files lived before the split, relative to the sidecar root.
_FLAT = {
    "capture-mark.json": ("stm", "capture-mark.json"),
    "turn-state.json": ("turn-state.json",),
    "health.jsonl": ("health.jsonl",),
}


def adoption_marker_file(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """Written once `ensure_sidecar` has had its one successful chance to adopt the flat files.

    **The client directory is not evidence that `ensure_sidecar` ever ran.** `journal.append` and
    `turnstate.write` each `mkdir(parents=True)` their own parent, and `SessionStart` and
    `UserPromptSubmit` reach those writers before any hook calls `ensure_sidecar`. Keying adoption
    on the directory therefore means it never happens on a project that already has memory: the
    capture mark stays orphaned at its flat path, `stop.py` reads `{}`, and `exchanges_after`
    replays the entire transcript into the shared buffer.

    A marker also keeps the `lts doctor` remedies honest. They tell an operator to "delete the
    capture mark file"; with only the per-file guard, a flat file that reappeared afterwards would
    be adopted into the gap and resurrect a stale position.
    """
    return client_state_dir(cfg, client) / ".adopted"


def _adopt_flat_state(cfg: Config, client: str = DEFAULT_CLIENT) -> bool:
    """Move the pre-split files into the per-client directory, until one call manages it.

    Only ever for `DEFAULT_CLIENT`: the flat files were written by Claude Code, because it was the
    only client that existed while they were being written. Handing them to a second client would
    hand it a capture mark naming a uuid out of a transcript it has never read — the exact failure
    `client_state_dir` exists to prevent.

    Failures are swallowed rather than raised. Five projects are running this code inside hooks; a
    capture mark that fails to move costs one replay of the transcript into the buffer, which is
    noisy and recoverable, while a raise costs the hook, which captures nothing at all. They are
    *reported*, though: returning False withholds the marker, so the next `ensure_sidecar` tries
    again rather than recording a migration that did not happen.
    """
    root = sidecar_root(cfg)
    state = client_state_dir(cfg, client)
    moved = True
    for name, parts in _FLAT.items():
        legacy = root.joinpath(*parts)
        target = state / name
        if legacy.exists() and not target.exists():
            try:
                legacy.replace(target)
            except OSError:
                moved = False
    return moved


def ensure_sidecar(cfg: Config, client: str = DEFAULT_CLIENT) -> None:
    """Create the sidecar — only ever at a configured project root, never at a bare cwd."""
    require_project(cfg)
    (sidecar_root(cfg) / "stm").mkdir(parents=True, exist_ok=True)
    pending_dir(cfg).mkdir(parents=True, exist_ok=True)
    client_state_dir(cfg, client).mkdir(parents=True, exist_ok=True)
    # Keyed on a marker this function writes, never on the directory: see `adoption_marker_file`.
    # Only the default client adopts — the flat files were written by Claude Code, because it was
    # the only client that existed while they were being written.
    marker = adoption_marker_file(cfg, client)
    if not marker.exists():
        adopted = _adopt_flat_state(cfg, client) if client == DEFAULT_CLIENT else True
        if adopted:
            try:
                marker.touch()
            except OSError:
                pass  # best-effort, like the move itself; the next run retries
