import json
from pathlib import Path
from unittest import mock

from lts import transcript


def test_extract_text_from_str_and_blocks():
    assert transcript.extract_text("hello") == "hello"
    blocks = [
        {"type": "thinking", "thinking": "secret"},
        {"type": "text", "text": "visible answer"},
        {"type": "tool_use", "name": "x"},
    ]
    assert transcript.extract_text(blocks) == "visible answer"
    assert transcript.extract_text(None) == ""


def test_read_exchanges(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    lines = [
        {"type": "mode", "mode": "x"},  # metadata — ignored
        {"type": "user", "message": {"role": "user", "content": "study the cart service"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "Cart uses Redis and 3 endpoints."},
        ]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "noise"},
        ]}},  # no text -> skipped
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    ex = transcript.read_exchanges(f)
    assert [e["role"] for e in ex] == ["user", "assistant"]
    assert ex[0]["text"] == "study the cart service"
    assert ex[1]["text"] == "Cart uses Redis and 3 endpoints."


def test_read_exchanges_missing(tmp_path: Path):
    assert transcript.read_exchanges(tmp_path / "nope.jsonl") == []


def test_context_tokens_from_usage(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"role": "assistant", "usage": {
            "input_tokens": 10, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}}},
        # the LAST assistant usage wins (reflects current context)
        {"type": "assistant", "message": {"role": "assistant", "usage": {
            "input_tokens": 2, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 100}}},
    ]
    f.write_text("\n".join(json.dumps(x) for x in lines), encoding="utf-8")
    assert transcript.context_tokens(f) == 2 + 2000 + 100


def test_context_tokens_falls_back_to_char_estimate(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    f.write_text("x" * 400, encoding="utf-8")  # no usage data
    assert transcript.context_tokens(f) == 100  # 400 // 4


def test_estimate_tokens_missing(tmp_path: Path):
    assert transcript.estimate_tokens(tmp_path / "nope.jsonl") == 0


def test_estimate_tokens_approx(tmp_path: Path):
    f = tmp_path / "t.jsonl"
    f.write_text("x" * 400, encoding="utf-8")
    assert transcript.estimate_tokens(f) == 100  # 400 chars / 4


def test_pressure_levels():
    w = 1000
    assert transcript.pressure_level(100, w, 0.6, 0.8) == "none"
    assert transcript.pressure_level(600, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(799, w, 0.6, 0.8) == "warn"
    assert transcript.pressure_level(800, w, 0.6, 0.8) == "force"


def test_read_exchanges_carries_the_entry_uuid_and_timestamp(tmp_path: Path):
    # The sleep watermark points at a specific message, so exchanges must be identifiable.
    t = tmp_path / "t.jsonl"
    t.write_text(json.dumps({
        "type": "user", "uuid": "u1", "timestamp": "2026-08-26T10:00:00Z",
        "message": {"role": "user", "content": "hi"},
    }), encoding="utf-8")
    ex = transcript.read_exchanges(t)[0]
    assert ex["uuid"] == "u1"
    assert ex["timestamp"] == "2026-08-26T10:00:00Z"


def _raw(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return path


def _user(text: str, uuid: str, **extra) -> dict:
    return {"type": "user", "uuid": uuid, "timestamp": "2026-08-26T11:00:00.000Z",
            "message": {"role": "user", "content": text}, **extra}


def test_read_exchanges_drops_slash_command_scaffolding(tmp_path: Path):
    """`/compact` writes four user records; none of them is material worth remembering.

    Left in, they land in the STM buffer and — worse — make PreCompact write a snapshot of
    pure scaffolding right after a sleep, so SessionStart demands a sleep that is not owed.
    """
    t = _raw(tmp_path / "t.jsonl", [
        _user("/compact", "c1"),
        _user("<local-command-caveat>Caveat: the messages below…</local-command-caveat>",
              "c2", isMeta=True),
        _user("<command-name>/compact</command-name>\n<command-args></command-args>", "c3"),
        _user("<local-command-stdout>Compacted</local-command-stdout>", "c4"),
        _user("real question", "c5"),
    ])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["c5"]


def test_read_exchanges_drops_the_compaction_summary(tmp_path: Path):
    # The raw tail it summarises was already captured by the PreCompact snapshot.
    t = _raw(tmp_path / "t.jsonl", [
        _user("This session is being continued from a previous conversation…", "s1",
              isCompactSummary=True, isVisibleInTranscriptOnly=True),
        _user("real question", "s2"),
    ])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["s2"]


def test_read_exchanges_keeps_a_slash_command_that_carries_an_instruction(tmp_path: Path):
    t = _raw(tmp_path / "t.jsonl", [_user("/sleep and then explain the schema", "k1")])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["k1"]


def test_pressure_level_has_no_verdict_on_an_impossible_measurement():
    """More tokens than the window fit is not "full" — it is a measurement that cannot be true.

    `force` on such a reading is a claim about pressure derived from a figure nothing stands
    behind, and `none` would be the same claim in the other direction.
    """
    w = 1000
    assert transcript.pressure_level(1001, w, 0.6, 0.8) == "unknown"
    assert transcript.pressure_level(1000, w, 0.6, 0.8) == "force"     # the boundary still holds


def test_measure_context_says_where_the_number_came_from(tmp_path: Path):
    """The root of the impossible-percentage family: an estimate that looked like a reading.

    `context_tokens` falls back to `len(file) // 4` whenever no usage record is found and returns
    it as an ordinary int, so a caller could not tell a 45,413,420-token file-size estimate from
    a measured window.
    """
    real = tmp_path / "real.jsonl"
    real.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "usage": {
            "input_tokens": 900, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}},
    }), encoding="utf-8")
    assert transcript.measure_context(real) == (900, transcript.USAGE)

    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert transcript.measure_context(empty) == (0, transcript.ESTIMATE)

    junk = tmp_path / "notes.txt"
    junk.write_text("x" * 48, encoding="utf-8")
    assert transcript.measure_context(junk) == (12, transcript.ESTIMATE)

    assert transcript.measure_context(tmp_path / "gone.jsonl") == (0, transcript.NO_FILE)
    # The old entry point keeps working, and asks for no second parse.
    assert transcript.context_tokens(real) == 900
    assert transcript.context_tokens(junk) == 12


