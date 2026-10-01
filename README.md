# Let The AI Sleep

Hybrid memory for Claude Code: a short-term **STM working buffer**, a hook-enforced
**"sleep"** consolidation cycle, and a **session anchor** — built as a thin layer on top of
[Basic Memory](https://github.com/basicmachines-co/basic-memory) (which provides the Markdown
vault, CRUD, local hybrid full-text + vector search, and the knowledge graph).

The goal: Claude's context never silently fills up, and when it does, a `/sleep` consolidates
everything worth keeping into linked long-term notes **without losing a single number or name**.

## How it works (three memory tiers)

| Tier | What | Where | Lives |
|------|------|-------|-------|
| **Context** | the live conversation | Claude's window | until `/compact` |
| **STM** | a working buffer of facts/decisions as they happen | `<root>/.ai_memory/stm/buffer.md` | until the next sleep |
| **LTM** | the linked knowledge graph | Basic Memory Markdown vault | permanent |

- **While working** a `Stop` hook automatically captures each exchange into STM — no need to decide to remember; it survives `/compact`.
- **`/sleep`** reads the STM buffer, writes/links long-term notes (zero loss), rebuilds embeddings, then clears STM.
- **`/recall`** retrieves by graph priority (anchor links first) + semantic search, loading only what's relevant.
- **`/memory-status`** shows a dashboard of both tiers and flags when to sleep or reindex.
- **Hooks** (run by Claude Code automatically): `PreCompact` snapshots un-consolidated material as
  a backstop, `SessionStart` injects the anchor or forces an unfinished sleep to complete,
  `UserPromptSubmit` states the measured context occupancy on every turn and adds escalating
  "time to sleep" pressure as it fills.

> **The sleep mark.** A Claude Code transcript is one append-only file that `--resume` keeps
> extending, so it holds the whole history of a project, not the current chapter. `lts stm clear`
> (step 6 of `/sleep`) records how far consolidation reached — `<root>/.ai_memory/sleep-mark.json`.
> Everything behind the mark is in long-term notes and is never snapshotted again, so a `PreCompact`
> that finds nothing new writes no snapshot, and `SessionStart` only demands a sleep that is really
> owed. The same mark keeps the sleep's own narration out of the buffer it just emptied.
>
> The mark a hook places at the end of a turn is an **instant**, not a message id: Claude Code
> can write the turn's last message to the transcript *after* the hook has read it, so a mark
> naming the last message in the file lands one short — and that message (the sleep's own
> closing summary) leaked into the next snapshot. A timestamp covers it, because a message is
> stamped when it is produced. Claude Code's own bookkeeping records — the four `user` entries
> a `/compact` writes, and the compaction summary — are filtered out of the transcript
> entirely, so they reach neither the STM buffer nor a snapshot.

> **The memory map.** `/recall` is a skill, so using it is the model's decision — and the model
> cannot decide to read notes it does not know exist. `SessionStart` used to return the "unfinished
> sleep" demand *instead of* the anchor, which is correct for short sessions and catastrophic for
> long ones: a session running for weeks on `/sleep` + `/compact` never has an empty STM buffer, so
> the anchor was withheld permanently. Two fixes: `SessionStart` now emits the demand *and* the
> anchor *and* the digest; and `UserPromptSubmit` injects a **memory map** on every turn — the
> titles of every note in the vault, grouped, ~120 tokens, capped by `[memory] map_budget` (0 turns
> it off). The map guarantees the *information*; choosing what to read is still the model's.
>
> The map is **ranked**, because on a 247-note vault the budget bought 23 titles and spent them all
> on the alphabetical head of `knowledge-base`. The anchor's notes come first, on their own
> `active:` line, above the groups — the last sleep chose them, and one of them is the last session
> note, which lives in the group that truncation drops first. Then recency within each topical
> group, then the alphabet to break ties. Session notes are ranked by title, since the title is the
> date and, unlike an mtime, it survives a `git clone`.

