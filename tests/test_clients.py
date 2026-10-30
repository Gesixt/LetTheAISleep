from pathlib import Path

import pytest

from lts import clients, transcript


def _transcript(tmp_path: Path) -> Path:
    path = tmp_path / "t.jsonl"
    path.write_text(
        '{"type":"user","uuid":"u1","timestamp":"2026-10-30T10:00:00.000Z",'
        '"message":{"role":"user","content":"hello"}}\n'
        '{"type":"assistant","uuid":"a1","timestamp":"2026-10-30T10:00:01.000Z",'
        '"message":{"role":"assistant","content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )
    return path


def test_no_client_named_means_the_one_that_existed_before_there_were_two():
    assert clients.DEFAULT == "claude_code"
    assert clients.resolve(None).id == "claude_code"


def test_an_unknown_client_is_refused_rather_than_guessed():
    with pytest.raises(clients.UnknownClient) as exc:
        clients.resolve("emacs-doctor")
    # The message has to name what *is* known, or the operator is left guessing in turn.
    assert "emacs-doctor" in str(exc.value)
    assert "claude_code" in str(exc.value)


def test_the_claude_code_adapter_reads_exactly_what_the_parser_reads(tmp_path: Path):
    path = _transcript(tmp_path)
    adapter = clients.resolve("claude_code")
    assert adapter.read_exchanges(path) == transcript.read_exchanges(path)
    assert adapter.measure_context(path) == transcript.measure_context(path)
    assert adapter.default_context_window == transcript.DEFAULT_WINDOW


def test_known_lists_every_registered_client():
    assert clients.known() == ("claude_code",)


def test_the_client_is_read_off_the_command_line_in_both_spellings():
    assert clients.from_argv(["--client", "claude_code"]) == "claude_code"
    assert clients.from_argv(["--client=claude_code"]) == "claude_code"


def test_an_absent_flag_is_the_default_not_an_error():
    assert clients.from_argv([]) == clients.DEFAULT
    assert clients.from_argv(["--something-else"]) == clients.DEFAULT


def test_a_dangling_flag_does_not_raise_in_a_hook():
    # A hook that exits on a malformed argument stops capturing. It must degrade, not die.
    assert clients.from_argv(["--client"]) == clients.DEFAULT


def test_an_empty_client_value_degrades_like_any_other_malformed_line():
    """`--client=` is a malformed command line, not a client named "".

    The likely source is an unset shell variable in the wiring. Passed through, the empty string
    reaches `resolve`, which refuses it — and a refusal costs the hook its turn, which is the
    price reserved for a name that would read a transcript with the wrong parser.
    """
    assert clients.from_argv(["--client="]) == clients.DEFAULT
    assert clients.from_argv(["--client", ""]) == clients.DEFAULT
    # Still refused where it does matter: only the command line degrades.
    with pytest.raises(clients.UnknownClient):
        clients.resolve("")


def test_the_adapter_answers_the_bounded_newest_exchange_question_too(tmp_path: Path):
    """The third seam method, which `healthchecks._check_capture_live` is the only caller of.

    It is on the interface rather than derived from `read_exchanges` because that check runs on
    every prompt: Claude Code answers it from bounded windows at the end of the file, where
    `read_exchanges` would parse the whole transcript.
    """
    path = _transcript(tmp_path)
    adapter = clients.resolve("claude_code")
    for stamp in (None, "2026-10-30T09:00:00.000Z", "2026-10-30T23:00:00.000Z"):
        assert adapter.newest_exchange_after(path, stamp) == \
            transcript.newest_exchange_after(path, stamp), stamp


def test_every_adapter_is_registered_under_its_own_id():
    """The key and the adapter's `id` are one string, which three things already assume.

    `known()` lists the keys, `resolve` looks a key up, and `lts doctor` reports under
    `resolve(...).id` and keys the state directory on it. An adapter registered under a different
    key would be asked about by one name and answer about another: `doctor --client second` would
    report on `claude_code`'s marks, journal and turn state while printing `second`. Nothing in
    `resolve` or `known` enforces it, so this does — for every adapter added after this one.
    """
    assert all(key == adapter.id for key, adapter in clients._REGISTRY.items()), \
        {key: adapter.id for key, adapter in clients._REGISTRY.items()}


def test_the_newest_exchange_contract_holds_clause_by_clause(tmp_path: Path):
    """Each clause the `Client` protocol states about `newest_exchange_after`, measured.

    That comment is the specification a second adapter is written from, and two of its clauses
    decide `ok` against `fail` in `healthchecks._check_capture_live`. Prose cannot drift from the
    reference implementation unnoticed while this holds it to each one.
    """
    adapter = clients.resolve("claude_code")
    dated = _transcript(tmp_path)
    newest = "2026-10-30T10:00:01.000Z"      # the stamp of the last exchange in `dated`

    # Strict: the mark sitting on the newest exchange is the measured normal, not a miss.
    assert adapter.newest_exchange_after(dated, newest) is False
    assert adapter.newest_exchange_after(dated, "2026-10-30T09:00:00.000Z") is True
    # No boundary — a missing mark, or a stamp in no shape this client writes.
    assert adapter.newest_exchange_after(dated, None) is True
    assert adapter.newest_exchange_after(dated, "last tuesday") is True

    undated = tmp_path / "undated.jsonl"
    undated.write_text(
        '{"type":"user","uuid":"u1","message":{"role":"user","content":"hello"}}\n'
        '{"type":"assistant","uuid":"a1","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"hi"}]}}\n',
        encoding="utf-8",
    )
    # "Passed over" is scoped to there being a boundary: with none, the first clause wins, or a
    # missing mark over a live transcript would read as nothing to capture.
    assert adapter.newest_exchange_after(undated, None) is True
    assert adapter.newest_exchange_after(undated, newest) is False

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert adapter.newest_exchange_after(empty, None) is False
    assert adapter.newest_exchange_after(tmp_path / "gone.jsonl", None) is False
