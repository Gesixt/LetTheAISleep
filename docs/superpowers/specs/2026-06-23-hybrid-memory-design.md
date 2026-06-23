# Design: Hybrid Memory System for Claude + Obsidian ("Let The AI Sleep")

**Date:** 2026-06-23
**Status:** approved for implementation (v1.1)
**Requirements source of truth:** `task.md` (Spec #1, v0.1; kept locally, not in the repo)

> **v1.1 change:** after surveying the field, the long-term store + CRUD + hybrid (full-text + vector) retrieval are delegated to **Basic Memory** (an existing local-first Obsidian/Markdown memory MCP that already uses our chosen stack: FastEmbed + SQLite). This project is now a **thin Claude Code integration layer on top of it** — the genuinely new parts (STM tier, sleep, hook-enforced homeostasis, anchor, auto-linking assist). No custom MCP server is built.

---

## 0. North Star

> **Keep Claude's context from filling up; if it does fill up, let it "sleep" (`/sleep`) without losing knowledge.**

Two cross-cutting requirements follow from this goal and are wired into every module:

1. **Context budget.** Memory must *save* context, not bloat it. On wake-up we load only the Anchor + the last session note + direct links (1 level), not the whole vault. Target — memory tokens < 50% of the window (Spec §6).
2. **Zero-loss sleep.** `/sleep` writes coherent prose preserving every number/name/relationship with `[[links]]`, not a lossy summary. Only then may the context be cleared.

---

## 1. Locked-in decisions

| Topic | Decision |
|---|---|
| Runtime | **Claude Code (CLI)** |
| Memory role | **Obsidian/Markdown vault is the single store**; Claude Code's built-in file memory is not used |
| LTM substrate | **Build on Basic Memory** (existing MCP): store, CRUD, hybrid full-text + vector search, graph, edit-sync. Local/private, AGPL-3.0 |
| Our package | **Claude Code integration layer**: skills + hooks + installer. **No custom MCP server** |
| Memory tiers | **Three tiers** (context → STM buffer → LTM), like a human; STM is freed during sleep (see §3) |
| Retrieval | **Full RAG + graph hybrid** — vector + full-text via Basic Memory `search`; `[[link]]` traversal (1 level) via `build_context` |
| Embeddings | **Local, private** — provided by Basic Memory (FastEmbed + SQLite) |
| Distribution | **Git-installable**; depends on `basic-memory`; vault path via Basic Memory projects (per-project / global) |
| Sleep triggers | **Full homeostasis**: manual `/sleep` + hard hooks (`PreCompact`, `SessionStart`, `UserPromptSubmit`) |

---

## 2. Reliability principle: soft vs hard

The key design principle is splitting mechanisms by who executes them:

| Layer | Executor | Reliability | Responsible for |
|---|---|---|---|
| **Skills / prompts** | the model itself (probabilistic) | soft, degrades when context is full | *quality* of sleep, ranking, what counts as important |
| **Claude Code hooks** | the harness (deterministic) | hard, independent of the model | *guarantees*: that sleep happens and data is not lost |

Prompts bias behavior strongly but do not guarantee it, and reliability drops exactly when the context is near full — precisely when `/sleep` is needed most. Therefore **guarantees are built on hooks** (an analogue of homeostasis: "sleep pressure" is accumulated by the harness, not the model), and **quality** is built on skills.

---

## 3. Three memory tiers (cognitive model)

Since Claude Code's built-in memory is not used, we reproduce the human three-tier scheme. Between the volatile context and the permanent graph sits the **STM buffer** — an analogue of the hippocampus: a fast intermediate store that accumulates during work and is *freed during sleep*, filing knowledge into LTM.

| Tier | What | Medium | Owner | Lifetime | Indexed |
|---|---|---|---|---|---|
| **Context** | live dialogue | Claude's window | harness | until `/compact` | no |
| **STM** | working buffer: facts/numbers/decisions as they appear | append-only file `.ai_memory/stm/<session>.md` (sidecar, outside the Basic Memory note tree) | **us** | until the next sleep | **no** (transient) |
| **LTM** | linked knowledge graph | Markdown vault | **Basic Memory** | permanent | yes (FastEmbed + SQLite) |

**Cycle:**
- **Work** — Claude cheaply appends key facts into STM as it goes: append-only, no linking or embeddings, instant, does not bloat context. STM is durable → survives `/compact`.
- **Sleep** — reads the curated STM buffer (not the raw transcript) + the remaining context → files it into LTM via Basic Memory `write_note`/`edit_note` (episode → session note, semantics → KB note) with `[[links]]` → **clears STM**.
- **Wake-up** — `SessionStart` sees a non-empty STM (the previous session didn't finish sleeping) → triggers completion. STM is the preferred curated path; `pending_consolidation/` remains a lower-quality raw backstop.

**Why a separate tier:** facts are captured at the moment they are stated (less loss risk), sleep processes a clean buffer (cheaper and more accurate, no re-derivation from the transcript), and "freeing the hippocampus during sleep" is modeled literally.

---

## 4. Architecture and components

This project ships a Claude Code integration layer; Basic Memory provides the memory substrate.

```
┌─────────────────────────────────────────────────────────┐
│  Claude Code (context — active window)                    │
│                                                           │
│   OUR PACKAGE (skills + hooks + sidecar files):           │
│     Hooks (hard, deterministic):                          │
│       SessionStart    → inject Anchor; detect un-slept    │
│                         STM/pending → force completion    │
│       PreCompact      → dump raw transcript snapshot      │
│       UserPromptSubmit→ escalating sleep pressure 60/80%  │
│     Skills (soft):                                        │
│       /sleep          → consolidation (reads STM → LTM)   │
│       /recall         → retrieval by topic                │
│     Sidecar files (.ai_memory/, plain files, no MCP):     │
│       stm/<session>.md · anchor.json · pending_consol./   │
└───────────────┬─────────────────────────────────────────┘
                │ MCP (stdio) — only for LTM operations
┌───────────────▼─────────────────────────────────────────┐
│  Basic Memory MCP (existing, AGPL-3.0) — LTM substrate     │
│   CRUD:   write_note · read_note · edit_note               │
│           · move_note · delete_note · list_directory      │
│   Search: search (hybrid full-text + vector, FastEmbed)   │
│   Graph:  build_context (link traversal) · relations      │
│   Sync:   recent_activity · sync_status (picks up human   │
│           edits made in Obsidian)                          │
│   Multi:  create_memory_project / list_memory_projects    │
└───────────────┬─────────────────────────────────────────┘
                │
┌───────────────▼─────────────────────────────────────────┐
│  Markdown Vault (LTM) — owned by Basic Memory             │
│   frontmatter + Markdown · [[links]] · SQLite index       │
│   organized with our convention: _Session_Memory/,        │
│   _Knowledge_Base/ (as folders/types within the project)  │
└──────────────────────────────────────────────────────────┘
```

### What we build vs reuse

| Concern | Provided by | Notes |
|---|---|---|
| Note store, CRUD | **Basic Memory** | `write_note`/`read_note`/`edit_note`/`move_note`/`delete_note` |
| Hybrid retrieval (vector + FTS) | **Basic Memory** | `search` — our chosen stack (FastEmbed + SQLite) |
| 1-level graph traversal | **Basic Memory** | `build_context` (depth-bounded) |
| Pick up human edits | **Basic Memory** | auto-sync + `sync_status` (replaces our "reconcile") |
| Per-project / global vault | **Basic Memory** | memory projects |
| **STM buffer tier** | **us** | sidecar files + skill writes |
| **`/sleep` consolidation** | **us** | skill |
| **`/recall`** | **us** | skill (wraps `search` + `build_context` with graph priority) |
| **Hook homeostasis** | **us** | `SessionStart` / `PreCompact` / `UserPromptSubmit` |
| **Session Anchor** | **us** | `anchor.json` sidecar, hook-injected |
| **Auto-linking assist** | **us** | skill-level prompt logic over `search` results |

### Module boundaries

- **Basic Memory** — the only component that touches the vault index and note files for LTM. We treat it as a black-box MCP contract.
- **Sidecar (`.ai_memory/`)** — STM, anchor, pending snapshots. Plain files we own; kept **outside** the Basic Memory note tree so it does not index them. Managed by skills (via native file tools) and hooks (via thin scripts).
- **Skills** — all the "smarts" of sleep/recall/linking phrasing live here, in prompts.
- **Hooks** — deterministic guarantees; pure file/transcript logic, no MCP dependency.

### Key invariants

- **Source of truth:** the Markdown vault (Basic Memory) is the single source of truth for LTM; its SQLite index is derived and rebuildable. STM is transient and explicitly non-authoritative.
- **Sidecar isolation:** `.ai_memory/` never enters the Basic Memory project directory, so STM/anchor scratch is never embedded or surfaced as a note.

---

## 5. Vault layout and note format

LTM lives in a Basic Memory project (default `~/basic-memory`, or a per-project path). We keep the Spec's taxonomy as an organizational convention via note folders/types:

```
<basic-memory project>/        ← owned & indexed by Basic Memory
├── _Project_Memory/Main_index.md
├── _Session_Memory/Session_2026-06-23_1530.md
├── _Knowledge_Base/DB_choice.md
└── _Templates/{session,knowledge}.md

<project>/.ai_memory/          ← OUR sidecar (gitignored, NOT a Basic Memory note folder)
├── anchor.json                ← current "Session Anchor"
├── stm/<session>.md           ← STM buffer (append-only)
└── pending_consolidation/     ← PreCompact raw snapshots
```

**Session note frontmatter** (written via `write_note`):
```yaml
---
type: session
date: 2026-06-23 15:30
topics: [db-choice, caching]
kb_refs: ["[[DB_choice]]", "[[Cache_strategy]]"]
status: consolidated        # consolidated | pending
project: <name>             # for global mode disambiguation
---
```
Body — coherent narrative with explicit `[[links]]` and preserved numbers (not a summary). Sections: `## Context` → `## Key decisions` → `## Open questions` → `## Links`.

**Session Anchor** (`.ai_memory/anchor.json`) — tiny JSON injected by `SessionStart`:
```json
{
  "updated": "2026-06-23 15:30",
  "last_session": "[[Session_2026-06-23_1530]]",
  "active_topics": ["db-choice", "caching"],
  "active_notes": ["[[DB_choice]]", "[[Cache_strategy]]", "[[Main_index]]"]
}
```

**Two key ideas**
1. The Anchor is a table of contents, not the contents — tiny (names + links); bodies are pulled on demand via `read_note`/`search`. Keeps the context budget < 50%.
2. Links live in the body (Basic Memory parses relations) and are mirrored in frontmatter `kb_refs` for cheap deterministic anchor-building by our skills.

---

## 6. Integration contract (how our layer uses Basic Memory)

We do not define MCP tools; we consume Basic Memory's. Mapping of conceptual operations:

| Our operation | Basic Memory call(s) | Notes |
|---|---|---|
| create/update note | `write_note` / `edit_note` | frontmatter + body |
| read note | `read_note` | |
| list notes | `list_directory` / `search` | |
| delete note | `delete_note` | |
| rename/move note | `move_note` | **verify** relation/back-link rewrite at impl time; if incomplete, our skill patches references |
| backlinks / neighbors | `build_context` / relations | |
| hybrid search (RAG) | `search` | full-text + vector already built in |
| 1-level graph traversal | `build_context` (depth=1) | graph priority applied by our skill ranking |
| pick up human edits | auto-sync + `sync_status` | replaces a custom reconcile loop |
| per-project / global vault | `create_memory_project` / `list_memory_projects` | |

**STM & Anchor (no MCP — plain sidecar files):**

| Our operation | Mechanism |
|---|---|
| `stm_append(text)` | skill/hook appends a line to `.ai_memory/stm/<session>.md` |
| `stm_read()` | read the file |
| `stm_clear()` | truncate/rotate the file after a confirmed LTM write |
| `get_anchor` / `update_anchor` | read/write `.ai_memory/anchor.json` |

**`/recall` ranking (graph-priority hybrid, Spec retrieval block):** call `search` for semantic candidates; pull `active_notes` from the Anchor + last-session `kb_refs`, expand 1 level via `build_context`; boost graph-linked hits over purely semantic ones; return snippets, fetch full bodies via `read_note` only when needed.

---

## 7. Data flows

### Flow A — SLEEP (consolidation, `/sleep` skill)

Entries: manual `/sleep`; `UserPromptSubmit` pressure at ~80%; or `PreCompact` backstop → completion next `SessionStart`.

```
1. COLLECT → stm_read (curated buffer) + significant remaining context;
             without STM → from pending_consolidation/ (raw)
2. EXTRACT → key decisions, ALL numbers, names, architectural choices, contentious points
3. WRITE   → write_note/edit_note for the session note; status: pending→consolidated
4. LINK    → topic exists in KB? edit_note (augment) : write_note in _Knowledge_Base/.
             Place [[links]] both ways (body + frontmatter kb_refs)
5. FREE STM + UPDATE ANCHOR + CLEAR → stm_clear; rewrite anchor.json; then /compact
```

Step 1 reads the **STM buffer** as the main curated input; `pending_consolidation/` is the raw fallback. Step 5 **frees STM** — an analogue of unloading the hippocampus during sleep.

**Zero-loss guarantee (Spec §6):** step 2 is extraction *with preservation*, not compression — "every number, name, rationale carried over verbatim; when in doubt, carry it over." Step 4 builds coherent linked prose, not a bullet summary.

**Sleep self-check:** before `/compact` (step 5) the skill cross-checks "mentioned in dialogue vs written into the note" for key entities (numbers, proper nouns); appends anything missing. Only then clears the context.

### Flow B — WAKE-UP (`SessionStart` hook + `/recall`)

```
1. SYNC      → Basic Memory auto-sync / sync_status picks up human edits
2. COMPLETE  → non-empty STM or pending_consolidation/? → inject "finish sleeping this first" (Flow A).
               STM (curated) over pending_consolidation (raw)
3. ANCHOR    → inject anchor.json (tiny: names + links)
4. LAZY      → bodies NOT loaded; Claude pulls on demand (read_note by link / search by topic)
```
**Startup budget:** only the Anchor (~a few KB) + the last session note on demand → "≤ 3–5 s" and "< 50% of window" (Spec §6).

### Flow C — WORK (on-the-fly linking, Spec linker block)

Significant answer: topic overlaps → `search` → place a `[[link]]`; new topic → `write_note` immediately with a link. Anchor updated on topic change. In parallel, key facts/numbers/decisions are appended to the STM buffer as work proceeds — cheaply and immediately, so sleep has a ready curated input.

**Symmetry:** sleep unloads knowledge into the graph and clears context; wake-up pulls a minimum back. Between them the context stays "light".

---

## 8. Retrieval & indexing (delegated)

Provided by **Basic Memory** — we do not build it:
- **Embeddings:** local FastEmbed; index in SQLite. Default multilingual model where configurable (notes may be non-English).
- **Hybrid ranking:** full-text + vector fusion inside `search`.
- **Graph:** `build_context` traverses `[[links]]`/relations; we bound depth to 1 (Spec retrieval block: non-recursive).
- **Graph priority (Spec retrieval block):** applied by our `/recall` skill — anchor/linked notes are boosted above purely semantic hits.

We only configure Basic Memory (model/project) and add the graph-priority heuristic at the skill layer. Performance targets ("3–5 s") are met by Basic Memory's SQLite search + our lazy loading.

---

## 9. Distribution, configuration, installation

### Repository structure
```
let-the-ai-sleep/
├── claude/                  ← Claude Code integration (laid out by install.py)
│   ├── skills/sleep/        ← /sleep
│   ├── skills/recall/       ← /recall
│   └── hooks/               ← session_start.py, pre_compact.py, user_prompt_submit.py
├── templates/               ← _Templates/session.md, knowledge.md (bootstrap)
├── install.py               ← installer wizard (idempotent)
├── config.example.toml
└── README.md
```
No `memory_mcp/` package — the server is Basic Memory.

### Dependency
- **Basic Memory** (`uv tool install basic-memory`), AGPL-3.0. **License note:** Basic Memory is a separate process the user installs; our package interacts with it only over MCP (no linking/embedding), so our own code can carry its own license — but this must be stated in the README and revisited if we ever vendor or fork it.

### Config — `config.toml` (personal, gitignored)
```toml
[vault]
mode = "per_project"        # per_project | global
project = "lts-<name>"      # Basic Memory project name
# path = "~/ai_memory_vault"  # used when creating the project

[sleep]
pressure_warn = 0.60        # UserPromptSubmit: soft reminder
pressure_force = 0.80       # hard "time to /sleep"
```

### `install.py` — idempotent wizard
```
1. Check that basic-memory is installed (offer: uv tool install basic-memory)
2. Read/ask vault mode + path → create/select a Basic Memory project
   (create_memory_project) and bootstrap folders + _Templates/
3. Register Basic Memory MCP for Claude Code (claude plugin / mcp add)
4. Install our skills + hooks into .claude/ (skills/, settings.json)
5. Create .ai_memory/ sidecar (gitignored), outside the project note tree
6. Smoke test: write_note → search → read_note ; STM append/clear
```

### Claude Code wiring
- Basic Memory MCP registered per its docs (`claude plugin install basic-memory@basicmachines-co`).
- Hooks in `.claude/settings.json` (`SessionStart`, `PreCompact`, `UserPromptSubmit`).
- Skills as folders in `.claude/skills/`.

---

## 10. Error handling

| Failure | Behavior | Why |
|---|---|---|
| Basic Memory unavailable/not installed | hooks still dump to `pending_consolidation/`; skills surface a clear "install basic-memory" error | Raw capture never depends on the MCP |
| Semantic search degraded | Basic Memory falls back to full-text; `/recall` still uses graph via `build_context` | Graph path independent of vectors |
| `PreCompact` didn't finish/crashed | raw transcript already dumped to `pending_consolidation/` first | Hard "don't lose" guarantee |
| `move_note` didn't rewrite a back-link | `/recall`/sleep skill detects dangling `[[link]]` and patches it | Defense in depth over the substrate |
| `/sleep` interrupted | session note `status: pending`; STM **not cleared** until LTM write confirmed; next `SessionStart` completes | Self-recovery; STM cleared only after success |
| STM buffer lost/corrupted | sleep falls back to `pending_consolidation/` | STM is transient; backstop remains |
| Human edits in Obsidian | Basic Memory auto-sync + `sync_status` reconciles | We don't maintain a separate index |

**Cross-cutting invariant:** the Markdown vault is the single source of truth; the SQLite index is rebuildable; STM/anchor are scratch.

---

## 11. Testing (TDD, our layer + integration)

We test what we build; Basic Memory is treated as a trusted dependency (smoke-tested, not unit-tested by us).

- **STM (unit):** append-only survives a restart; `stm_read`/`stm_clear`; cleared only after a confirmed LTM write; sidecar stays outside the project tree.
- **Anchor (unit):** `SessionStart` injects a tiny anchor; `update_anchor` round-trips; budget stays < 50% (Spec §6).
- **Hooks (unit):** `PreCompact` always writes a snapshot before anything else; `UserPromptSubmit` escalates at the configured thresholds; `SessionStart` forces completion when STM/pending is non-empty.
- **/recall (integration, Spec retrieval block):** with a seeded Basic Memory project, a direct `[[link]]` from the Anchor beats a purely semantic hit; traversal depth = 1.
- **Sleep (scenario, Spec §6):** dialogue with numbers/names → STM → `/sleep` → `write_note` → all numbers/names present in the note (sleep self-check).
- **Wake-up (e2e, golden):** DB choice → sleep → clear → new session → "why PostgreSQL?" → answer recovered via `[[link]]`.
- **Install (smoke):** `install.py` is idempotent; project created; smoke test passes.

A test Basic Memory project on a tmp path keeps integration tests hermetic and offline.

---

## 12. Explicitly deferred (Future, Spec §7)

- Custom MCP server (only if Basic Memory proves limiting).
- Cloud embedding provider (Basic Memory supports swapping; not needed for local/private v1).
- Auto-updating model weights (LoRA).
- Full graph engine beyond 1-level traversal.
- Version-conflict resolution beyond Basic Memory's behavior + Git history.
- Background rewriting of old notes to add cross-links (Spec §5, step 8).

---

## 13. Mapping to acceptance criteria (Spec §6)

| Criterion | How it's covered |
|---|---|
| After `/sleep`, all numbers/names/relationships preserved | Zero-loss sleep (§7) + sleep self-check + scenario test |
| New notes auto-linked (incl. back-links) | On-the-fly linking (Flow C) + frontmatter mirror + Basic Memory relations |
| Context load ≤ 3–5 s | Lazy loading (Flow B) + Basic Memory SQLite search |
| Memory tokens < 50% of window | Anchor-as-TOC + snippets from `search` + budget test |

> Note: block references (STM, LTM, Linker, Sleep, Retrieval) and numbered §4/§6/§7 point to sections of the source spec `task.md`.