from __future__ import annotations

import json
import re
from pathlib import Path

DEFAULT_WINDOW = 200_000

# Claude Code writes its own bookkeeping into the transcript as `user` records. A single
# `/compact` produces four of them (the bare command, an isMeta caveat, a <command-name>
# block and the <local-command-stdout>), plus an isCompactSummary record afterwards. None of
# it is material: left in, it fills the STM buffer and makes PreCompact snapshot pure
# scaffolding right after a sleep — an "unfinished sleep" that was in fact finished.
_SCAFFOLD_FLAGS = ("isMeta", "isCompactSummary", "isVisibleInTranscriptOnly")
_SCAFFOLD_PREFIXES = ("<command-name>", "<command-message>", "<local-command-stdout>",
                      "<local-command-caveat>")
# A slash command with no arguments carries no content of its own; one with arguments does.
_BARE_SLASH_COMMAND = re.compile(r"^/[A-Za-z][\w:-]*$")


def is_scaffolding(record: dict, role: str, text: str) -> bool:
    """True when this transcript record is Claude Code's plumbing rather than the dialogue."""
    if any(record.get(flag) for flag in _SCAFFOLD_FLAGS):
        return True
    if text.startswith(_SCAFFOLD_PREFIXES):
        return True
    return role == "user" and bool(_BARE_SLASH_COMMAND.match(text))



def extract_text(content) -> str:
    """Human-readable text from a transcript message's content (str, or list of blocks).

    For block lists, only `text` blocks are kept — thinking and tool_use/tool_result are dropped.
    """
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p).strip()
    return ""


def read_exchanges(transcript_path: Path) -> list[dict]:
    """Ordered user/assistant exchanges with non-empty text.

    Each entry is `{"role", "text", "uuid", "timestamp"}`. The uuid/timestamp identify the
    exchange inside the transcript, which is what lets the sleep watermark say "consolidation
    reached here" in a file that keeps growing across `--resume`.
    """
    if not transcript_path.exists():
        return []
    out: list[dict] = []
    for line in transcript_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("type") not in ("user", "assistant"):
            continue
        msg = d.get("message") or {}
        role = msg.get("role") or d.get("type")
        text = extract_text(msg.get("content"))
        if text and not is_scaffolding(d, role, text):
            out.append({
                "role": role,
                "text": text,
                "uuid": d.get("uuid"),
                "timestamp": d.get("timestamp"),
            })
    return out


def estimate_tokens(transcript_path: Path, *, text: str | None = None) -> int:
    """`len(file) // 4`, from `text` when the caller has already read the file.

    `measure_context` is the only caller in `lts` and it has just read the whole transcript to look
    for `usage` records, so re-reading it here was a second full parse of the same bytes — 1.5 s of
    the session-load budget thrown away on a 208 MB transcript, in exactly the state where the
    figure is least trustworthy anyway. The path-only form stays for the callers that have no text
    in hand.
    """
    if text is not None:
        return len(text) // 4
    if not transcript_path.exists():
        return 0
    return len(transcript_path.read_text(encoding="utf-8", errors="ignore")) // 4


# How a token figure was arrived at. The distinction is the point: `estimate_tokens` is
# `len(file) // 4` over a file that is append-only across `--resume`, so on a long-running project
# it describes the project's whole history and not the live window. Divided by a window it was
# never measured against, that is the 22,707% reading this branch keeps quoting — and a caller
# that cannot tell the two apart will present one as the other, which is how an empty file used to
# buy `✓ pressure: 0/1,000,000 tokens (0%)`.
USAGE = "usage"          # summed from a real assistant `usage` record
ESTIMATE = "estimate"    # derived from the file's size, because no usage record was found
NO_FILE = "no_file"      # nothing was read at all


# How much of the end of a transcript is read to find the newest `usage` record, and how far the
# search widens before giving up. Measured 2026-10-01 on the five real transcripts under
# ~/.claude/projects (7.4 MB to 208 MB): the newest usage-bearing record began between 4,581 and
# 13,924 bytes before EOF, and the tail parse returned the same total as a full parse of the same
# bytes in all five, in 0.0002-0.0003 s per call. So one window covers the widest of those five
# about eighteen times over. It is not the tightest fit — 16 KiB would have held all five — and the
# margin is deliberate: a record ten times longer than any of them still costs a single read, and a
# window that is wrong in spite of that widens rather than guessing. `_TAIL_CAP` bounds what a file
# with no usage record in its tail can waste before the full read takes over: five windows, 256 KiB
# to 64 MiB, ~85 MiB read in all.
_TAIL_WINDOW = 256 * 1024
_TAIL_CAP = 64 * 1024 * 1024


