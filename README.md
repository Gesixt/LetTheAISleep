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
| **STM** | a working buffer of facts/decisions as they happen | `.ai_memory/stm/<session>.md` | until the next sleep |
| **LTM** | the linked knowledge graph | Basic Memory Markdown vault | permanent |

- **While working** Claude appends key facts to STM (`lts stm append`) — cheap, survives `/compact`.
- **`/sleep`** reads the STM buffer, writes/links long-term notes (zero loss), rebuilds embeddings, then clears STM.
- **`/recall`** retrieves by graph priority (anchor links first) + semantic search, loading only what's relevant.
- **`/memory-status`** shows a dashboard of both tiers and flags when to sleep or reindex.
- **Hooks** (run by Claude Code automatically): `PreCompact` snapshots raw material as a backstop,
  `SessionStart` injects the anchor or forces an unfinished sleep to complete, `UserPromptSubmit`
  adds escalating "time to sleep" pressure as context fills.

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
#   e.g.  python ~/tools/LetTheAISleep/install.py --target ~/code/netprint --project netprint
#   (omit --target to use the current directory; omit --project to be prompted)
```

`install.py` prints the remaining manual steps — run them **from the target project**:

```bash
cd /path/to/your/project

# 3. create a Basic Memory project (the vault) and build its vector index
basic-memory project add <project-name> <vault-path>
#   e.g.  basic-memory project add netprint ~/code/netprint/.ai_vault
basic-memory reindex --embeddings -p <project-name>
#   e.g.  basic-memory reindex --embeddings -p netprint

# 4. REGISTER the Basic Memory MCP server with Claude Code (required — the skills call its tools)
claude mcp add basic-memory -- basic-memory mcp
#   (or, via the official plugin:)
#   claude plugin marketplace add basicmachines-co/basic-memory
#   claude plugin install basic-memory@basicmachines-co

# 5. restart Claude Code in the target project so it loads the MCP server, hooks and skills
```

`install.py` also reminds you to add `.claude/settings.json`, `.ai_memory/` and `config.toml`
to the **target project's** `.gitignore` (they contain machine-specific absolute paths).

Use the **same `<project-name>`** in step 3 that you passed to `install.py` in step 2 — the
installer already wrote it into `config.toml` (`[vault] project`), so the skills target the
right vault automatically.

> Without step 4 the `/sleep` and `/recall` skills cannot read or write long-term notes —
> registering the Basic Memory MCP is **required**, not optional.

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

## Usage

**Daily flow**
1. Work normally. When a real decision, number, or name comes up, it gets captured to the STM buffer.
2. As context fills, the `UserPromptSubmit` hook nudges you (~60%) and then insists (~80%) to sleep.
3. Run **`/sleep`** at a natural break (or when nudged). It consolidates STM → linked long-term notes,
   rebuilds embeddings, clears STM, and you can `/compact`.
4. Next session, the `SessionStart` hook injects the **anchor** (a tiny table of contents). Ask a
   question and Claude uses **`/recall`** to pull only the relevant notes.

**Commands & skills**

| Surface | Purpose |
|---------|---------|
| `/sleep` | Consolidate the session into long-term memory without loss, then clear STM. |
| `/recall` | Retrieve relevant memory (anchor/graph links prioritized over pure semantic hits). |
| `/memory-status` | Dashboard: STM buffer, sleep debt, context pressure, LTM note/link counts, embedding freshness. |
| `lts status [--transcript P] [--json]` | The STM/sidecar metrics directly (used by `/memory-status`). |
| `lts stm append/read/clear` | Inspect or manage the per-project STM buffer directly. |
| `basic-memory project info <project>` | LTM counts (Entities/Relations/Isolated) and embedding status. |

**Checking memory load**

```bash
lts status
```
shows the project, STM size, un-slept "sleep debt", context pressure, and anchor freshness. `/memory-status`
combines this with Basic Memory's LTM stats and tells you if you should `/sleep` or reindex.

## License note

This project talks to Basic Memory only over MCP (no linking/embedding). Basic Memory itself is
AGPL-3.0; this repository's own license is declared separately.
