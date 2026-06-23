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
  process over MCP. Install with `uv tool install basic-memory` (or `pip install --user basic-memory`).

## Install

```bash
# 1. install this package (puts the `lts` command on PATH)
uv venv && uv pip install -e '.'

# 2. lay out config, sidecar, hooks and skills into .claude/
python install.py
```

`install.py` prints the remaining manual steps:

```bash
# 3. create a Basic Memory project (the vault) and build its vector index
basic-memory project add <project-name> <vault-path>
#   e.g.  basic-memory project add my-memory /path/to/project/my-memory
basic-memory reindex --embeddings -p <project-name>
#   e.g.  basic-memory reindex --embeddings -p my-memory

# 4. REGISTER the Basic Memory MCP server with Claude Code (required — the skills call its tools)
claude mcp add basic-memory -- basic-memory mcp
#   (or, via the official plugin:)
#   claude plugin marketplace add basicmachines-co/basic-memory
#   claude plugin install basic-memory@basicmachines-co

# 5. restart Claude Code so it loads the MCP server and the new hooks/skills
```

Then set the same project name in `config.toml` so the skills target the right vault:

```toml
[vault]
mode = "per_project"
project = "<project-name>"   # e.g. "my-memory"
```

> Without step 4 the `/sleep` and `/recall` skills cannot read or write long-term notes —
> registering the Basic Memory MCP is **required**, not optional.

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
| `lts status [--session S] [--transcript P] [--json]` | The STM/sidecar metrics directly (used by `/memory-status`). |
| `lts stm append/read/clear --session S` | Inspect or manage the STM buffer directly. |
| `basic-memory project info <project>` | LTM counts (Entities/Relations/Isolated) and embedding status. |

**Checking memory load**

```bash
lts status --session "$CLAUDE_SESSION_ID"
```
shows STM size, un-slept "sleep debt", context pressure, and anchor freshness. `/memory-status`
combines this with Basic Memory's LTM stats and tells you if you should `/sleep` or reindex.

## License note

This project talks to Basic Memory only over MCP (no linking/embedding). Basic Memory itself is
AGPL-3.0; this repository's own license is declared separately.
