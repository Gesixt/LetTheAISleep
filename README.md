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

> **The sidecar, and which half of it belongs to one client.** `<root>/.ai_memory/` holds
> everything memory knows that is not a note. One rule splits it: **anything that names a position
> inside a transcript belongs to the client that wrote that transcript; anything that is the
> project's memory is shared.** The rule has exactly one exception in the code, and it is a relic:
> the pre-watermark `stm/.lts-capture-offset`, a bare exchange count at a shared path, which the
> `Stop` hook honours once and deletes on first read.
>
> | under `<root>/.ai_memory/` | whose | why |
> |---|---|---|
> | `stm/buffer.md` | shared | the buffer a sleep consolidates — sharing it is the point of wiring two clients to one project |
> | `anchor.json` | shared | the last sleep chose those notes for the project, not for a transcript |
> | `sleep-mark.json` | shared | it claims that notes exist, and a note is the project's |
> | `stm/.lts-sleep-armed` | shared, and **not soundly so** | one sleep, one latch: the next hook to run is what turns it into the sleep mark. With two clients wired that hook may be the *other* one's `Stop`, which then discards its own turn and advances its capture mark past it — so that turn reaches neither the buffer nor a note, and nothing will capture it later. Unreachable while one client is wired, which is why it is recorded rather than fixed: a per-client latch and an armed branch that captures its tail first are both design decisions. See `paths.client_state_dir` |
> | `pending_consolidation/` | shared | material no note holds yet is owed to the project; snapshots are keyed by session id, so they cannot collide |
> | `clients/<client>/capture-mark.json` | one client | it names an exchange by `uuid` *inside one transcript* |
> | `clients/<client>/snapshot-mark.json` | one client | the same kind of position, named the same way |
> | `clients/<client>/turn-state.json` | one client | it counts prompts and misses within one client's turn sequence |
> | `clients/<client>/health.jsonl` | one client | the trend checks read it as a single history, and two interleaved clients make that history describe neither of them |
> | `clients/<client>/.adopted` | one client | the marker that adoption of the pre-split flat files has had its one chance here — only `claude_code` has any such files to adopt (see the one-way door, below) |
>
> The capture mark is why the rule is stated that strongly: sharing it would not break loudly. A
> `uuid` out of one client's transcript never matches in the other's, so `watermark.exchanges_after`
> falls through to its timestamp branch, which returns only what is newer than the *other* client's
> position — every exchange older than that is dropped, permanently, with every health check green.
> That is the signature of the 2026-10-16 incident below, and a shared mark would reintroduce it by
> construction rather than by accident. The snapshot mark falls through the same way, which would
> let one client's compaction declare the other's unsnapshotted chapters already flushed.

> **The client seam.** The transcript is the one thing here that a vendor owns: the sidecar is ours,
> the vault is Basic Memory's, and no module in `lts` calls MCP at all. So "how does this client's
> transcript read" lives behind a single interface, `lts/clients/`, and what goes through it is
> named rather than counted: STM capture, the `PreCompact` backstop, the context figure, and the
> `capture` and `capture_live` health checks. (The capture mark is not a separate reader — the
> `Stop` hook derives it from the same parse as the capture.) Codex's own documentation says its
> transcript format "isn't a stable interface for hooks and may change over time"; a dependency
> that broad on a format its
> vendor disclaims belongs behind one interface with its own tests rather than at each of those
> call sites.
>
> **The running client is declared, never sniffed.** Each hook reads `--client <id>` off its own
> command line, and an id no adapter answers to is **refused** rather than resolved to the default:
> the failure mode of the wrong parser is not an exception but silence — it returns no exchanges, or
> the wrong ones, and capture carries on looking healthy. Deciding the parser from the shape of a
> payload would be the same guess with better manners. The refusal costs that hook its turn — it
> lands in `/tmp/lts-hook-errors.log` and the hook still prints well-formed `{}` — rather than
> reaching the harness as a crash. The flag is read by hand and not with `argparse`, which answers
> an argument it dislikes with a usage block on stderr and exit 2; in a hook that means capture
> stops silently.
>
> An absent flag resolves to `claude_code`. One adapter is registered today, so the wiring
> `install.py` and `lts update` write passes no flag at all and every installed project resolves to
> that default — the per-client split reaches an already-wired project the moment its clone is
> pulled, with nothing to re-wire. A second client arrives as an adapter plus wiring that declares
> its own id; `lts doctor --client <id>` already reports on one client at a time.

