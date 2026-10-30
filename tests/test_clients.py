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