> **Memory health.** Three memory failures have been found in this project, each after running a
> long time, each found by accident: `SessionStart` withheld the anchor for ~2 months, `lts
> pressure` measured 45,413,420 tokens against a hardcoded 200,000-token window (**22,707%**), and
> the model stated a context percentage no component had produced. A liveness poll would have caught
> none of them — every component was alive and the *claims* were false. So `lts doctor` now runs
> eleven checks in four kinds: **inventory** (is each part present — including that each of the
> four hooks `lts` owns is wired and its script still exists on disk, since projects point at
> `~/tools` by absolute path; a hook you wired yourself is outside what that check looks at),
> **invariants** (is the sleep mark behind the capture mark, is the measured context inside the
> window), **trend**, which reads the sidecar's `health.jsonl` — the per-run log that records what
> `SessionStart` actually emitted, and is the only way to catch "the anchor has not reached the
> model in five sessions", invisible in a filesystem snapshot — and **liveness**: `capture_live`
> (below) is the only check that notices *mid-session* that the `Stop` hook has stopped
> capturing. A failed check becomes a **demand** in context, not a line of information:
> quiet information is what the anchor and `/recall` already proved gets ignored.

> **Two channels.** A failed check reaches the model through the hook's `additionalContext` — the
> check lines with a `Fix:` each — and the user through `systemMessage`, which Claude Code shows in
> the transcript and the model never sees. The second exists because the first is an *instruction*:
> the block says "tell the user", and a guarantee that depends on being repeated is the soft kind
> this project already refused for STM capture. Both halves were **observed** on 2026-10-01, against
> `UserPromptSubmit` in the deployed clone, rather than taken from the documentation: the user saw
> the probe line verbatim, and two consecutive turns carried no trace of it in the model's injected
> context while `additionalContext` arrived normally on both. The harness prefixes the block with
> the event name (`UserPromptSubmit says: `), so each line has to be a self-contained sentence that
> names its own subject; newlines survive the prefix (three lines and 279 characters arrived whole,
> same observation). The user's line is deduplicated by a fingerprint per subject — the sorted ids
> of the failing checks, the order of magnitude of the STM backlog and of the pending snapshots, the
> window's ten-percent band — so a persistent problem is stated when it changes rather than on every
> turn, and a subject that goes quiet is forgotten rather than remembered as empty, which is what
> makes its return news again. `SessionStart` *emits* whatever is outstanding unconditionally,
> because a new session is a new screen, and deliberately stores no fingerprints, so what it emits
> cannot suppress the per-turn hook's first line — but see the paragraph below before assuming it
> arrives.
>
> The same field on `SessionStart` is **documented but not yet observed.** That event fires only at
> session start and on `/compact`, so it could not be probed in the session that probed
> `UserPromptSubmit`, and printing the hook's JSON by hand proves what we emit, not what the harness
> puts on the screen. The first `/compact` after this ships is where it gets confirmed; until then,
> treat the session-start line as emitted and not as seen.