def test_a_usage_record_summing_to_zero_is_a_measurement(tmp_path: Path):
    """An empty window that was read is not the same as a window nobody read.

    `return last if last else estimate_tokens(...)` could not tell them apart, and would have
    answered this file with a size estimate labelled as a reading.
    """
    t = tmp_path / "fresh.jsonl"
    t.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "usage": {
            "input_tokens": 0, "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0}},
    }) + "\n" + "padding that would estimate to nonzero tokens" * 10, encoding="utf-8")
    assert transcript.measure_context(t) == (0, transcript.USAGE)


def test_measure_context_never_reads_the_whole_file_twice(tmp_path: Path):
    """At most one full read per call — and none at all once the tail answers.

    `measure_context` read the whole transcript looking for `usage`, and when it found none called
    `estimate_tokens(path)`, which opened the same file again — a second full parse of bytes it had
    just held. Traced 2026-09-30 through `health.run(transcript_path=...)`: 3 reads of the
    transcript for a file without `usage` against 2 for one with it, the extra one from inside
    `measure_context`. On the 208,142,001-byte transcript measured on 2026-09-29 one parse is
    ~1.5 s, so the duplicate was ~1.5 s of the 3-5 s session-load budget of ТЗ §6 spent twice on
    the same bytes.

    The usage branch now reads no whole file at all: `newest_usage` opens the file in binary and
    reads one window from the end, so the count this test used to assert for that branch — exactly
    one — is now zero, and the duplicate it was written to catch cannot come back by either route.
    """
    reads: list[Path] = []
    real = Path.read_text

    def counting(self, *a, **k):
        reads.append(Path(self))
        return real(self, *a, **k)

    with_usage = tmp_path / "usage.jsonl"
    with_usage.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant", "usage": {"input_tokens": 900}},
    }) + "\n", encoding="utf-8")
    without = tmp_path / "no-usage.jsonl"
    without.write_text(json.dumps({
        "type": "user", "message": {"role": "user", "content": "x" * 400},
    }) + "\n", encoding="utf-8")

    with mock.patch.object(Path, "read_text", counting):
        assert transcript.measure_context(with_usage) == (900, transcript.USAGE)
        assert [p for p in reads if p == with_usage] == []
        reads.clear()
        tokens, source = transcript.measure_context(without)
        assert source == transcript.ESTIMATE and tokens == without.stat().st_size // 4
        assert [p for p in reads if p == without] == [without]


