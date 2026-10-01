"""What the user is told directly, without the model relaying it.

A hook's `additionalContext` reaches the model and not the user; its `systemMessage` reaches the
user and not the model. Both halves were **observed** on 2026-10-01 against the deployed clone, not
read from documentation — the probe line appeared in the chat verbatim, and the model's own
`UserPromptSubmit hook additional context:` block carried only the memory map on two consecutive
turns (spec §2.1.1).

Until this module existed, every demand this system made travelled the first route only: the health
block asks the model to "Tell the user before doing anything else" (`lts/health.py:51`) and the
sleep demand to tell them "Before doing anything else" (`claude/hooks/session_start.py:18`). Both
are instructions, not deliveries. The vault's `Architecture` note calls that shape out directly —
"the guarantee is that the failure is in front of the model, not that anything is done about it"
("The reliability subsystem sits on the hard side, with one soft edge") — and the `Hooks and
Automatic STM Capture` note records the same reasoning being applied once before, when capture moved
out of a `CLAUDE.md` instruction and into the `Stop` hook. This module applies it to the report about
whether that capture is working. A grep for `systemMessage` over the tree before this commit returned
nothing, so "the first route only" is literal.

Three subjects, and a fingerprint each, because the states are persistent: unconsolidated memory
stays unconsolidated until `/sleep`, and a full window stays full until a compaction. A line every
turn would become wallpaper — the argument the memory-health design already made against demanding
on `warn` — and a line once per session scrolls away while the risk grows. So a subject speaks again
when the quantity it measures has changed materially, and "materially" is defined per subject rather
than by a timer nobody can justify (spec §6.2).

Each line is a self-contained sentence naming its own subject, and none of them carries a warning
glyph. Observed 2026-10-01: the harness prints the field prefixed with the event name — the user sees
`UserPromptSubmit says: ` and then our text — so a `⚠` duplicates framing we already get, and a line
reading "1 check failing" without its subject would be a sentence completing "UserPromptSubmit
says", which is implementation jargon to the person reading it. Newlines survive that prefix (spec
§2.1.1: three lines and 279 characters arrived whole, soft-wrapped, with one prefix for the block and
the continuation lines indented), which is what makes one line per subject work. No length limit was
established by that observation and none is assumed here; the lines are short because a long line is
hard to read in a terminal.

No markdown is used in the lines either. Whether the harness renders any is unobserved — the probe
carried none — and a backtick that is not rendered is noise in a sentence the user cannot skip, so
commands are written bare.

This module knows nothing about hooks, files or Claude Code. It takes checks and metrics, and returns
text plus the fingerprints to store.
"""

from __future__ import annotations

from .healthchecks import Check

SUBJECTS = ("health", "sleep", "pressure")


def _magnitude(n: int) -> int:
    """`n` rounded down to its order of magnitude: 0, 1, 10, 100 ... Used as a fingerprint.

    So an STM backlog announces itself at 8 entries and again at 40, not once per entry. The
    boundary is the digit count, which is the coarsest honest summary of "how big is this".
    """
    return 0 if n <= 0 else 10 ** (len(str(int(n))) - 1)


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _health(checks: list[Check]) -> tuple[str, str]:
    """The failing check ids, or silence.

    `fail` only. `warn` and `skip` never reach the user: a warning that interrupts teaches the
    reader to ignore it, and failures are ignored along with it (spec §6.1, carried over from the
    memory-health design unchanged).

    Sorted, because the fingerprint is the set of failing ids and not the order `all_checks`
    happened to return them in — the per-turn caller passes a subset (`healthchecks.PER_TURN_IDS`),
    so the order is not this module's to depend on.
    """
    failing = sorted(c.id for c in checks if c.level == "fail")
    if not failing:
        return "", ""
    ids = ", ".join(failing)
    return (
        "+".join(failing),
        f"Memory health: {len(failing)} {_plural(len(failing), 'check', 'checks')} failing — "
        f"{ids}. Memory may be incomplete; run lts doctor for the fix for each.",
    )