> **`capture_live`** is the eleventh check, and the only one that detects a dead `Stop` hook
> *mid-session*: the trend checks read the sidecar's `health.jsonl`, which gains a record only at
> `SessionStart`, so between two compactions they keep answering `ok` from the same five records
> while every exchange goes uncaptured. It asks two things at once — has the capture mark stayed put
> across consecutive prompts, **and** does the transcript hold exchanges newer than it? Either half
> alone lies. `stop.py` leaves the mark alone for a transcript it cannot read (so a reset cannot
> replay the file) and writes the same value back for a turn that produced no exchange, so an
> unmoved mark is not evidence by itself; and something being newer than the mark is the ordinary
> state between a turn ending and the next `Stop` running. Together they mean the hook was given
> work and did not do it.
>
> One miss is tolerated, because a turn the user interrupts runs no `Stop` hook at all and that is
> something people do on purpose: the demand arrives only once **two consecutive prompts** have each
> found the mark exactly where the prompt before them left it — the third run of the hook, counting
> the one that set the baseline. The count restarts when the session id changes, because the state
> file is per project with no session dimension and nothing deletes it, so an interrupted last turn
> would otherwise hand the next session a failure it has not earned (and would not need: the next
> working `Stop` captures from the mark, not from "this turn"). A mark that identifies its exchange
> by `uuid` rather than by a usable timestamp is a `skip`, not a verdict — it still resolves exactly,
> so the capture is working, while the timestamp both halves of the question compare stands still.
> There is deliberately **no sleep exemption**, though the design document asked for one: the armed
> branch of `stop.py` writes a fresh capture mark, so the mark moves during a sleep and the count
> resets by itself, and `stop.py` is also what disarms the sleep flag — which makes a hook that is
> not running the one state where that flag stays armed across prompts, exactly the state an
> exemption would have blinded the check in.
>
> The per-turn hook runs the **eight** checks that neither walk a directory tree nor parse the whole
> transcript — `config`, `hooks`, `marks`, `pressure`, `anchor_fresh`, `anchor_delivery`,
> `capture_progress`, `capture_live` — and the subset is selected *before* the checks run, which is
> the only reason it is cheaper than running them all. Measured 2026-10-01 on three real projects
> (this one, `ppss`, `nextcloud-development`), every vault on the same fuseblk mount and each project
> given its largest real transcript (13.4 MB / 209.0 MB / 89.0 MB): no check in that subset cost
> more than **1.121 ms**, against **605.770 ms** for the `sidecars` tree walk and **1,463.639 ms**
> for `capture`'s full-transcript parse. Those are figures for those projects on that filesystem on
> that day — per project and per filesystem, not properties of the code — so re-measure before
> quoting them about anything else. The membership is not configurable: a reliability guarantee an
> operator can quietly switch off is not one. `capture_live` needs a transcript to reach a verdict,
> so `lts doctor` without `--transcript` reports it as `ok`, or as a `skip` that names how many
> prompts found the mark unmoved.

> **Semantic search note:** Basic Memory serves full-text and graph links immediately on write,
> but vector embeddings are rebuilt by `basic-memory reindex --embeddings -p <project>` (not on
> every write). `/sleep` runs this after consolidating; `install.py` lists it as a setup step.

## Requirements

