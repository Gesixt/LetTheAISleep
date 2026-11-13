"""Which coding client is this hook running under, and how does its transcript read.

Everything else in `lts` is vendor-neutral already: the sidecar is ours, the vault is Basic
Memory's, and no module in this package calls MCP at all. The transcript is the exception — it is
written by the client, in the client's own schema. What depends on reading it correctly, named
rather than counted because a count in prose is what rots: STM capture, the compaction backstop,
the context figure, and the `capture` and `capture_live` health checks. The capture mark is not
another of them — `stop.py` derives it from the same parse as the capture it just wrote.

Codex's own documentation states that its transcript format "isn't a stable interface for hooks and
may change over time". A dependency that broad on a format its vendor disclaims belongs behind one
interface with its own tests rather than at each of those call sites.

**The running client is declared, never sniffed.** A hook reads `--client <id>` off its own command
line. Deciding from the shape of a payload which parser to use would be a guess on the hard side of
the soft/hard split, and the hard side does not guess: a wrong guess does not fail, it captures
plausible-looking nonsense into long-term memory.

Nothing writes the flag yet: `claude_code` is the only registered adapter, so the wiring that
`lts.sync.render_hook_settings` and `install.py` emit carries no `--client` at all, and every
installed project resolves to the default. That default is what lets the split reach an
already-wired project the moment its clone is pulled, with nothing to re-wire; a second client
arrives as an adapter plus wiring that declares its own id.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from lts.clients import claude_code as _claude_code

DEFAULT = "claude_code"


class UnknownClient(ValueError):
    """A `--client` value no adapter answers to."""


class Client(Protocol):
    # Properties and not variable members: a variable member demands a *settable* attribute, and
    # every adapter is a frozen dataclass — correctly, since an adapter is a description of a
    # client and not state. Declared as `id: str` the registry line below was a type error
    # (`dict[str, ClaudeCode]` not assignable to `dict[str, Client]`) in the one file the next
    # adapter will be written from.
    @property
    def id(self) -> str: ...

    @property
    def default_context_window(self) -> int: ...

    def read_exchanges(self, path: Path) -> list[dict]: ...

    def measure_context(self, path: Path) -> tuple[int, str]: ...

    # Is there an exchange in this transcript newer than `stamp`? `lts.transcript
    # .newest_exchange_after` is the reference implementation; its contract, which an adapter must
    # keep, is:
    #
    # - `stamp` is the raw `timestamp` off the capture mark, so it may be `None` or a string in no
    #   shape this client writes. **Either is no boundary at all and answers `True` whenever the
    #   file holds any exchange**, because nothing has been established about how far the mark
    #   reaches. `_check_capture_live` arrives here with `stamp=None` whenever the mark file is
    #   *missing or unreadable* — `_classify_mark` answers `None` for both, and the early return
    #   covers only a mark that is present and undated. Neither state establishes a boundary: a
    #   mark that cannot be read says nothing about how far capture got, exactly as an absent one
    #   does not. In either, a live transcript means the `Stop` hook was given work. Answering
    #   `False` there ("no stamp I can parse, so nothing is newer") turns the project's only
    #   mid-session detector of a dead `Stop` hook into a confident `ok` about one;
    # - the comparison is **strict**: the mark's own exchange is not newer than itself, and that
    #   equality is the measured normal rather than a miss — `stop.py` leaves the mark sitting on
    #   the newest exchange, which is the input behind `_check_capture_live`'s "nothing to
    #   capture" `ok`. An adapter comparing `>=` would answer `True` on every such prompt and
    #   turn a healthy hook into "the Stop hook is not capturing": the same failure as the clause
    #   above with the sign reversed, a confident alarm instead of a confident clean bill;
    # - *when there is a boundary*, an exchange carrying no timestamp is passed over rather than
    #   counted, which is what `watermark.exchanges_after`'s timestamp branch does with it too.
    #   Only when there is one: read as unconditional, this clause would answer `False` for a
    #   file of nothing but undated exchanges under a missing mark, which is the first clause's
    #   forbidden `ok` reached through this one. The reference resolves it in the first clause's
    #   favour: it returns `True` on the first exchange it finds, before reading its timestamp.
    #   The state is real — `mark_of` writes `timestamp: None` for an exchange that carried
    #   no stamp;
    # - a file that cannot be read, holds no record, or holds nothing but scaffolding is `False`:
    #   nothing observed is newer than the mark, so there was nothing to capture.
    #
    # It is its own method rather than derived from `read_exchanges` because `_check_capture_live`
    # is in `healthchecks.PER_TURN_IDS` and `capture` is excluded from that subset precisely
    # because it parses the whole transcript. The only answer needed here is "is there at least
    # one", and Claude Code's implementation reads bounded windows from the end of the file;
    # deriving it from `read_exchanges` would move a per-prompt check into the cost class that
    # subset exists to exclude — 1.5 s on the 208 MB transcript measured in `lts.health.run`, on
    # every prompt, in exactly the degraded state this check exists to report.
    def newest_exchange_after(self, path: Path, stamp: str | None) -> bool: ...


_REGISTRY: dict[str, Client] = {_claude_code.ADAPTER.id: _claude_code.ADAPTER}


def known() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def resolve(client_id: str | None) -> Client:
    """The adapter for `client_id`; `None` means the client that existed before there were two.

    An unrecognised id raises instead of falling back to the default. A fallback would read every
    transcript with the wrong parser — and the failure mode of the wrong parser is not an exception
    but silence: it returns no exchanges, or the wrong ones, and capture proceeds looking healthy.
    This project's whole history is that a confident wrong answer costs more than no answer.
    """
    if client_id is None:
        client_id = DEFAULT
    try:
        return _REGISTRY[client_id]
    except KeyError:
        raise UnknownClient(
            f"unknown client {client_id!r}; known clients: {', '.join(known())}"
        ) from None


def from_argv(argv: list[str]) -> str:
    """`--client <id>` (or `--client=<id>`) out of a hook's argv; the default when it is absent.

    Deliberately not `argparse`. A hook's contract is to read JSON on stdin and write JSON on
    stdout; `argparse` answers an argument it dislikes with `SystemExit(2)` and a usage block on
    stderr, which in a hook means capture silently stops. Unrecognised arguments are ignored here,
    and so is a `--client` whose value is missing or empty — `--client`, `--client=` and
    `--client ""` are all a malformed command line, not a client. An unknown *client name* is a
    different matter and is refused by `resolve` — the difference is that a malformed command line
    is the wiring's problem, while a wrong client name would corrupt memory.

    The empty value is guarded in both forms because the one place it is likely to come from is an
    unset shell variable in the wiring, which expands to exactly that. Passed through, it reaches
    `resolve`, which refuses it — and the refusal costs the hook its turn, which is the price
    reserved for a name that would read a transcript with the wrong parser.
    """
    for i, arg in enumerate(argv):
        if arg == "--client" and i + 1 < len(argv) and argv[i + 1]:
            return argv[i + 1]
        if arg.startswith("--client=") and arg.split("=", 1)[1]:
            return arg.split("=", 1)[1]
    return DEFAULT