def _tail_assistant(uuid: str, when: str, *, input_tokens: int, cache_read: int = 0) -> str:
    return json.dumps({
        "uuid": uuid, "timestamp": when, "type": "assistant",
        "message": {"role": "assistant", "content": "x",
                    "usage": {"input_tokens": input_tokens,
                              "cache_read_input_tokens": cache_read}},
    })


def _tail_filler(uuid: str, when: str, *, pad: int = 0) -> str:
    return json.dumps({
        "uuid": uuid, "timestamp": when, "type": "user",
        "message": {"role": "user", "content": "y" * pad},
    })


def test_newest_usage_reads_the_tail_not_the_whole_file(tmp_path: Path):
    """The newest `usage` record is at the end of an append-only file, so only the end is read.

    The spec's §7.2 records the prototype's figures on the real ppss transcript (208,143,252 B):
    1.529 s for the full parse, 0.0003 s for the tail, 228,889 tokens from both. Re-verified here
    on 2026-10-01 against the five largest real transcripts (7.4 MB to 208 MB), tail parse against
    the pre-change loop over the *same* bytes: identical totals in all five, 0.0002-0.0003 s against
    0.018-0.697 s, and ppss still at 228,889. What this test owns is that agreement, which is the
    part a unit test can pin; the speed belongs to the measurements above.
    """
    t = tmp_path / "t.jsonl"
    lines = [_tail_filler(f"u{i}", "2026-10-01T06:00:00.000Z", pad=2000) for i in range(200)]
    lines.insert(0, _tail_assistant("a-old", "2026-10-01T05:00:00.000Z", input_tokens=11))
    lines.append(
        _tail_assistant("a-new", "2026-10-01T07:00:00.000Z", input_tokens=4000, cache_read=1000))
    t.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert t.stat().st_size > transcript._TAIL_WINDOW, "fixture must exceed one window"
    assert transcript.newest_usage(t) == 5000
    assert transcript.measure_context(t) == (5000, transcript.USAGE)


def test_newest_usage_widens_its_window_until_it_finds_a_record(tmp_path: Path):
    """A newest record further back than the first window must still be found, not missed.

    Without the widening this returns None and `measure_context` falls back to the size estimate —
    a figure of a different kind, silently, for a file that does carry a measurement.
    """
    t = tmp_path / "t.jsonl"
    filler = [_tail_filler(f"u{i}", "2026-10-01T06:00:00.000Z", pad=4000) for i in range(200)]
    t.write_text("\n".join([_tail_assistant("a1", "2026-10-01T05:00:00.000Z", input_tokens=777)]
                           + filler) + "\n", encoding="utf-8")
    assert t.stat().st_size > transcript._TAIL_WINDOW, "fixture must exceed one window"
    assert transcript.newest_usage(t) == 777


def test_a_file_with_no_usage_record_still_falls_back_to_the_estimate(tmp_path: Path):
    """`ESTIMATE` must keep computing `len(text) // 4`, unchanged, byte for byte.

    The tail scan can only conclude "no usage record here"; concluding "therefore estimate from the
    size" without reading the file would change a figure that four surfaces print.
    """
    t = tmp_path / "junk.jsonl"
    t.write_text("x" * 48, encoding="utf-8")
    assert transcript.newest_usage(t) is None
    assert transcript.measure_context(t) == (12, transcript.ESTIMATE)