- Python 3.11+
- Claude Code CLI
- [Basic Memory](https://github.com/basicmachines-co/basic-memory) (AGPL-3.0), used as a separate
  process over MCP. Install with `uv tool install basic-memory`
  (or `pip install --user basic-memory`; on Debian/Ubuntu add `--break-system-packages`).

## Install

You clone this repo **once** and install `lts` **once**, then attach memory to each project you
want it in (the project you attach to is the **target**; this repo stays the **source** of the
hook scripts and skills).

```bash
# 0. clone this repo once (anywhere stable — it stays as the source; not inside your project)
git clone https://github.com/Gesixt/LetTheAISleep.git ~/tools/LetTheAISleep
cd ~/tools/LetTheAISleep

# 1. install this package GLOBALLY so the `lts` command is on your PATH.
#    The skills (/sleep, /memory-status) call `lts` from shells Claude Code spawns,
#    so it must be available outside any project virtualenv.
uv tool install --editable .
#   or, without uv:
#   pip install --user -e .          # on Debian/Ubuntu: add --break-system-packages
lts --help          # verify the command is found
```

Then, **for each project** you want memory in, attach it (this writes `config.toml`, the
`.ai_memory/` sidecar, `.claude/` hooks+skills, and a memory-instructions block in the project's
`CLAUDE.md` so Claude captures memory proactively):

```bash
# 2. attach memory to a target project and set its Basic Memory project name
python ~/tools/LetTheAISleep/install.py --target /path/to/your/project --project <project-name>
#   e.g.  python ~/tools/LetTheAISleep/install.py --target ~/code/myproject --project myproject
#   (omit --target to use the current directory; omit --project to be prompted)
```

`install.py` prints the remaining manual steps — run them **from the target project**:

```bash
cd /path/to/your/project

# 3. create a Basic Memory project (the vault) and build its vector index
basic-memory project add <project-name> <vault-path>
#   e.g.  basic-memory project add myproject ~/code/myproject/.ai_vault
basic-memory reindex --embeddings -p <project-name>
#   e.g.  basic-memory reindex --embeddings -p myproject

# 4. REGISTER the Basic Memory MCP server with Claude Code (required — the skills call its tools)
claude mcp add basic-memory -- basic-memory mcp
#   (or, via the official plugin:)
#   claude plugin marketplace add basicmachines-co/basic-memory
#   claude plugin install basic-memory@basicmachines-co

# 5. restart Claude Code in the target project so it loads the MCP server, hooks and skills
```

`install.py` also reminds you to add `.claude/settings.json`, `.ai_memory/` and `config.toml`
to the **target project's** `.gitignore` (they contain machine-specific absolute paths).

> `config.toml` **is** the project root marker — the hooks and the `lts` CLI locate `.ai_memory/`
> by walking up until they find one with a `[vault]` section. Keep it at the root; if it is moved
> or deleted, memory writes refuse (rather than sprouting a sidecar elsewhere). `lts doctor` tells
> you which root resolved.

Use the **same `<project-name>`** in step 3 that you passed to `install.py` in step 2 — the
installer already wrote it into `config.toml` (`[vault] project`), so the skills target the
right vault automatically.

> Without step 4 the `/sleep` and `/recall` skills cannot read or write long-term notes —
> registering the Basic Memory MCP is **required**, not optional.

## Updating an already-installed project

An install leaves two kinds of thing behind, and they age differently.

The **hook scripts and the `lts` CLI stay in the clone** and are only referenced from a project,
so they update with a `git pull` there — every project follows at once. The **skills, the hook
wiring in `.claude/settings.json`, and the memory block in `CLAUDE.md` are copies**, and a copy
goes stale in silence: a project can keep running last month's `/sleep` long after the clone
moved on.

`lts update` refreshes exactly those copies. Re-running `install.py` is never needed:

```bash
git -C <path-to-clone> pull       # the code: hooks + the lts CLI, shared by every project
cd <your-project>
lts update --check                # what is stale? writes nothing, exits 1 if anything is
lts update                        # refresh this project's copies
```

It prints which clone it is copying **from** — the answer is whichever clone this `lts` is
running out of, not whichever one you are standing in, which matters as soon as a machine holds
both a development clone and a deployed one. Point it elsewhere with `--source`, name another
project with `--target`, and get the report as data with `--json`.

`lts update` never touches `config.toml`, `.ai_memory/` or the vault — those are your state, so
the command is safe to run at any moment, including mid-session. Skills the project added of its
own are left alone; only the ones the clone ships are overwritten.

## Troubleshooting (install gotchas)

- **`uv: command not found`** — `uv` is optional. Either install it (`sudo snap install astral-uv`)
  or use plain pip in every step (`python3 -m pip install --user --break-system-packages -e .`).
- **`error: externally-managed-environment`** (Debian/Ubuntu, PEP 668) — add
  `--break-system-packages` to user pip installs, e.g.
  `python3 -m pip install --user --break-system-packages -e .`
- **`-e option requires 1 argument`** — you dropped the trailing `.`. The dot is the package path;
  run it **from inside the cloned repo** (where `pyproject.toml` is): `pip install --user -e .`
- **`lts: command not found` after installing** — `~/.local/bin` is not on your PATH. Add
  `export PATH="$HOME/.local/bin:$PATH"` to your `~/.bashrc` and reopen the shell.
- **`Error: path argument is required in local mode`** (from `basic-memory project add`) — pass the
  vault path as the second argument: `basic-memory project add <project-name> <vault-path>`.
- **Hooks stop firing after you move or delete the clone** — a target project's
  `.claude/settings.json` points at the **source clone's absolute path**. Keep the clone in a stable
  place (e.g. `~/tools/LetTheAISleep`); if you move it, re-run `install.py --target <project>`.
- **Pulled a new version but a project still uses old skills/hooks** — re-run
  `python install.py --target <project> --project <name>` to refresh that project's copied
  `.claude/skills` and hook paths.
- **Notes land in the wrong vault** — the skills pass `[vault] project` from `config.toml` to Basic
  Memory; make sure that name matches the one you used in `basic-memory project add`.
- **A `.ai_memory/` appeared in a subdirectory** — `config.toml` is what marks the project root, so
  moving or deleting it detaches memory from the project. Run `lts doctor`: it prints the root it
  resolved, and lists stray sidecars (orphaned memory that `/sleep` will never read). Restore
  `config.toml` at the root, merge anything worth keeping out of the stray `.ai_memory/stm/buffer.md`
  into the root one, then delete the stray directory. `lts` refuses to create a sidecar outside a
  configured root, so this cannot recur silently.
- **`basic-memory project info <name>` fails with `set to cloud mode but no credentials`** — that
  error is misleading; it really means "no such project". Basic Memory registers projects under a
  **slug** (`LetTheAISleep` → `let-the-aisleep`) but reports the display name over MCP, and its CLI
  is inconsistent about which form each subcommand accepts:

  | name form | `project info` | `reindex -p` | MCP (`write_note`, …) |
  |---|---|---|---|
  | display (`LetTheAISleep`) | fails | works | works |
  | slug (`let-the-aisleep`) | works | fails | — |

  `lts status` prints both forms next to the subcommand each belongs to
  (`Basic Memory: reindex -p <name>  ·  project info <slug>`, or `project_slug` in `--json`),
  and the skills already pass the right form to each. Simplest prevention: name Basic Memory projects in
  lowercase so both forms coincide.
- **A reported context percentage that is not the real one** — the model has to be *given* the
  number or it will estimate one. Observed 2026-09-25: it announced "context ~75%" and recommended
  a `/compact` while the turn's own usage record read 276,013 tokens, i.e. 27.6%. The hook now
  states `Context window: <used>/<window> tokens (<pct>%)` on every turn, and the nudges no longer
  quote bands of their own. Two meters exist and must not be conflated: the **context window**
  (what `/compact` resets) and the **STM buffer** (what `/sleep` clears).
- **Wrong "context filling" warnings** — context pressure is measured against
  `[sleep] context_window` in `config.toml` (default `1000000`, for Opus's 1M window). If you run a
  smaller-window model, set your own value, e.g. `context_window = 200000` for Sonnet — otherwise
  warnings fire too late (window too large) or too early (too small).

## Usage

**Daily flow**
1. Work normally. Every exchange is captured to the STM buffer automatically by the `Stop` hook — like human short-term memory, you don't decide what to remember.
2. As context fills, the `UserPromptSubmit` hook nudges you (~60%) and then insists (~80%) to sleep.
3. Run **`/sleep`** at a natural break (or when nudged). It consolidates STM → linked long-term notes,
   rebuilds embeddings, clears STM, and you can `/compact`.
4. Next session, the `SessionStart` hook injects the **anchor** (a tiny table of contents). Ask a
   question and Claude uses **`/recall`** to pull only the relevant notes.

**Commands & skills**

| Surface | Purpose |
|---------|---------|
| `/sleep` | Consolidate the session into long-term memory without loss, then clear STM. |
| `/recall [question]` | Retrieve relevant memory (anchor/graph links prioritized over pure semantic hits). Pass a topic or question, or call it bare. |
| `/memory-status` | Dashboard: STM buffer, sleep debt, context pressure, LTM note/link counts, embedding freshness. |
| `lts status [--transcript P] [--json]` | The STM/sidecar metrics directly (used by `/memory-status`). |
| `lts doctor [--json] [--transcript PATH]` | Eleven health checks: inventory, invariants, trend and liveness. Exits non-zero on a failure. `--transcript` enables the two that cannot answer without one — `capture`, which parses the whole file, and `pressure`, which reads only its end — and lets `capture_live` reach a verdict instead of a `skip`; hooks omit it. `--json` prints `{"worst": <level>, "checks": [{"id", "level", "message", "fix"}, ...]}`, **eleven** objects, one per check in report order, `level` one of `ok`/`warn`/`skip`/`fail` and `fix` possibly `null`. |
| `lts stm append/read/clear` | Inspect or manage the per-project STM buffer directly. |
| `lts update [--check] [--source P] [--target P]` | Refresh this project's copies (skills, hook wiring, `CLAUDE.md` block) from the clone, without re-installing. |
| `lts memory-map` | The note titles the `UserPromptSubmit` hook injects on every turn. |
| `basic-memory project info <project>` | LTM counts (Entities/Relations/Isolated) and embedding status. |

**Recalling memory** — `/recall` takes an optional topic or question:

```
/recall why did we choose the BFF approach for cart?   # answer a specific question from memory
/recall cart service                                   # pull everything relevant to a topic
/recall                                                 # bare: load the anchor + last session as context
```

You can also just ask the question in plain language — the `CLAUDE.md` memory instructions tell
Claude to recall first — but an explicit `/recall` guarantees the retrieval runs.

**Checking memory load**

```bash
lts status
```
shows the project, STM size, un-slept "sleep debt", context pressure, and anchor freshness. `/memory-status`
combines this with Basic Memory's LTM stats and tells you if you should `/sleep` or reindex.

## Team mode

Several developers, one memory. `knowledge-base/` is **shared** — one note per topic, everyone
augments it. `session-memory/` is **personal** — one namespace per developer. Attribution comes from
byline `[[links]]` back to each session note, so the knowledge graph stays connected instead of
splitting into one island per person.

```
.ai_vault/                       # its own git repository
  knowledge-base/
    Cart Service.md              # shared; Petr and Vincent both append to it
  session-memory/
    dmitrii/Session_dmitrii_2026-07-10_1002.md
    petr/Session_petr_2026-07-09_1730.md
```

Set it up by cloning the vault **before** installing:

```bash
git clone git@github.com:acme/myproject.git
cd myproject
git clone git@github.com:acme/myproject-memory.git .ai_vault
python3 ~/tools/LetTheAISleep/install.py \
    --target ~/code/myproject --project myproject --author dmitrii
```

`install.py` never creates, moves or deletes vault content. Omit `--author` (or answer the prompt
with an empty line) and everything behaves exactly as it does for a single developer.

**The system never runs git for you.** No fetch, pull, commit or push, from any hook or skill —
publishing memory is your decision, and a hook must not block on the network. What it does instead:

| Command | What it tells you |
|---------|-------------------|
| `lts digest` | What teammates changed since your last sleep, and who changed it; plus commits behind/ahead and uncommitted files |
| `lts session-name` | The session note title and directory for your namespace |

`SessionStart` shows the digest automatically when there is something to report, and `/memory-status`
always includes it. Ahead/behind counts are relative to the **last fetched** remote ref — run
`git -C .ai_vault fetch` yourself if you want them fresh.

Notes pulled from teammates are indexed automatically: Basic Memory's MCP server background-syncs
every project on startup and runs a file watcher, and that path builds vector embeddings too. Nothing
extra to run after `git pull`.

## License note

This project talks to Basic Memory only over MCP (no linking/embedding). Basic Memory itself is
AGPL-3.0; this repository's own license is declared separately.
