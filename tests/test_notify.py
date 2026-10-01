from __future__ import annotations

import re

from lts import notify
from lts.healthchecks import Check

# Every percentage in a rendered line, as an integer. The notification is the second surface that
# prints one (`claude/hooks/user_prompt_submit._context_line` is the first), and both are held to
# the same rule: a percentage above 100 is never printed.
_PERCENT = re.compile(r"(\d+)%")


def _metrics(*, stm: int = 0, snapshots: int = 0, tokens: int = 0,
             window: int = 1_000_000, level: str = "none") -> dict:
    """A `status.collect` metrics dict, in the shape read 2026-10-01 at `lts/status.py:45-54`.

    Only the three sub-dicts this module reads are filled; `collect` also returns `project`,
    `author`, `vault_path` and `anchor`, which notify never looks at.
    """
    return {
        "stm": {"lines": stm, "bytes": stm * 100, "approx_tokens": stm * 20},
        "pending": {"snapshots": snapshots, "bytes": 0},
        "pressure": {"tokens": tokens, "window": window, "ratio": tokens / window if window else 0,
                     "level": level, "source": "usage"},
    }


def _ok(check_id: str) -> Check:
    return Check(check_id, "ok", "fine")


def test_a_healthy_quiet_project_says_nothing():
    """An empty notification every turn would be the noise this design exists to avoid."""
    text, shown = notify.message([_ok("config"), _ok("marks")], _metrics(), {})
    assert text == ""
    assert shown == {}


def test_a_failing_check_names_the_check_and_what_to_run():
    checks = [_ok("config"), Check("capture_live", "fail", "the capture mark has not moved", "look here")]
    text, shown = notify.message(checks, _metrics(), {})
    assert "capture_live" in text
    assert "lts doctor" in text
    assert shown["health"] == "capture_live"


def test_warn_and_skip_never_reach_the_user():
    """The memory-health design's rule, unchanged: a warning that interrupts teaches the reader to
    ignore it, and failures are ignored along with it. Only `fail` is shown (spec §6.1).
    """
    checks = [Check("vault", "warn", "vault is empty"), Check("capture", "skip", "no transcript")]
    text, _ = notify.message(checks, _metrics(), {})
    assert text == ""


def test_the_same_failure_is_not_repeated_on_the_next_turn():
    checks = [Check("marks", "fail", "the sleep mark is ahead", "run /sleep")]
    first, shown = notify.message(checks, _metrics(), {})
    assert first
    second, shown2 = notify.message(checks, _metrics(), shown)
    assert second == ""
    assert shown2 == shown


def test_a_different_failing_check_is_announced_again():
    first, shown = notify.message([Check("marks", "fail", "m", "f")], _metrics(), {})
    assert first
    second, _ = notify.message(
        [Check("marks", "fail", "m", "f"), Check("hooks", "fail", "h", "f")], _metrics(), shown)
    assert "hooks" in second


def test_the_same_failures_in_a_different_order_are_one_fingerprint():
    """The fingerprint is the *sorted* ids (spec §6.2), so the check order must not re-announce.

    `healthchecks.all_checks` fixes its order (`lts/healthchecks.py:111-176`), but `ids=` selects a
    subset and the per-turn caller passes `PER_TURN_IDS`, so which failures appear together — and in
    which order a future registry edit puts them — is not this module's to rely on. Without the sort,
    the identical pair of failures would print again on the next turn, which is the wallpaper the
    fingerprint exists to prevent.
    """
    pair = [Check("hooks", "fail", "h", "f"), Check("marks", "fail", "m", "f")]
    first, shown = notify.message(pair, _metrics(), {})
    assert "hooks" in first and "marks" in first
    reordered, shown2 = notify.message(list(reversed(pair)), _metrics(), shown)
    assert reordered == "", "the same two failures, listed the other way round, are not news"
    assert shown2 == shown


def test_a_failure_that_clears_and_returns_is_announced_again():
    """A cleared subject is forgotten, so its return is news rather than a repeat.

    This is the whole behaviour of the fingerprint store in one test, and it is written as a sequence
    because each step's meaning depends on the one before: announce, stay quiet, clear, announce
    again. Asserting only the first two would leave the store free to remember a subject for ever,
    which would silence the second occurrence of a real failure.
    """
    failing = [Check("marks", "fail", "the sleep mark is ahead", "run /sleep")]
    first, shown = notify.message(failing, _metrics(), {})
    assert "marks" in first
    assert shown == {"health": "marks"}

    repeat, shown = notify.message(failing, _metrics(), shown)
    assert repeat == "", "an unchanged failure must not be restated"

    cleared, shown = notify.message([_ok("marks")], _metrics(), shown)
    assert cleared == "", "nothing is owed when nothing is wrong"
    assert shown == {}, "a subject with nothing to say is forgotten, not remembered as empty"

    again, shown = notify.message(failing, _metrics(), shown)
    assert "marks" in again, "the same failure returning is news again"