def test_a_truncated_final_line_does_not_hide_the_record_before_it(tmp_path: Path):
    """A hook killed mid-write leaves a partial last line. The record before it is still the newest."""
    t = tmp_path / "t.jsonl"
    t.write_text(_tail_assistant("a1", "2026-10-01T06:00:00.000Z", input_tokens=321) + "\n"
                 + '{"uuid": "a2", "type": "assist', encoding="utf-8")
    assert transcript.newest_usage(t) == 321


def test_newest_usage_is_none_for_a_file_that_is_not_there(tmp_path: Path):
    assert transcript.newest_usage(tmp_path / "nope.jsonl") is None
    assert transcript.measure_context(tmp_path / "nope.jsonl") == (0, transcript.NO_FILE)


def test_the_fragment_the_window_cut_in_half_is_not_read_as_a_record(tmp_path: Path):
    """The first line of a tail window is half a record, and must not be read as a whole one.

    None of the fixtures above can fail if the window keeps that fragment: a half-written record
    almost never parses. This one is built so that it does. Byte arithmetic puts the opening brace
    of a decoy `usage` object exactly on the window boundary, so the fragment the window begins
    with is valid JSON on its own — 999 tokens that were never a record. The real newest record is
    321, one window further back, and dropping the fragment is what makes the scan widen to it.
    """
    decoy = b'{"type": "assistant", "message": {"usage": {"input_tokens": 999}}}'
    real = _tail_assistant("a1", "2026-10-01T06:00:00.000Z", input_tokens=321).encode("utf-8")
    pad_open = b'{"type": "user", "message": {"role": "user", "content": "'
    pad_close = b'"}}\n'
    # The window starts at `size - _TAIL_WINDOW`, so everything from the decoy's brace to EOF has
    # to be exactly one window long for that boundary to land on the brace.
    pad = transcript._TAIL_WINDOW - len(decoy) - 1 - len(pad_open) - len(pad_close)
    tail = decoy + b"\n" + pad_open + b"y" * pad + pad_close
    assert len(tail) == transcript._TAIL_WINDOW
    # The decoy's line read whole is not valid JSON — only the half the window keeps is.
    t = tmp_path / "t.jsonl"
    t.write_bytes(real + b"\n" + pad_open + tail)

    assert transcript.newest_usage(t) == 321


def test_a_newest_usage_record_summing_to_zero_wins_over_an_older_one(tmp_path: Path):
    """Zero is a reading, so the tail scan has to stop at it instead of looking further back.

    `_usage_total` answers None for "not a usage record" and 0 for "a window measured as empty", and
    a scan that tests its result for truth rather than for None walks straight past a freshly
    compacted context and reports the previous turn's 4,096 tokens. The full-read fallback hides
    that mutation — it runs to the end of the file either way and the last record still wins — so
    this fixture leaves it nowhere to hide: both records are USAGE, and only the tail's own choice
    decides which figure comes back.
    """
    t = tmp_path / "t.jsonl"
    t.write_text("\n".join([
        _tail_assistant("a-old", "2026-10-01T05:00:00.000Z", input_tokens=96, cache_read=4000),
        _tail_assistant("a-new", "2026-10-01T06:00:00.000Z", input_tokens=0),
    ]) + "\n", encoding="utf-8")
    assert transcript.newest_usage(t) == 0
    assert transcript.measure_context(t) == (0, transcript.USAGE)


# --- newest_exchange_after ----------------------------------------------------------------------
#
# The bounded form of the question `watermark.entries_after` answers exhaustively. Every test below
# compares it against `read_exchanges` where it can, because a second, looser notion of "an
# exchange" in the same module would be a defect of its own: `_check_capture_live` turns a `True`
# here into a `fail` that says the Stop hook is dead, and `stop.py` decides what to capture from
# `read_exchanges`. If the two disagree about what counts, the check accuses a hook of skipping a
# record the hook was never going to capture.


def _exchange(uuid: str, when: str, *, role: str = "assistant", text: str = "hello") -> dict:
    return {"type": role, "uuid": uuid, "timestamp": when,
            "message": {"role": role, "content": [{"type": "text", "text": text}]}}


