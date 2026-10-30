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
    """How far consolidation has reached — a claim that notes exist (see `lts.watermark`).

    Written only by a sleep: the armed branches of `Stop` and `PreCompact`. What `PreCompact`
    flushes into a pending snapshot goes to `snapshot_mark_file` instead, which is a different
    claim and, unlike this one, is per client.
    """
    return sidecar_root(cfg) / "sleep-mark.json"


def legacy_capture_offset_file(cfg: Config) -> Path:
    """Pre-watermark cursor: a bare exchange count. Honoured once, then removed."""
    return sidecar_root(cfg) / "stm" / ".lts-capture-offset"


def sleep_flag_file(cfg: Config) -> Path:
    """Set by `lts stm clear`: a sleep just finished, mid-turn. Cleared by the next hook."""
    return sidecar_root(cfg) / "stm" / ".lts-sleep-armed"


def pending_dir(cfg: Config) -> Path:
    return sidecar_root(cfg) / "pending_consolidation"


def clients_root(cfg: Config) -> Path:
    """The parent of the per-client state directories (see `client_state_dir`)."""
    return sidecar_root(cfg) / "clients"


def client_ids_on_disk(cfg: Config) -> list[str]:
    """The clients this sidecar holds state for, as the directory names under `clients_root`.

    Read off the disk rather than from `lts.clients.known()`: a directory left behind by a client
    that is no longer registered still holds a snapshot mark, and the caller this exists for —
    `lts pending clear`, resetting the claim the snapshots it has just deleted were backing — has
    to reach that one too. An absent or unreadable `clients/` directory answers with an empty
    list; that is also what a project still on the pre-split flat layout looks like.
    """
    try:
        return sorted(entry.name for entry in clients_root(cfg).iterdir() if entry.is_dir())
    except OSError:
        return []


def client_state_dir(cfg: Config, client: str = DEFAULT_CLIENT) -> Path:
    """State that belongs to one client's transcript rather than to the project.

    Four things are about a transcript, so two clients wired to one project cannot share them:

    - the **capture mark** names an exchange by uuid *inside one transcript*. Resolved against
      another client's transcript the uuid never matches, and `watermark.exchanges_after` falls
      through to its timestamp branch, which returns only what is newer than the other client's
      position — every older exchange is dropped, permanently, with every health check green. That
      is the signature of the 2026-10-16 incident, and a shared mark would reintroduce it by
      construction rather than by accident;
    - the **snapshot mark** names a position the same way, and the same fall-through would let one
      client's compaction declare the other's unsnapshotted chapters already flushed;
    - the **turn state** counts prompts and misses within one client's turn sequence;
    - the **health journal** is read by the trend checks as a single history, and two interleaved
      clients make that history describe neither of them.

    The buffer, the anchor, the sleep mark, the sleep latch and the pending snapshots stay shared:
    they are the project's memory, and sharing them is the point of wiring both clients to one
    project. Snapshots are already keyed by session id, so they cannot collide.

    **The sleep latch is the one of those whose sharing is not sound for two clients.** It is
    recorded here and deliberately not fixed. `/sleep` in client B arms it; if a turn ends in
    client A within `watermark.SLEEP_FLAG_MAX_AGE`, A's `Stop` takes the armed branch — it writes
    A's capture mark to `mark_at(now)`, writes the shared sleep mark, disarms and returns without
    capturing its own turn. A's turn is discarded *and* A's mark jumps past it, so nothing captures
    it later and B's notes never held it. The `PreCompact` armed branch has the mirror shape.
    Nothing emits `--client` yet, so there is exactly one client and the state is unreachable
    today; it becomes reachable with the first wiring that declares a second. The resolutions — a
    per-client latch, or an armed branch that captures its own tail before disarming — are design
    decisions, not something this split settled.
    """
    return clients_root(cfg) / client


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
    `client_state_dir` exists to prevent. The guard is the first line of the body, so the
    invariant lives in the function that states it. It answers `True`: there was nothing here to
    adopt, which is not a failure worth retrying, so the caller may record the marker.

    Failures are swallowed rather than raised. Five projects are running this code inside hooks; a
    capture mark that fails to move costs one replay of the transcript into the buffer, which is
    noisy and recoverable, while a raise costs the hook, which captures nothing at all. They are
    *reported*, though: returning False withholds the marker, so the next `ensure_sidecar` tries
    again rather than recording a migration that did not happen.
    """
    if client != DEFAULT_CLIENT:
        return True
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
    # The old sleep mark was the frontier PreCompact worked from, so it is this client's starting
    # snapshot mark. This does **not** change day one: the sleep mark is deliberately left in
    # place, and `watermark.later_of({}, sleep)` is the same frontier as `later_of(copy, sleep)`,
    # so the hook behaves identically with or without the copy. What it buys is a snapshot mark
    # that exists from day one rather than an absent file that happens to resolve the same way —
    # a position for any later reader of this mark, and a record of where the split left each
    # upgraded project.
    #
    # The sleep mark itself is left exactly as it is: clearing its claim would make
    # `sleep_evidence` announce that nothing is consolidated across five live projects, which is a
    # manufactured demand, and a false demand is what trains demands away. It becomes time-only by
    # itself at the next real sleep.
    sleep_mark = sleep_mark_file(cfg)
    snapshot = snapshot_mark_file(cfg, client)
    if sleep_mark.exists() and not snapshot.exists():
        try:
            snapshot.write_text(sleep_mark.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
    return moved


def ensure_sidecar(cfg: Config, client: str = DEFAULT_CLIENT) -> None:
    """Create the sidecar — only ever at a configured project root, never at a bare cwd."""
    require_project(cfg)
    (sidecar_root(cfg) / "stm").mkdir(parents=True, exist_ok=True)
    pending_dir(cfg).mkdir(parents=True, exist_ok=True)
    client_state_dir(cfg, client).mkdir(parents=True, exist_ok=True)
    # Keyed on a marker this function writes, never on the directory: see `adoption_marker_file`.
    # Only the default client has flat files to adopt, and `_adopt_flat_state` enforces that
    # itself rather than being trusted to be called correctly.
    marker = adoption_marker_file(cfg, client)
    if not marker.exists():
        adopted = _adopt_flat_state(cfg, client)
        if adopted:
            try:
                marker.touch()
            except OSError:
                pass  # best-effort, like the move itself; the next run retries
