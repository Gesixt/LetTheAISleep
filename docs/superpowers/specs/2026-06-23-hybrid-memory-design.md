# Design: Hybrid Memory System for Claude + Obsidian ("Let The AI Sleep")

**Date:** 2026-06-23
**Status:** approved for implementation (v1)
**Requirements source of truth:** `task.md` (Spec #1, v0.1; kept locally, not in the repo)

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
| Memory role | **Obsidian vault is the single store** for long-term memory; Claude Code's built-in file memory is not used |
| Memory tiers | **Three tiers** (context → STM buffer → LTM), like a human; STM is freed during sleep (see §3) |
| Retrieval | **Full RAG + graph hybrid**: local embeddings (vector) + `[[link]]` traversal, 1 level |
| Embeddings | **Local, private**: local multilingual model + local vector DB |
| Indexer stack | **Python** |
| Distribution | **Git-installable product**; vault path configurable (per-project / global mode) |
| Sleep triggers | **Full homeostasis**: manual `/sleep` + hard hooks (`PreCompact`, `SessionStart`, `UserPromptSubmit`) |
| Architecture | **A — MCP server as the single contract** |

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

| Tier | What | Medium | Lifetime | Indexed |
|---|---|---|---|---|
| **Context** | live dialogue | Claude's window | until `/compact` | no |
| **STM** | working buffer: facts/numbers/decisions as they appear | append-only file `.ai_memory/stm/<session>.md` (outside the Obsidian graph) | until the next sleep | **no** (transient) |
| **LTM** | linked knowledge graph | `_Knowledge_Base/` + `_Session_Memory/` | permanent | yes (embeddings) |

**Cycle:**
- **Work** — Claude cheaply flushes key facts into STM (`stm_append`) as it goes: append-only, no linking or embeddings, instant, does not bloat context. STM is durable → survives `/compact`.
- **Sleep** — reads the curated STM buffer (not the raw transcript) + the remaining context → files it into LTM (episode → session note, semantics → KB) with `[[links]]` → **clears STM** (`stm_clear`).
- **Wake-up** — `SessionStart` sees a non-empty STM (the previous session didn't finish sleeping) → triggers completion. STM is the preferred curated path; `pending_consolidation/` remains a lower-quality raw backstop.

**Why a separate tier:** facts are captured at the moment they are stated (less loss risk), sleep processes a clean buffer (cheaper and more accurate, no re-derivation from the transcript), and "freeing the hippocampus during sleep" is modeled literally.

---

## 4. Architecture and components

The system is a git-installable package that turns a local Obsidian vault into Claude Code's long-term memory. Four layers:

```
┌─────────────────────────────────────────────────────────┐
│  Claude Code (STM — active context)                       │
│   Hooks (hard):                                           │
│     SessionStart     → reconcile + completion + Anchor    │
│     PreCompact       → raw snapshot (backstop)            │
│     UserPromptSubmit → escalating pressure 60%/80%        │
│   Skills (soft):                                          │
│     /sleep           → consolidation                      │
│     /recall          → manual retrieval by topic          │
└───────────────┬─────────────────────────────────────────┘
                │ MCP (stdio)
┌───────────────▼─────────────────────────────────────────┐
│  memory-mcp  (Python MCP server — single contract)        │
│   Notes layer (§4): list_notes · get_note · create_note   │
│     · update_note · delete_note · rename_note             │
│     · get_backlinks                                       │
│   Retrieval layer: search_memory · get_anchor             │
│     · update_anchor · reindex                             │
│   STM layer: stm_append · stm_read · stm_clear            │
└───────┬───────────────────────────┬─────────────────────┘
        │                           │
┌───────▼─────────┐        ┌────────▼──────────────────────┐
│ vault_io        │        │ index (RAG engine)            │
│ • read/write    │        │ • local embeddings             │
│ • frontmatter   │        │ • vector DB (sqlite-vec)      │
│ • [[linking]]   │        │ • incremental reindex          │
│ • backlinks     │        │ • full-text fallback           │
└───────┬─────────┘        └────────┬──────────────────────┘
        │                           │
┌───────▼───────────────────────────▼─────────────────────┐
│  Obsidian Vault (LTM — Markdown + [[links]])             │
│   _Session_Memory/ _Knowledge_Base/ _Templates/          │
│   _Project_Memory/Main_index.md  .ai_memory/            │
└──────────────────────────────────────────────────────────┘
```

### Module boundaries

- **`vault_io`** — the only one that touches files and frontmatter; knows the folder structure and link format. Knows nothing about embeddings.
- **`index`** — the only one that knows about embeddings/vector DB; takes text+id, returns ranked ids. Knows nothing about MCP or the folder structure.
- **`memory-mcp`** — a thin layer: translates MCP calls into `vault_io`/`index`, keeps them consistent (wrote a note → reindexed it). No search business logic of its own.
- **Claude layer** (skills + hooks) — orchestration of the sleep/wake scenarios; all the "smarts" of phrasing live in prompts, not in Python.

### Key invariants

- **Write consistency:** any note write through MCP atomically updates the file, the frontmatter (`last_accessed`, `session_refs`), and the vector index. This keeps RAG and the graph in sync without a separate daemon.
- **Source of truth:** the Markdown vault is the single source of truth; `index.db`, `anchor.json`, and backlinks are derived and **always rebuildable** from the vault. A failure of derived data loses no knowledge.

---

## 5. Vault structure and note format

```
<vault>/
├── _Project_Memory/
│   └── Main_index.md             ← root navigator (Anchor entry point)
├── _Session_Memory/
│   └── Session_2026-06-23_1530.md ← one note per session
├── _Knowledge_Base/
│   └── DB_choice.md              ← permanent topical notes
├── _Templates/
│   ├── session.md
│   └── knowledge.md
└── .ai_memory/                   ← internal, not for human eyes
    ├── index.db                  ← sqlite-vec: embeddings + metadata
    ├── anchor.json               ← current "Session Anchor"
    ├── stm/                      ← STM buffer: stm/<session>.md (append-only, outside graph)
    ├── backups/                  ← versions of overwritten notes
    └── pending_consolidation/    ← PreCompact snapshots (raw material for completion)
```

**Topical note frontmatter** (`_Knowledge_Base/*.md`):
```yaml
---
tags: [project, db]
created: 2026-06-23
last_accessed: 2026-06-23
session_refs: ["[[Session_2026-06-23_1530]]"]   # back-links to sessions
---
```

**Session note frontmatter** (`_Session_Memory/*.md`):
```yaml
---
type: session
date: 2026-06-23 15:30
topics: [db-choice, caching]
kb_refs: ["[[DB_choice]]", "[[Cache_strategy]]"]   # which KB notes it links to
status: consolidated        # consolidated | pending
project: <name>             # only for global vault mode
---
```

**Session note body** — coherent narrative with explicit `[[links]]` and preserved numbers (not a summary). Template sections: `## Context` → `## Key decisions` (with numbers/rationale) → `## Open questions` → `## Links`.

**Session Anchor** (`.ai_memory/anchor.json`) — a lightweight JSON injected by the `SessionStart` hook:
```json
{
  "updated": "2026-06-23 15:30",
  "last_session": "[[Session_2026-06-23_1530]]",
  "active_topics": ["db-choice", "caching"],
  "active_notes": ["[[DB_choice]]", "[[Cache_strategy]]", "[[Main_index]]"]
}
```

### Two key ideas

1. **Link duplication** — `[[links]]` are stored both in the body (for the Obsidian graph and humans) and in frontmatter `kb_refs`/`session_refs` (for fast deterministic graph traversal without parsing Markdown). `vault_io` keeps them in sync.
2. **The Anchor is a table of contents, not the contents** — tiny (names + links); note bodies are loaded *on demand* via `get_note`/`search_memory`. This keeps the context budget < 50%.

---

## 6. MCP tool contract

Tool names from Spec §4 are preserved as the contract. Transport — **stdio MCP** (local process, no network).

### Notes layer (wrapper over `vault_io`)

| Tool | Input | Output | Side effects |
|---|---|---|---|
| `list_notes` | `path?`, `tag?` | list of `{title, path, tags, last_accessed}` | — |
| `get_note` | `title` | body + frontmatter | updates `last_accessed` |
| `create_note` | `title`, `content`, `folder` | path | indexing + back-link creation |
| `update_note` | `title`, `content` | path | reindex + link resync |
| `delete_note` | `title` | ok | delete file + purge index + clean/mark broken links |
| `rename_note` | `old_title`, `new_title` | new path | rename + rewrite all `[[old]]`→`[[new]]` + reindex (atomic) |
| `get_backlinks` | `title` | notes linking here | — |

Delete/rename belong to Claude (the memory is its own); the human edits only "if needed" (picked up via reconcile, see below). `append_to_session` from Spec §4 is reinterpreted as `stm_append` (see the STM layer below) — it used to "accumulate into the session note", now it accumulates into the STM buffer.

### Retrieval and Anchor layer

| Tool | Input | Output |
|---|---|---|
| `search_memory` | `query`, `k?`, `traverse?=true` | ranked list of `{title, snippet, score, source}` |
| `get_anchor` | — | contents of `anchor.json` |
| `update_anchor` | `active_topics`, `active_notes` | updated Anchor |
| `reindex` | `scope?` (full/partial) | reindex report |

### STM layer (working buffer, see §3)

| Tool | Input | Output | Side effects |
|---|---|---|---|
| `stm_append` | `text` | ok | append-only write to `.ai_memory/stm/<session>.md`; **no indexing** (transient) |
| `stm_read` | `session?` | buffer contents | — |
| `stm_clear` | `session?` | ok | clear the buffer after successful consolidation |

STM is **not embedded** and not part of the graph — a cheap volatile store. Appending is instant and does not bloat context.

### `search_memory` — heart of the RAG+graph hybrid

1. **Vector:** embeds `query`, takes top-K similar chunks from `index`.
2. **Graph:** `active_notes` from the Anchor + `kb_refs` of the last session → adds notes 1 **level** of links deep (no recursion, the Retrieval block).
3. **Merge and priority:** graph results get a ranking boost (direct links beat semantic proximity, the Retrieval block). Dedup, trim to `k`.
4. **Fallback:** embeddings unavailable → degrade to full-text grep + graph (system does not crash).

Returns **snippets**, not whole notes. Claude pulls the full body deliberately via `get_note` — retrieval does not bloat the context by itself.

### Reconcile — robustness to human edits

Approach A has no watcher daemon (the daemon is in Future). Human edits made directly in Obsidian are picked up cheaply:

- `vault_io` stores `mtime`/hash of each file in `index.db`.
- The **`SessionStart` hook** reconciles the delta on startup: `mtime` newer than index → reindex; file gone → purge; new → add.
- Manual `reindex` (full/partial rebuild) as a last resort.

---

## 7. Data flows

### Flow A — SLEEP (consolidation)

Three entries converge on the `/sleep` skill:
- **Manual:** calling `/sleep`.
- **Pressure:** `UserPromptSubmit` at ~80% injected a hard "time to sleep" → Claude calls `/sleep`.
- **Backstop:** `PreCompact` saved the raw material → completion on the next `SessionStart`.

**5 steps (the Sleep block):**
```
1. COLLECT   → stm_read (curated buffer) + significant remaining context;
               for completion without STM — from pending_consolidation/ (raw)
2. EXTRACT   → key decisions, ALL numbers, names, architectural choices, contentious points
3. WRITE     → create_note/update_note for the session; status: pending→consolidated
4. LINK      → topic in KB? update_note (augment) : create_note in _Knowledge_Base/.
               Place [[links]] both ways (body + frontmatter kb_refs/session_refs)
5. FREE STM + UPDATE ANCHOR + CLEAR → stm_clear; update_anchor(...); then /compact
```

Step 1 now reads the **STM buffer** as the main curated input (instead of re-deriving everything from the transcript); `pending_consolidation/` is the fallback raw input if STM wasn't kept. Step 5 **frees STM** (`stm_clear`) — an analogue of unloading the hippocampus during sleep.

**Zero-loss guarantee (Spec §6):** step 2 is extraction *with preservation*, not compression. Prompt rule: "every number, name, rationale is carried over verbatim; when in doubt, carry it over." Step 4 builds coherent linked prose, not a bullet summary.

**Sleep self-check:** before `/compact` (step 5) the skill cross-checks "mentioned in dialogue vs written into the note" for key entities (numbers, proper nouns); appends anything missing. Only then clears the context.

### Flow B — WAKE-UP (session start)

Executed by the `SessionStart` hook + `/recall` if needed:
```
1. RECONCILE → reconcile the index (pick up human edits)
2. COMPLETE  → non-empty STM or pending_consolidation/? → inject "finish sleeping this first" (Flow A).
               STM (curated) takes priority over pending_consolidation (raw)
3. ANCHOR    → inject anchor.json (tiny: names + links)
4. LAZY      → bodies NOT loaded; Claude pulls on demand (get_note by link / search_memory by topic)
```

**Startup budget:** only the Anchor (~a few KB) + the last session note on demand → "≤ 3–5 s" and "< 50% of window" (Spec §6).

### Flow C — WORK (on-the-fly linking, the Linker block)

Significant answer: topic overlaps → `search_memory` → place a `[[link]]`; new topic → `create_note` immediately with a link. The Anchor is updated on topic change (`update_anchor`). In parallel, key facts/numbers/decisions are flushed to the STM buffer (`stm_append`) as work proceeds — cheaply and immediately, so sleep has a ready curated input.

**Symmetry:** sleep unloads knowledge into the graph and clears the context; wake-up pulls a minimum back. Between them Claude's context stays "light".

---

## 8. Indexing and vector search (`index`)

**Embeddings (local):**
- Via **FastEmbed** (ONNX, lightweight) or **sentence-transformers** — final choice after a quick benchmark on the hardware (locked in the implementation plan).
- Default model — **multilingual** (notes may be non-English): candidates `intfloat/multilingual-e5-small`, `paraphrase-multilingual-MiniLM`.
- Provider behind an `Embedder` interface (`embed(texts) -> vectors`) — closes "local", leaves the door open for cloud (Future) without rework.

**Vector DB:** **sqlite-vec** — a single `.ai_memory/index.db` file, zero external services, trivial to distribute and back up. Per chunk it stores: `vector`, `note_path`, `chunk_text`, `note_title`, `mtime`, `hash`.

**Chunking:** by Markdown headings (`##`) with a soft size limit and small overlap — preserves semantic boundaries. Frontmatter is not embedded; `tags`/`title` are prepended to the chunk for findability.

**(Re)indexing — incremental by hash:**
```
create/update/append → reindex ONLY this note (drop old chunks → re-chunk → embed → insert)
delete               → drop chunks by note_path
SessionStart reconcile → mtime changed → compare hash → differs → reindex; no file → drop
reindex (manual)     → full rebuild
```
The hash check avoids embedding unchanged content (mtime may change without a content change).

**Hybrid ranking** (in `search_memory`, over `index`):
```
score = w_vec * cosine_similarity
      + w_graph * is_linked_from_anchor      # boost direct links (the Retrieval block)
      + w_recency * freshness(last_accessed)
```
Weights are in config; default prioritizes the graph over semantics (the Retrieval block).

**Performance:** incremental indexing = embed one note per write; sqlite-vec search over thousands of chunks — tens of ms. Well within "3–5 s".

---

## 9. Distribution, configuration, installation

### Repository structure
```
let-the-ai-sleep/
├── memory_mcp/              ← Python package
│   ├── server.py            ← MCP entry point (stdio)
│   ├── vault_io.py
│   ├── index.py
│   ├── embedder.py
│   └── config.py
├── claude/                  ← Claude Code integration (laid out by install.py)
│   ├── skills/sleep/
│   ├── skills/recall/
│   └── hooks/               ← session_start.py, pre_compact.py, user_prompt_submit.py
├── templates/               ← _Templates/session.md, knowledge.md
├── install.py               ← installer wizard (idempotent)
├── config.example.toml
├── pyproject.toml
└── README.md
```

### Config — `config.toml` (personal, in `.gitignore`; repo ships `config.example.toml`)
```toml
[vault]
mode = "per_project"        # per_project | global
path = "./.ai_vault"        # for per_project — relative to the project
# global_path = "~/ai_memory_vault"   # for mode=global

[embedding]
provider = "fastembed"      # fastembed | sentence_transformers | (future) cloud
model = "intfloat/multilingual-e5-small"

[retrieval]
k = 8
traverse_depth = 1          # Retrieval block: non-recursive
weights = { vec = 1.0, graph = 1.5, recency = 0.3 }

[sleep]
pressure_warn = 0.60        # UserPromptSubmit: soft reminder
pressure_force = 0.80       # hard "time to /sleep"
```

### Vault modes
- **`per_project`** — each project has its own `.ai_vault/` next to the code; memory isolated per project.
- **`global`** — one shared vault for all projects; session notes are tagged with `project` to avoid mixing.

### `install.py` — idempotent wizard
```
1. Check Python version, install dependencies (pyproject)
2. Read/ask vault mode and path
3. Bootstrap the vault: create folders + copy _Templates/
4. Register the MCP server in .mcp.json (project-level or ~/.claude — per mode)
5. Install skills and hooks into .claude/ (skills/, settings.json)
6. Download the embedding model (first run) + build the index from existing notes
7. Print "what's next" + smoke test (create_note → search_memory)
```

### Claude Code wiring
- MCP — in `.mcp.json` (project-level) or user-level (per vault mode).
- Hooks — in `.claude/settings.json` (`SessionStart`, `PreCompact`, `UserPromptSubmit`).
- Skills — folders in `.claude/skills/`.
- Everything is versioned templates in the repo; `install.py` lays them out with path substitution.

---

## 10. Error handling

Principle: memory must never silently lose data and must never crash the session.

| Failure | Behavior | Why |
|---|---|---|
| Embedding model failed to load | `search_memory` → full-text grep + graph; warning | Worse search, system alive; graph always works |
| Index corrupted/missing | auto-`reindex` from vault on startup | Vault is the source of truth, index is derived |
| Write conflict (human + MCP) | MCP write wins; previous version → `.ai_memory/backups/` | Spec §7: overwrite + history, no loss |
| `PreCompact` didn't finish/crashed | raw transcript already dumped to `pending_consolidation/` first | Hard "don't lose" guarantee |
| `rename_note` aborted | transaction: all back-links rewritten or rolled back | Otherwise the graph breaks |
| `/sleep` interrupted | note `status: pending`; STM **not cleared** until the write is confirmed; next `SessionStart` completes | Self-recovery; STM cleared only after success |
| STM buffer lost/corrupted | sleep falls back to `pending_consolidation/` (raw); loss non-zero but not catastrophic | STM is transient; the backstop remains |
| vault unavailable | MCP starts, tools return an explicit error, do not crash | Claude gets a message, not a crash |

**Cross-cutting invariant:** the Markdown vault is the single source of truth; everything derived is rebuildable.

---

## 11. Testing (TDD, by layers)

- **`vault_io` (unit):** CRUD; frontmatter parse/resync; back-link rewrite on rename; "body ↔ frontmatter links" sync. On a tmp vault.
- **`index` (unit):** heading chunking; incremental reindex by hash; chunk deletion; **fake `Embedder`** (deterministic vectors) — fast, no network.
- **`search_memory` (integration):** hybrid ranking — a direct `[[link]]` from the Anchor beats a purely semantic hit (the Retrieval block); traversal depth = 1.
- **STM (unit):** `stm_append` (append-only, survives a "restart"), `stm_read`, `stm_clear`; STM does not enter the index/graph; `stm_clear` only after a confirmed write to LTM.
- **Reconcile:** edit/delete a file outside MCP → correct pickup on startup.
- **Sleep (scenario, Spec §6):** a dialogue with numbers/names → `/sleep` → all numbers/names are in the note.
- **Wake-up (e2e, golden Sleep scenario):** DB choice → sleep → clear → new session → "why PostgreSQL?" → answer recovered via `[[link]]`.
- **Context budget (Spec §6):** Anchor + on-demand loading stay < 50% of the window.

The fake `Embedder` decouples search/ranking logic from the ML model — tests are deterministic and fast.

---

## 12. Explicitly deferred (Future, Spec §7)

- Watcher daemon for a live index + Obsidian Local REST API integration (Approach C).
- Cloud embedding provider (the `Embedder` interface is already ready for it).
- Auto-updating model weights (LoRA).
- A full graph engine (currently — 1-level traversal).
- Version-conflict resolution beyond "overwrite + backup/Git".
- Background rewriting of old notes to add cross-links (Spec §5, step 8).

---

## 13. Mapping to acceptance criteria (Spec §6)

| Criterion | How it's covered |
|---|---|
| After `/sleep`, all numbers/names/relationships preserved | Zero-loss sleep (§7) + sleep self-check + scenario test |
| New notes auto-linked (incl. back-links) | On-the-fly linking (Flow C) + two-way links body+frontmatter |
| Context load ≤ 3–5 s | Lazy loading (Flow B) + sqlite-vec (tens of ms) |
| Memory tokens < 50% of window | Anchor-as-TOC + snippets in `search_memory` + budget test |

> Note: block references (STM, LTM, Linker, Sleep, Retrieval) and numbered §4/§6/§7 point to sections of the source spec `task.md`.