def _any_newer_per_read_exchanges(path: Path, stamp: str) -> bool:
    """The same question, answered the slow exhaustive way — `exchanges_after`'s timestamp branch."""
    return any((e.get("timestamp") or "") > stamp for e in transcript.read_exchanges(path))


def test_newest_exchange_after_agrees_with_read_exchanges_on_every_boundary(tmp_path: Path):
    """The agreement is the contract, so it is checked at every boundary the fixture offers.

    Not a sampled pair of marks: the two implementations are compared at a stamp before, equal to
    and after each exchange in the file, which is every answer the function has. `read_exchanges`
    is the definition; this function is an optimisation of it that must not also be a redefinition.
    """
    t = _raw(tmp_path / "t.jsonl", [
        _exchange("e1", "2026-10-01T05:00:00.000Z", role="user", text="question"),
        _exchange("e2", "2026-10-01T06:00:00.000Z"),
        _user("/compact", "c1"),                                   # scaffolding: a bare command
        _exchange("e3", "2026-10-01T07:00:00.000Z", role="user", text="next"),
        _exchange("e4", "2026-10-01T08:00:00.000Z"),
    ])
    boundaries = [
        "2026-10-01T04:00:00.000Z",
        "2026-10-01T05:00:00.000Z", "2026-10-01T05:30:00.000Z",
        "2026-10-01T06:00:00.000Z", "2026-10-01T06:30:00.000Z",
        "2026-10-01T07:00:00.000Z", "2026-10-01T07:30:00.000Z",
        "2026-10-01T08:00:00.000Z", "2026-10-01T09:00:00.000Z",
    ]
    for stamp in boundaries:
        assert transcript.newest_exchange_after(t, stamp) == _any_newer_per_read_exchanges(t, stamp), \
            stamp
    # And the two ends of that list are the two answers the check acts on, named outright so a
    # fixture that stopped producing both would not pass this test by producing one twice.
    assert transcript.newest_exchange_after(t, "2026-10-01T04:00:00.000Z") is True
    assert transcript.newest_exchange_after(t, "2026-10-01T08:00:00.000Z") is False


def test_a_mark_at_the_newest_exchange_has_nothing_after_it(tmp_path: Path):
    """The ordinary state between a turn ending and the next prompt: `Stop` captured everything.

    This is the state that must not read as a dead hook, and it is the one `stop.py` leaves behind
    on every successful turn — it writes `watermark.mark_of`, which names the newest exchange.
    """
    t = _raw(tmp_path / "t.jsonl", [
        _exchange("e1", "2026-10-01T05:00:00.000Z"),
        _exchange("e2", "2026-10-01T06:00:00.000Z"),
    ])
    mark = transcript.read_exchanges(t)[-1]["timestamp"]
    assert transcript.newest_exchange_after(t, mark) is False


