from __future__ import annotations

import json
from pathlib import Path

from lts import paths, turnstate
from lts.config import load_config
from tests.helpers import make_project


def test_the_state_lives_in_the_sidecar_and_is_not_the_health_journal(tmp_path: Path):
    """Reached only through `lts.paths`, and a different file from the journal.

    Both halves are load-bearing: a hand-typed relative path resolves against the hook's working
    directory and splits memory in two, and a per-turn record in `health.jsonl` would redefine
    `TREND_WINDOW` from five sessions to five turns without touching the trend checks.
    """
    cfg = load_config(make_project(tmp_path))
    state_file = paths.turn_state_file(cfg)
    assert state_file.parent == paths.sidecar_root(cfg)
    assert state_file.name == "turn-state.json"
    assert state_file != paths.health_journal_file(cfg)


def test_a_prompt_with_no_previous_state_is_not_a_miss():
    """With no previous state there is nothing to compare, so `misses` starts at 0.

    Counting such a prompt as a miss would make every project one prompt away from a false
    `capture_live` failure the moment the state file is created or deleted, which is the
    false-demand class this subsystem keeps correcting.

    Note what this is *not*: the file is `sidecar_root(cfg) / "turn-state.json"` — per project, with
    no session dimension and nothing that removes it (checked 2026-10-01 against `lts.paths` and
    every writer of the sidecar) — so it outlives a session. "No previous state" therefore means the
    first prompt ever on this project, not the first prompt of each session. The count inside it is a
    separate question, answered by the session-id tests at the end of this file: the state survives,
    the count restarts.
    """
    state = turnstate.advance({}, capture_mark="2026-10-01T06:00:00.000Z", at="2026-10-01T06:00:01.000Z")
    assert state["misses"] == 0
    assert state["capture_mark"] == "2026-10-01T06:00:00.000Z"
    assert state["prev_at"] is None
    # And with no mark either: without this case the test does not touch the "no previous state"
    # clause at all, because an absent previous mark already differs from a present one. Measured
    # 2026-10-01: mutating `0 if (moved or not had_state)` to `0 if moved` left the assertions above
    # green and only reddened `test_a_mark_that_is_absent_on_both_prompts_is_still_a_miss`.
    assert turnstate.advance({}, capture_mark=None, at="t1")["misses"] == 0


def test_an_unmoved_mark_counts_one_miss_and_a_moved_one_resets():
    first = turnstate.advance({}, capture_mark="m1", at="t1")
    second = turnstate.advance(first, capture_mark="m1", at="t2")
    third = turnstate.advance(second, capture_mark="m1", at="t3")
    assert [second["misses"], third["misses"]] == [1, 2]
    assert second["prev_at"] == "t1"
    moved = turnstate.advance(third, capture_mark="m2", at="t4")
    assert moved["misses"] == 0


def test_a_mark_that_is_absent_on_both_prompts_is_still_a_miss():
    """No mark at all, twice, is the same evidence as an unmoved one: nothing was captured.

    `None == None` is the comparison that makes this work, so it is pinned: a project whose Stop
    hook never ran once has no mark to be unmoved, and must not read as healthy.
    """
    first = turnstate.advance({}, capture_mark=None, at="t1")
    second = turnstate.advance(first, capture_mark=None, at="t2")
    assert [first["misses"], second["misses"]] == [0, 1]


def test_a_mark_that_disappears_resets_instead_of_counting_up():
    """A mark that was there and is now gone is movement, not a miss — and it self-corrects.

    `stop.py` only ever writes the mark file (`watermark.write_mark`), so a mark that vanishes was
    deleted or made unreadable from outside, which is no evidence about the Stop hook. The prompt
    after it compares absent with absent and starts counting (see the test above), so the real
    failure is still reached in two more prompts; treating the disappearance itself as a miss would
    instead let a deletion carry a count it did not earn.
    """
    first = turnstate.advance({}, capture_mark="m1", at="t1")
    second = turnstate.advance(first, capture_mark="m1", at="t2")
    assert second["misses"] == 1
    vanished = turnstate.advance(second, capture_mark=None, at="t3")
    assert vanished["misses"] == 0
    assert turnstate.advance(vanished, capture_mark=None, at="t4")["misses"] == 1