def _sleep(metrics: dict) -> tuple[str, str]:
    stm = (metrics.get("stm") or {}).get("lines") or 0
    snaps = (metrics.get("pending") or {}).get("snapshots") or 0
    if not stm and not snaps:
        return "", ""
    return (
        f"{_magnitude(stm)}/{_magnitude(snaps)}",
        f"Unconsolidated memory: {stm} STM {_plural(stm, 'entry', 'entries')} and {snaps} pending "
        f"{_plural(snaps, 'snapshot', 'snapshots')} are waiting. "
        "/sleep writes them into long-term notes.",
    )


def _pressure(metrics: dict) -> tuple[str, str]:
    pr = metrics.get("pressure") or {}
    if pr.get("level") != "force":
        return "", ""
    tokens = int(pr.get("tokens") or 0)
    window = int(pr.get("window") or 0)
    if window <= 0 or tokens > window:
        # The rule `user_prompt_submit._context_line` already enforces: a percentage above 100 is
        # never real, so it is not printed. The account of the real reading is in
        # `healthchecks._check_pressure` — an estimate of 45,413,420 tokens divided by a hardcoded
        # 200,000 window, i.e. 22,707%, on a session actually holding 301,343 of its configured
        # 1,000,000 — and a line the user is guaranteed to read is the last place to repeat it.
        #
        # Silence rather than `_context_line`'s "treat the figure as unknown" sentence, because this
        # module is not the only thing speaking: `healthchecks._check_pressure` returns `fail` for
        # both of these states (`tokens > window`, and `window <= 0`), `pressure` is in
        # `PER_TURN_IDS`, and so `_health` above already puts the broken measurement in front of the
        # user. Two lines about one measurement, one of them refusing to name a figure, is worse than
        # one. `_context_line` has no such neighbour — it is the only surface the model reads — which
        # is why it states the numbers there and this states none here.
        #
        # Neither state can arrive from `status.collect`: `transcript.pressure_level` returns
        # "unknown" for `tokens > window` and "none" for `window <= 0`, so `level == "force"` is
        # impossible beside them. The guard is for a caller that builds its own metrics dict, which
        # the tests do and a second producer would.
        return "", ""
    pct = round(tokens / window * 100)
    return (
        f"{pct // 10}",
        f"Context window at {pct}% ({tokens:,}/{window:,} tokens). "
        "Run /sleep now, before a compaction decides for you.",
    )


def current(checks: list[Check], metrics: dict) -> dict[str, tuple[str, str]]:
    """Subject → `(fingerprint, line)`, for the subjects that have something to say right now.

    The keys are a subset of `SUBJECTS`, which is what the callers iterate.
    """
    built = {
        "health": _health(checks),
        "sleep": _sleep(metrics),
        "pressure": _pressure(metrics),
    }
    return {subject: pair for subject, pair in built.items() if pair[1]}


def message(checks: list[Check], metrics: dict, shown: dict) -> tuple[str, dict]:
    """The text to put in front of the user, and the fingerprints to remember.

    `""` when every subject is either silent or unchanged since `shown` — which is the common case,
    turn after turn, and is the whole reason this is not simply "print the demand every time".

    `shown` arrives from a file (`turnstate`), so it may be anything JSON can hold; a hook that died
    on a hand-edited state file would take the report with it. A non-dict is read as "nothing shown
    yet", which costs one repeated line and nothing else.

    The returned map holds only the subjects that speak now, so a subject that goes quiet is
    forgotten rather than remembered as empty — which is what makes the *return* of a failure news
    again instead of a silent repeat.
    """
    shown = shown if isinstance(shown, dict) else {}
    now = current(checks, metrics)
    lines = [line for subject, (print_, line) in now.items() if shown.get(subject) != print_]
    remembered = {subject: print_ for subject, (print_, _line) in now.items()}
    return "\n".join(lines), remembered