def test_scaffolding_newer_than_the_mark_is_not_something_to_capture(tmp_path: Path):
    """State 2 of `stop.py:65-75`: a turn whose records are all plumbing moves no mark.

    `mark_of` names the newest *exchange*, so a turn that produced only a bare slash command, an
    isMeta caveat and a compaction summary writes the same mark value back — `Stop` ran and was
    correct. Counting those records as work would make this check demand attention from a working
    hook, which is the false-demand class the amended design exists to remove.
    """
    t = _raw(tmp_path / "t.jsonl", [
        _exchange("e1", "2026-10-01T05:00:00.000Z"),
        {**_user("/compact", "c1"), "timestamp": "2026-10-01T06:00:00.000Z"},
        {**_user("<local-command-stdout>Compacted</local-command-stdout>", "c2"),
         "timestamp": "2026-10-01T06:00:01.000Z"},
        {**_user("summary of the session so far", "c3", isCompactSummary=True),
         "timestamp": "2026-10-01T06:00:02.000Z"},
        {"type": "user", "uuid": "c4", "timestamp": "2026-10-01T06:00:03.000Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "noise"}]}},
    ])
    assert [e["uuid"] for e in transcript.read_exchanges(t)] == ["e1"]
    assert transcript.newest_exchange_after(t, "2026-10-01T05:00:00.000Z") is False


def test_a_mark_that_cannot_be_ordered_leaves_no_boundary_to_be_newer_than(tmp_path: Path):
    """`None`, an empty string and a stamp of another shape: everything in the file qualifies.

    The comparison is lexicographic, as `watermark.exchanges_after` documents, and that is only
    chronological within one ISO-8601 shape. `"2026-10-01T…" > "whenever"` is False — so a mark of
    another shape, left to the comparison, would quietly answer "nothing to capture" and turn a
    dead hook into an `ok`. There is no boundary here, so the answer is the one the caller can act
    on, and the broken mark itself is the `marks` check's subject, not this one's.
    """
    t = _raw(tmp_path / "t.jsonl", [_exchange("e1", "2026-10-01T05:00:00.000Z")])
    for stamp in (None, "", "whenever", "2026-10-01", 7):
        assert transcript.newest_exchange_after(t, stamp) is True, stamp


def test_a_file_with_nothing_in_it_answers_that_nothing_is_newer(tmp_path: Path):
    """No file, an empty file, and a file of pure scaffolding: nothing observed is newer.

    `False` and not `True`, even for the absent file: this is state 1 of `stop.py:65-75` — an
    unreadable transcript, where the `if here:` guard deliberately leaves the mark alone rather
    than replaying the whole file — and a hook that was handed nothing did not fail to capture it.
    """
    assert transcript.newest_exchange_after(tmp_path / "nope.jsonl", None) is False
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    assert transcript.newest_exchange_after(empty, None) is False
    scaffold = _raw(tmp_path / "s.jsonl", [_user("/sleep", "c1")])
    assert transcript.read_exchanges(scaffold) == []
    assert transcript.newest_exchange_after(scaffold, None) is False


def test_an_exchange_further_back_than_one_window_is_still_found(tmp_path: Path):
    """The widening is shared with `newest_usage`, and the `True` answer depends on it.

    Without it a tail full of tool results — a long run of tool calls, which is an ordinary way for
    a turn to end — would answer "nothing newer" and hide a dead Stop hook.
    """
    t = tmp_path / "t.jsonl"
    filler = [json.dumps({"type": "user", "uuid": f"f{i}", "timestamp": "2026-10-01T07:00:00.000Z",
                          "message": {"role": "user",
                                      "content": [{"type": "tool_result", "content": "y" * 4000}]}})
              for i in range(200)]
    t.write_text("\n".join([json.dumps(_exchange("e1", "2026-10-01T06:00:00.000Z"))] + filler)
                 + "\n", encoding="utf-8")
    assert t.stat().st_size > transcript._TAIL_WINDOW, "fixture must exceed one window"
    assert transcript.newest_exchange_after(t, "2026-10-01T05:00:00.000Z") is True


def test_the_scan_stops_at_the_first_exchange_the_mark_covers(tmp_path: Path):
    """Why the answer is cheap: `False` is reached from the end of the file, not from its start.

    An append-only transcript is chronological, so the first exchange at or before the mark ends the
    question — everything before it is older still. Without that exit the ordinary per-turn case
    (the mark sitting on the newest exchange) would read every widening window up to `_TAIL_CAP`,
    ~85 MiB, on every prompt. Measured here by counting the windows the scan opens.
    """
    t = tmp_path / "t.jsonl"
    pad = json.dumps({"type": "user", "uuid": "p", "timestamp": "2026-10-01T01:00:00.000Z",
                      "message": {"role": "user", "content": "y" * 300_000}})
    t.write_text("\n".join([pad, json.dumps(_exchange("e1", "2026-10-01T06:00:00.000Z"))]) + "\n",
                 encoding="utf-8")
    assert t.stat().st_size > transcript._TAIL_WINDOW, "fixture must exceed one window"
    reads = []
    real_open = Path.open

    def counting_open(self, *a, **kw):
        if self == t:
            reads.append(1)
        return real_open(self, *a, **kw)

    with mock.patch.object(Path, "open", counting_open):
        assert transcript.newest_exchange_after(t, "2026-10-01T06:00:00.000Z") is False
    assert len(reads) == 1, f"the scan opened {len(reads)} windows for an answer the first one held"