def test_the_shown_fingerprints_survive_a_prompt():
    """`notify` stores what the user has already been told here; advancing must not drop it."""
    first = turnstate.advance({}, capture_mark="m1", at="t1")
    first["shown"] = {"health": "marks"}
    second = turnstate.advance(first, capture_mark="m2", at="t2")
    assert second["shown"] == {"health": "marks"}


def test_a_corrupt_state_file_reads_as_empty_rather_than_raising(tmp_path: Path):
    f = tmp_path / "turn-state.json"
    f.write_text('{"at": "t1", "misse', encoding="utf-8")
    assert turnstate.read(f) == {}
    assert turnstate.read(tmp_path / "absent.json") == {}
    f.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert turnstate.read(f) == {}


def test_a_directory_in_place_of_the_state_file_reads_as_empty(tmp_path: Path):
    """The failure `test_hooks` already pins for the journal: a path that is a directory.

    `json.loads` never gets a chance here — the read raises `IsADirectoryError`, an `OSError`, and
    a hook must survive it.
    """
    d = tmp_path / "turn-state.json"
    d.mkdir()
    assert turnstate.read(d) == {}


def test_a_nonsense_miss_count_restarts_at_zero():
    """A hand-edited or half-written count must not be arithmetic. Garbage in, 0 out."""
    for junk in ("three", -4, None, 1.5, {"n": 1}, True):
        state = turnstate.advance({"capture_mark": "m1", "misses": junk}, capture_mark="m1", at="t2")
        assert state["misses"] == 1, junk


def test_a_previous_state_that_is_not_a_dict_is_ignored():
    """`read` already guarantees a dict, but `advance` is a pure function with other callers."""
    state = turnstate.advance(["junk"], capture_mark="m1", at="t2")  # type: ignore[arg-type]
    assert state == {"at": "t2", "prev_at": None, "capture_mark": "m1", "misses": 0, "shown": {},
                     "session_id": None}


def test_a_missing_at_is_stamped_like_the_journal():
    """`at` defaults to `watermark.mark_at`, so the field matches `journal.append`'s stamps."""
    state = turnstate.advance({}, capture_mark="m1")
    assert state["at"].endswith("Z") and state["at"][4] == "-"


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path: Path):
    f = tmp_path / "turn-state.json"
    turnstate.write(f, {"at": "t1", "misses": 0})
    assert json.loads(f.read_text(encoding="utf-8"))["at"] == "t1"
    assert [p.name for p in tmp_path.iterdir()] == ["turn-state.json"]


def test_the_write_creates_the_sidecar_directory_when_it_is_absent(tmp_path: Path):
    f = tmp_path / "fresh" / "turn-state.json"
    turnstate.write(f, {"at": "t1"})
    assert turnstate.read(f) == {"at": "t1"}