def _usage_total(raw: bytes) -> int | None:
    """One assistant record's context total, or None for any other line.

    None means "not a usage-bearing assistant record", which covers a blank line, a line this
    process cannot parse, and a record of another type. It never means zero: a record summing to
    zero is a measurement of an empty window, and `measure_context` has to tell those apart.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("type") != "assistant":
        return None
    usage = (d.get("message") or {}).get("usage")
    if not isinstance(usage, dict):
        return None
    return (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )


def _tail_lines(transcript_path: Path):
    """The file's lines, newest first, over windows that widen until the whole file is covered.

    Shared by the two bounded questions asked of a transcript — the newest `usage` record and
    whether anything is newer than a mark — because both want the same thing: the end of an
    append-only file, read in one `pread`-style window rather than parsed from the start. Keeping
    one walk keeps the two honest about the same three details, each of which was a defect before it
    was a rule: the first line of a window is a fragment of a record and is not a line; a file that
    cannot be stat'd or read yields nothing rather than raising, because both callers are on a
    hook's path; and the widening restarts the scan over the wider window rather than continuing,
    so a caller that stops at its first match reads the duplicated head at most once per widening.

    Yields `bytes`, undecoded: the callers hand each line to `json.loads`, which takes bytes, and
    decoding a 256 KiB window to find one record was the cost `newest_usage` removed.
    """
    try:
        size = transcript_path.stat().st_size
    except OSError:
        return
    window = _TAIL_WINDOW
    while True:
        start = max(0, size - window)
        try:
            with transcript_path.open("rb") as fh:
                fh.seek(start)
                chunk = fh.read()
        except OSError:
            return
        lines = chunk.split(b"\n")
        if start > 0:
            # The window almost certainly began mid-record; that fragment is not a line.
            del lines[0]
        yield from reversed(lines)
        if start == 0 or window >= _TAIL_CAP:
            return
        window *= 4


def newest_usage(transcript_path: Path) -> int | None:
    """The newest assistant `usage` total, read from the end of the file.

    A transcript is append-only, so the record that describes the live window is the last one — and
    reading the whole file to find it was costing the `UserPromptSubmit` hook, per the spec's §7.1
    measurement of 2026-10-01, 1.410-2.575 s on every turn on the 208,143,252-byte ppss transcript.
    Measured end to end on that same transcript on 2026-10-01, three runs of `lts pressure
    --transcript` each way: 1.55-1.59 s wall and 1.72 GB peak RSS before, 0.04-0.06 s and 16 MB
    after, same verdict out. The memory figure is the part that was understated — `read_text` holds
    the file, and `splitlines()` then builds a Python str per line on top of it. This reads one
    window from the end instead, and scans it backwards.

    `None` is "no usage record was found", not "zero tokens". The caller falls back to the full read
    on `None`, so a record beyond `_TAIL_CAP` is found the slow way rather than silently replaced by
    an estimate of a different kind.
    """
    for raw in _tail_lines(transcript_path):
        total = _usage_total(raw)
        if total is not None:
            return total
    return None


# The shape of the one ISO-8601 stamp Claude Code writes into a transcript, which is also the shape
# `watermark.mark_at` writes into a mark. `newest_exchange_after` compares stamps lexicographically,
# as `watermark.exchanges_after` does and for the reason given there — but that is chronological only
# within one shape, so a stamp of another shape is no boundary at all rather than a boundary that
# sorts wrongly. `"2026-10-01T05:00:00.000Z" > "whenever"` is False, and left to the comparison a
# mark nothing can order would answer "nothing newer" about a file full of uncaptured exchanges.
_ISO_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def _exchange_of(raw: bytes) -> dict | None:
    """The exchange this transcript line holds, or `None` when the line is not one.

    `read_exchanges`' four rules for one line, in its order and through its two helpers, so that
    there is one definition of "an exchange" in this module and not two: a `user` or `assistant`
    record, whose `text` blocks are non-empty, and which `is_scaffolding` does not claim. The caller
    turns a `True` into an accusation that the `Stop` hook failed to capture something, and `stop.py`
    decides what to capture from `read_exchanges` — a looser rule here would accuse it of skipping a
    record it was never going to take.

    The two `isinstance` guards are the one deliberate difference, and they are what reading a window
    instead of whole lines costs: the first line of a tail window is a fragment, and a fragment can be
    valid JSON of any shape (`_usage_total` guards the same thing for the same reason). On a full line
    they can never fire, so they narrow nothing that `read_exchanges` accepts.
    """
    raw = raw.strip()
    if not raw:
        return None
    try:
        d = json.loads(raw)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(d, dict) or d.get("type") not in ("user", "assistant"):
        return None
    msg = d.get("message")
    msg = msg if isinstance(msg, dict) else {}
    role = msg.get("role") or d.get("type")
    text = extract_text(msg.get("content"))
    if not text or is_scaffolding(d, role, text):
        return None
    return {"role": role, "text": text, "uuid": d.get("uuid"), "timestamp": d.get("timestamp")}


def newest_exchange_after(transcript_path: Path, stamp: str | None) -> bool:
    """Is there an exchange in the tail of this transcript newer than `stamp`?

    A bounded form of the question `watermark.entries_after` answers exhaustively. The caller —
    `healthchecks._check_capture_live` — needs only "is there at least one", never how many, so the
    tail windows of `newest_usage` are enough: the newest records are at the end of an append-only
    file, so if anything is newer than `stamp`, the newest exchange is.

    `False` is reached from the end of the file rather than from its start, and that early exit is
    what makes the common case cheap. A transcript is chronological, so the first exchange found at
    or before `stamp` settles it: everything further back is older still. Without it, the ordinary
    per-turn state — the mark sitting on the newest exchange, which is exactly what `stop.py` leaves
    behind after a successful turn — would widen through every window up to `_TAIL_CAP`, ~85 MiB
    read on every prompt, to establish the answer the first window already held.

    An exchange carrying no timestamp is passed over rather than counted, which is what
    `watermark.exchanges_after`'s timestamp branch does with it too; a `stamp` that is not in Claude
    Code's one ISO-8601 shape (including `None`) is no boundary at all and makes this `True`, per
    `_ISO_STAMP`.

    Returns `False` for a file that cannot be read, holds no record, or holds nothing but
    scaffolding, which is the honest answer: nothing observed is newer than the mark, so there was
    nothing for the `Stop` hook to capture.
    """
    boundary = stamp if isinstance(stamp, str) and _ISO_STAMP.match(stamp) else None
    for raw in _tail_lines(transcript_path):
        exchange = _exchange_of(raw)
        if exchange is None:
            continue
        if boundary is None:
            return True
        when = exchange.get("timestamp") or ""
        if when > boundary:
            return True
        if when:
            return False
    return False


def measure_context(transcript_path: Path) -> tuple[int, str]:
    """`(tokens, provenance)` — the number, and what kind of number it is.

    The common path reads only the end of the file (`newest_usage`), because the record describing
    the live window is the last one. The full read below survives as the fallback for a file whose
    newest `usage` record lies beyond the widened windows, and for one that has none at all — where
    it still computes `len(text) // 4` exactly as it always did, so no figure this function has ever
    returned changes. `USAGE` is a measurement of the live window; `ESTIMATE` is a size-derived guess
    about a file that may span months; `NO_FILE` is neither.
    """
    if not transcript_path.exists():
        return 0, NO_FILE
    total = newest_usage(transcript_path)
    if total is not None:
        return total, USAGE
    # Nothing in the tail windows. Read it all: the record may sit further back than `_TAIL_CAP`,
    # and if there is none anywhere, this is also where the estimate's text comes from. Unguarded,
    # exactly as before — `newest_usage` swallows its own `OSError` and answers `None`, so a file
    # that exists and cannot be read still reaches this line and still raises. An `OSError` on such
    # a file is not "no file", and `NO_FILE` is the one state `lts pressure` prints
    # `status.ESTIMATED` for; "estimated from file size" would be a false sentence about a file
    # nothing could read. That mislabel is its own defect, and widening its reach is not this change.
    text = transcript_path.read_text(encoding="utf-8", errors="ignore")
    last = 0
    # Tracked apart from `last`, because a real record summing to zero is a measurement of an
    # empty window, not the absence of one, and `last or estimate` cannot tell those apart.
    measured = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if d.get("type") != "assistant":
            continue
        usage = (d.get("message") or {}).get("usage")
        if isinstance(usage, dict):
            measured = True
            last = (
                int(usage.get("input_tokens", 0) or 0)
                + int(usage.get("cache_read_input_tokens", 0) or 0)
                + int(usage.get("cache_creation_input_tokens", 0) or 0)
            )
    if measured:
        return last, USAGE
    return estimate_tokens(transcript_path, text=text), ESTIMATE


def context_tokens(transcript_path: Path) -> int:
    """Current context size in tokens, however it was arrived at.

    The number alone. `measure_context` returns the same number with its provenance, and anything
    that will present the figure to a human or to the model asks for both halves — `lts status`,
    `lts pressure`, `_check_pressure` and `UserPromptSubmit` all do, because an estimate shown as
    a reading is the defect this pair exists to separate.
    """
    return measure_context(transcript_path)[0]


def pressure_level(tokens: int, window: int, warn: float, force: float) -> str:
    """"none" | "warn" | "force", or "unknown" when the measurement cannot be true.

    More tokens than the window holds is not a full window: it is a reading nothing stands behind,
    and every verdict about it would be invented. `none` would understate it and `force` would
    overstate it with equal confidence, so there is no level to report. `UserPromptSubmit` states
    the two numbers and emits no nudge in that case — one measurement must not produce a sentence
    calling it unknown and a demand derived from it in the same block.
    """
    if window <= 0:
        return "none"
    if tokens > window:
        return "unknown"
    ratio = tokens / window
    if ratio >= force:
        return "force"
    if ratio >= warn:
        return "warn"
    return "none"