> **The sleep mark, and the snapshot mark.** A Claude Code transcript is one append-only file that
> `--resume` keeps extending, so it holds the whole history of a project, not the current chapter.
> Two marks say how far through it memory has got, and they are two **different claims**:
>
> - the **sleep mark** — `<root>/.ai_memory/sleep-mark.json` — claims that **notes exist**:
>   everything behind it is in long-term notes. Only a sleep writes it (`lts stm clear`, step 6 of
>   `/sleep`, arms the flag that the next `Stop` — or `PreCompact`, if an auto-compact beats it —
>   turns into the mark). It is shared by every client, because a note is the project's, not a
>   transcript's;
> - the **snapshot mark** — `<root>/.ai_memory/clients/<client>/snapshot-mark.json` — claims only
>   that a **pending snapshot** holds the material, and no note does. `PreCompact` writes it, and
>   `lts pending clear` resets it: deleting the snapshots is the one thing that falsifies the
>   claim, and the pending directory is shared, so every client's copy of the mark goes.
>   `PreCompact` is the only reader that *works from* this mark — `marks` tests nothing about it
>   but its clock — so a copy left standing over deleted snapshots would quietly become the
>   frontier the next compaction excludes material behind. It is per client because it names a
>   position *inside one transcript*, and resolved against another client's transcript that
>   position means nothing.
>
> `PreCompact` snapshots the tail that neither claim covers, i.e. whatever follows the later of the
> two marks — so a compact that finds nothing new writes no snapshot, nothing already consolidated
> is dumped twice, and `SessionStart` only demands a sleep that is really owed. The sleep mark also
> keeps the sleep's own narration out of the buffer it just emptied.
>
> Until 2026-10-30 one file carried both claims, and `PreCompact`'s ordinary path wrote the
> *snapshot* claim into it (this project's own mark, measured that day, was
> `{"uuid": "4825afa5-…", "timestamp": "2026-10-19T06:09:51.626Z", "count": 509}` — the `uuid` is
> proof a compaction wrote it, since a sleep writes a timestamp alone). So an ordinary `/compact`
> advanced the consolidation frontier with nothing consolidated, and `sleep_evidence` — the check
> that asks what backs the frontier — reported that nothing did, which is true in the letter and
> indistinguishable from the 2026-10-16 incident below, where nothing at all held the material.
> Split apart, that check reads a claim only consolidation can make: "a snapshot holds it" and
> "nothing holds it" stop being the same report.
>
> The mark a hook places at the end of a turn is an **instant**, not a message id: Claude Code
> can write the turn's last message to the transcript *after* the hook has read it, so a mark
> naming the last message in the file lands one short — and that message (the sleep's own
> closing summary) leaked into the next snapshot. A timestamp covers it, because a message is
> stamped when it is produced. Claude Code's own bookkeeping records — the four `user` entries
> a `/compact` writes, and the compaction summary — are filtered out of the transcript
> entirely, so they reach neither the STM buffer nor a snapshot.
>
> **The mark is not moved by `lts stm clear` itself.** The command sets a flag, and the next hook to
> run — `Stop`, or `PreCompact` if an auto-compact beats it — is what writes the mark. That flag is a
> latch on disk with no owner, and until 2026-10-19 nothing bounded how long it could wait. Both live
> failures it produced are on record, on the same day, from the same cause:
>
> | project | what consumed the stale latch | marks afterwards | what `marks` said |
> |---|---|---|---|
> | `nextcloud-development` | `Stop` | sleep **and** capture set to one instant | `ok` — perfectly consistent |
> | `ppss` | `PreCompact` | sleep mark alone, 42 s past the capture mark | `fail` |
>
> In the first the frontier moved ~20 hours over 90 exchanges that no note held *at that moment*,
> and every check stayed green; in the second, 2.6 hours past every note in a 267-note vault. The
> first span was recovered three days later only because the buffer happened not to be cleared
> again — `PreCompact`'s backstop was off for it, since that hook snapshots only what lies *after*
> the frontier the two marks set between them. So the latch now
> carries **when it was armed**, and a hook honours it only within `watermark.SLEEP_FLAG_MAX_AGE`
> (5 minutes; the six sleeps on record consumed it in 8.3–16.9 s). Anything else — no stamp, an
> unreadable one, one from the future, one past the bound — is a latch nobody consumed, and the hook
> captures normally instead, logging why. Every flag written before this exists is an empty file, so
> each already-installed project holds exactly that case.
>
> And `lts stm clear` **arms only when the vault gained a note** newer than the current sleep mark.
> Arming claims the span behind it is in long-term notes; the skill's "clear only after the writes
> succeeded" is an instruction the model can reorder, while the half that moves the frontier is not.
> Emptying the buffer stays unconditional — doing that on purpose is a real thing to want — but it
> then says plainly that it is not declaring anything consolidated.

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