def test_an_unwritable_path_costs_the_state_and_never_raises(tmp_path: Path):
    """Best-effort, like `journal.append`: turn state that could break the hook is not worth having."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        turnstate.write(blocked / "turn-state.json", {"at": "t1"})
    finally:
        blocked.chmod(0o700)
    assert not (blocked / "turn-state.json").exists()


def test_a_state_that_cannot_be_serialised_costs_the_state_and_never_raises(tmp_path: Path):
    """`shown` comes from `notify`; a value `json` cannot encode must not reach the hook as a crash."""
    f = tmp_path / "turn-state.json"
    turnstate.write(f, {"at": "t1", "shown": {"health": object()}})
    assert not f.exists()
    assert [p.name for p in tmp_path.iterdir()] == []


# --- the count must not be inherited across sessions --------------------------------------------
#
# `paths.turn_state_file(cfg)` is per project with no session dimension and nothing removes it, so
# the count outlives a session: a session whose last turn the user interrupted leaves `misses = 1`
# behind, and the first prompt of the next session would inherit it and could reach
# `_CAPTURE_MISS_TOLERANCE` at once. That demand would be premature rather than true — the
# interrupted turn's exchanges are still in the transcript, and the next working `Stop` captures
# them, because `watermark.entries_after` starts from the mark and not from "this turn".
#
# What the caller passes is "a stable identifier for this session". The field name was *not*
# observed in a real `UserPromptSubmit` payload: the evidence is one real `Stop` payload (recorded
# in ~/.claude/history.jsonl, a `/tmp/lts-hook-trace.log` the user pasted into their terminal),
# which carries `session_id`, `transcript_path`, `cwd` and `hook_event_name` — the common envelope
# — plus `claude/hooks/pre_compact.py`, which has read `event.get("session_id", "default")` in
# production since before this branch. So `session_id` is what the hook should read, and if it ever
# turns out not to be there, this function does not care: `transcript_path` is in that same observed
# payload and is per session too, so the hook can pass that instead without a change here.


def test_a_new_session_does_not_inherit_the_previous_sessions_misses():
    """Two unmoved prompts in *different* sessions are not two misses; in the same session they are.

    Both halves in one test on purpose: a reset that fired unconditionally would satisfy the first
    assertion and make the check unable to fail at all.
    """
    first = turnstate.advance({}, capture_mark="m1", at="t1", session_id="s1")
    second = turnstate.advance(first, capture_mark="m1", at="t2", session_id="s1")
    assert second["misses"] == 1
    third = turnstate.advance(second, capture_mark="m1", at="t3", session_id="s1")
    assert third["misses"] == 2, "the same session still reaches the threshold"
    fresh = turnstate.advance(second, capture_mark="m1", at="t3", session_id="s2")
    assert fresh["misses"] == 0
    assert fresh["session_id"] == "s2"
    # And the reset is one prompt deep, not a permanent exemption: the new session counts its own.
    assert turnstate.advance(fresh, capture_mark="m1", at="t4", session_id="s2")["misses"] == 1


def test_a_prompt_that_knows_no_session_id_counts_as_before():
    """`None` is "this caller cannot tell me", not "a new session".

    Resetting on it would disable the count for every caller that has no id to pass — `lts doctor`
    reads this file but never writes it, and a hook whose payload lost the field would silently stop
    the only mid-session detector of a dead `Stop` hook from ever reaching its threshold.
    """
    first = turnstate.advance({}, capture_mark="m1", at="t1")
    second = turnstate.advance(first, capture_mark="m1", at="t2")
    assert [first["misses"], second["misses"], second["session_id"]] == [0, 1, None]


def test_a_state_written_before_session_ids_existed_resets_once(tmp_path: Path):
    """The upgrade path: a state file from the previous commit carries no `session_id` at all.

    One miss of evidence is lost on the first prompt after the upgrade, which is the conservative
    direction — the alternative is a demand raised against a count whose session nobody recorded.
    """
    legacy = {"at": "t1", "prev_at": None, "capture_mark": "m1", "misses": 1, "shown": {}}
    assert turnstate.advance(legacy, capture_mark="m1", at="t2", session_id="s1")["misses"] == 0


def test_the_session_id_survives_a_round_trip_through_the_file(tmp_path: Path):
    f = tmp_path / "turn-state.json"
    turnstate.write(f, turnstate.advance({}, capture_mark="m1", at="t1", session_id="s1"))
    second = turnstate.advance(turnstate.read(f), capture_mark="m1", at="t2", session_id="s1")
    assert second["misses"] == 1, "a session id that does not survive the file resets every prompt"