def test_a_malformed_fingerprint_store_is_treated_as_empty():
    """The store arrives from a file, so it can be any JSON — and this must not raise.

    `turnstate.advance` normalises `shown` to a dict (`lts/turnstate.py:98-105`), but `turnstate.read`
    returns whatever the file held, and `lts doctor` reads that file without advancing it. A hook that
    died on a hand-edited state file would take the memory-health report down with it, which is the
    failure `healthchecks._answered` and `hooklog.log_error` are both built around.
    """
    failing = [Check("marks", "fail", "m", "f")]
    for broken in (None, "marks", 7, ["marks"]):
        text, shown = notify.message(failing, _metrics(), broken)
        assert "marks" in text, broken
        assert shown == {"health": "marks"}, broken


def test_the_sleep_line_repeats_only_when_the_backlog_grows_by_an_order_of_magnitude():
    """Nine more entries is not news; ten times as many is. A line per entry is wallpaper."""
    first, shown = notify.message([_ok("config")], _metrics(stm=8), {})
    assert "8" in first
    same, shown = notify.message([_ok("config")], _metrics(stm=9), shown)
    assert same == ""
    grown, shown = notify.message([_ok("config")], _metrics(stm=40), shown)
    assert "40" in grown


def test_the_pressure_line_repeats_once_per_ten_percent_band():
    first, shown = notify.message([_ok("config")], _metrics(tokens=800_000, level="force"), {})
    assert "80%" in first
    same, shown = notify.message([_ok("config")], _metrics(tokens=840_000, level="force"), shown)
    assert same == ""
    higher, shown = notify.message([_ok("config")], _metrics(tokens=910_000, level="force"), shown)
    assert "91%" in higher


def test_pressure_below_force_is_not_announced():
    """`warn` is the model's business; the user is told when action is actually owed (spec §6.1)."""
    text, _ = notify.message([_ok("config")], _metrics(tokens=650_000, level="warn"), {})
    assert text == ""


def test_an_impossible_percentage_is_never_printed():
    """The rule `_context_line` already enforces, in the one other place a percentage appears.

    The account of the real reading is in `healthchecks._check_pressure`: 45,413,420 estimated tokens
    against a hardcoded 200,000 window, i.e. 22,707%, while the live session held 301,343 of its
    configured 1,000,000. (The figure `lts pressure` printed beside it, "9900%", followed from no
    window at all — so "it printed 22,707%" is not what was observed, and the number is a
    recomputation from the recorded token count.) A notification is the last place to repeat it.

    `transcript.pressure_level` returns "unknown", not "force", for `tokens > window`, so this exact
    dict cannot come out of `status.collect` — it is hand-built here, as a caller that builds its own
    metrics would build it, and the guard is what keeps the rule true for such a caller.
    """
    text, _ = notify.message([_ok("config")],
                             _metrics(tokens=2_000_000, window=1_000_000, level="force"), {})
    assert [int(p) for p in _PERCENT.findall(text) if int(p) > 100] == []
    assert "200%" not in text


def test_no_line_ever_prints_a_percentage_above_one_hundred():
    """The same rule over the whole input space notify can be handed, not one example of it."""
    for tokens, window in ((2_000_000, 1_000_000), (1_000_001, 1_000_000),
                           (45_413_420, 200_000), (10, 0), (10, -1)):
        text, _ = notify.message([_ok("config")],
                                 _metrics(tokens=tokens, window=window, level="force"), {})
        assert [int(p) for p in _PERCENT.findall(text) if int(p) > 100] == [], (tokens, window)


def test_every_line_states_a_quantity():
    """The requirement the ten checks are held to, applied to what the user reads (spec §6.3).

    A notification saying only "something is wrong" cannot be acted on and cannot be verified.
    """
    checks = [Check("marks", "fail", "m", "f")]
    text, _ = notify.message(checks, _metrics(stm=12, tokens=900_000, level="force"), {})
    assert len([line for line in text.splitlines() if line.strip()]) == 3, text
    for line in [line for line in text.splitlines() if line.strip()]:
        assert any(ch.isdigit() for ch in line), line


def test_every_subject_is_named_in_SUBJECTS():
    """`SUBJECTS` is published for the hooks, so it must be the full domain of `current`'s keys.

    Task 5 and Task 6 iterate it to decide what to keep in the turn state; a subject `current` can
    emit but `SUBJECTS` does not name would be dropped from the store and then announced every turn,
    and a name in `SUBJECTS` that `current` never emits would be a dead key nothing clears.
    """
    speaking = notify.current(
        [Check("marks", "fail", "m", "f")],
        _metrics(stm=3, snapshots=1, tokens=900_000, level="force"),
    )
    assert set(speaking) == set(notify.SUBJECTS)
    assert set(notify.current([_ok("marks")], _metrics())) == set()


def test_current_returns_a_fingerprint_and_a_line_for_each_speaking_subject():
    """The pair, in that order, is the published interface Task 5 stores and prints."""
    speaking = notify.current([Check("marks", "fail", "m", "f")], _metrics())
    fingerprint, line = speaking["health"]
    assert fingerprint == "marks"
    assert line.startswith("Memory health:")