> **Memory health.** Three memory failures were found in this project, each after running a long
> time, each found by accident: `SessionStart` withheld the anchor for ~2 months; `lts pressure`
> divided 45,413,420 estimated tokens by a hardcoded 200,000-token window, and the percentage
> recorded beside that count — "9900%" — follows from no window at all (22,707% is the figure that
> reconciles, computed afterwards, never printed); and the model stated a context percentage no
> component had produced. **A liveness poll would have caught none of those three** — every component
> was alive and the *claims* were false, which is why liveness is not the organising principle here.
>
> A **fourth** failure mode turned up while this was being built, and it *is* a liveness failure: a
> `Stop` hook that dies mid-session, where nothing notices until the next `/compact` because the only
> record of what happened is written at `SessionStart`. So `lts doctor` runs twelve checks in four
> kinds: **inventory** (is each part present — including that each of the
> four hooks `lts` owns is wired and its script still exists on disk, since projects point at
> `~/tools` by absolute path; a hook you wired yourself is outside what that check looks at),
> **invariants** (is the sleep mark behind the capture mark, is the measured context inside the
> window, and — `sleep_evidence` — is *anything written* where the sleep mark says consolidation
> reached, which is the only check that reads that claim rather than comparing it with another
> claim), **trend**, which reads the sidecar's `health.jsonl` — the per-run log that records what
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

> **`capture_live`** is the only check that detects a dead `Stop` hook
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
> resets by itself. The original argument against the exemption was that `stop.py` is also what
> disarms the flag, making a hook that is not running the one state where it stays armed across
> prompts — exactly the state an exemption would have blinded the check in. Since the latch gained a
> bound that is no longer the only such state: a flag can also outlive its turn and sit there stale.
> The conclusion is unchanged and now rests on something simpler — an exemption keyed on the flag
> would be keyed on a file whose age the check would have to judge anyway, and `capture_live`
> already asks a question the sleep answers correctly.
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

> **The per-client split is a one-way door.** On a project installed before 2026-10-30, the first
> hook of any kind to run out of the pulled clone — in practice `UserPromptSubmit`, on the first
> prompt of the session — or any `lts` command that touches the sidecar (`lts stm clear`, `lts
> anchor write`) **moves** three files into `.ai_memory/clients/claude_code/` —
> `stm/capture-mark.json`, `turn-state.json` and `health.jsonl` — and writes a `.adopted` marker
> beside them, so it is attempted once rather than on every hook. The move is best-effort: a file
> that fails to move withholds the marker, so the next run tries again instead of recording a
> migration that did not happen.
>
> There is no downgrade. A clone checked out back to a pre-split commit looks for those three files
> at their flat paths, where they no longer are. It therefore reads an **empty** capture mark, which
> means "nothing captured yet" — and `watermark.exchanges_after` replays the whole transcript, which
> is append-only across `--resume` and can span months, into the shared STM buffer. That follows
> from moving the files rather than copying them, not from a defect in how they are moved; it is
> worth writing down because these projects take code by checking out the clone, so an ordinary
> `git checkout` reaches it.
>
> The replay is the lesser harm. Pre-split code also writes `PreCompact`'s *snapshot* claim back
> into `sleep-mark.json`, so an ordinary `/compact` again advances the consolidation frontier with
> nothing consolidated — the bug commit `4381505` on this branch fixed, and the one whose
> `sleep_evidence` report cannot be told apart from material nobody holds at all.
>
> So roll the clone **forward**, not back. If you must downgrade anyway, do it by hand first: move
> `clients/claude_code/capture-mark.json` back to `stm/capture-mark.json`, and `turn-state.json` and
> `health.jsonl` back to the sidecar root. Delete the `.adopted` marker with them — otherwise a
> later roll-forward finds the migration already recorded and leaves the flat files orphaned, which
> is the same replay by a longer route.

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
| `lts doctor [--json] [--transcript PATH] [--client ID]` | Twelve health checks: inventory, invariants, trend and liveness. Exits non-zero on a failure. `--transcript` enables the two that cannot answer without one — `capture`, which parses the whole file, and `pressure`, which reads only its end — and lets `capture_live` reach a verdict instead of a `skip`; hooks omit it. `--client` says whose state to report on, default `claude_code`: `marks`, `capture` and `capture_live` read one client's capture mark — `marks` its snapshot mark as well — and `capture` and `capture_live` also parse its transcript, with that client's parser, and an unknown id is refused at exit 2 rather than reported on as an empty state directory. `--json` prints `{"worst": <level>, "client": <id>, "checks": [{"id", "level", "message", "fix"}, ...]}`, **twelve** objects, one per check in report order, `level` one of `ok`/`warn`/`skip`/`fail` and `fix` possibly `null`. |
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
