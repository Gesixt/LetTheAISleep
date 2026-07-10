# Design: Team Mode — shared knowledge, personal episodes

**Date:** 2026-07-10
**Status:** approved for implementation
**Builds on:** `2026-06-23-hybrid-memory-design.md` (v1.1)

---

## 0. Problem

Five developers work on one project. Each runs Claude Code with this memory system, and each
model writes long-term notes. Today those writes all land in one flat vault owned by one
machine. Nothing tells a developer's model that a teammate already learned the answer.

The **primary goal is attribution**: knowing *who* learned *what*, so that a model (and a human)
can say "Petr worked this out — read his session note, or go ask him." Avoiding git merge
conflicts and separating draft from vetted knowledge are secondary; they must not be bought at
the cost of a fragmented graph.

---

## 1. North Star (unchanged) and what team mode adds

The north star still holds: keep context from filling up, and sleep without losing knowledge.

Team mode adds one clause: **a developer's model must know what its neighbours learned**, and
must never silently overwrite or duplicate their knowledge.

---

## 2. Key decision: split along the episodic/semantic seam

The vault already has two directories that mean different things. Team mode gives them
different ownership rules rather than inventing a third axis.

| Directory | Meaning | Ownership |
|---|---|---|
| `session-memory/` | **episodic** — what *I* did on Tuesday | **personal**, one namespace per developer |
| `knowledge-base/` | **semantic** — how the cart service works | **shared**, flat, converging |

```
.ai_vault/                              # its own git repository
  knowledge-base/
    Cart Service.md                     # one note per topic, everyone augments it
    Kafka Topics.md
  session-memory/
    dmitrii/Session_dmitrii_2026-07-10_1002.md
    petr/Session_petr_2026-07-09_1730.md
```

### 2.1 Rejected: `knowledge-base/<developer>/`

Per-developer knowledge folders fragment the graph into N disconnected clusters. Connectedness
is what makes both `build_context` traversal and semantic ranking useful; five islands with no
edges between them degrade retrieval for everyone. Attribution can be obtained far more cheaply
(see §3).

### 2.2 Rejected: `knowledge-base/general/`

A separate "consolidated" folder implies a third copy of every topic and an unanswered question:
who merges into it, and when? In practice nobody does. `knowledge-base/` **is** the general
store. There is no promotion step because there is nothing to promote.

### 2.3 Consequence: session note titles must be globally unique

Basic Memory resolves `[[Title]]` by title/permalink, not by containing folder. Two notes titled
`Session_2026-07-10_1002` in different folders make every `[[link]]` to them ambiguous.

- **Team mode:** `Session_<author>_<YYYY-MM-DD_HHMM>`
- **Single-developer mode:** `Session_<YYYY-MM-DD_HHMM>` (unchanged; existing notes untouched)

---

## 3. Attribution

Two signals — no folder split, and no separate list of authors to maintain.

1. **`author:` frontmatter** on every session note.
2. **A byline link on each fact** in a knowledge-base note, pointing back to the session note that
   produced it. This is already how `/sleep` works; in team mode the target is namespaced, so the
   link itself carries the author.

```markdown
## Redis cache
TTL is 900s. — [[Session_petr_2026-07-09_1730]]

## Item limit
12345 items. — [[Session_vincent_2026-07-10_1100]]
```

"Who has touched this note" is then **derived** from its outgoing relations (the graph already
stores them), not stored redundantly.

### 3.1 Why not a `contributors:` frontmatter list

Basic Memory's tools cannot maintain one incrementally. `edit_note` operates on the note body
(`append`, `prepend`, `find_replace`, `replace_section`, `insert_before/after_section`).
`write_note` can merge frontmatter via `metadata`, but on an existing note it requires
`overwrite=True`, i.e. rewriting the whole file. Adding yourself to a frontmatter list would
therefore mean read-modify-write — exactly the race that §5.5 exists to prevent.

A `## Contributors` body section maintained by `replace_section` would be writable, but it makes
every developer rewrite the same lines, converting a conflict-free append into a guaranteed
git conflict hot spot.

Byline links are append-only. Two developers appending different sections to the same note merge
cleanly; the same two rewriting a shared list do not.

---

## 4. Configuration and installation

```toml
[vault]
project = "myproject"
path    = "/path/to/project/.ai_vault"   # required for read-only git inspection
author  = "dmitrii"                       # absent => single-developer mode
```

- `install.py --author <name>` sets `[vault] author`. Prompted interactively as an optional
  field; an empty answer means single-developer mode.

  The interactive prompt **suggests** a default derived from the developer's git identity —
  `slugify_project(git config user.name)`, so `Dmitry Mitin` is offered as `dmitry-mitin`. It is
  only a suggestion: the developer may type any slug-stable name, and pressing Enter on an empty
  prompt still selects single-developer mode rather than accepting the suggestion silently.
  When `git config user.name` is unset or slugifies to an empty string, no default is offered.
  The suggestion exists so that the common path produces a slug-stable name by construction
  instead of being rejected by the validation below.

  A full team installation therefore looks like:

  ```bash
  git clone git@github.com:acme/myproject.git
  cd myproject
  git clone git@github.com:acme/myproject-memory.git .ai_vault   # the vault, cloned first
  python3 ~/tools/LetTheAISleep/install.py \
      --target ~/code/myproject --project myproject --author dmitrii
  ```

  Re-running `install.py` later with `--author` on an existing single-developer project is
  supported: it adds the field, new session notes become namespaced, and existing flat session
  notes keep their paths and titles.
- `install.py --vault-path <path>` sets `[vault] path`, defaulting to `<project-root>/.ai_vault`.
- **Installation never overwrites an existing `.ai_vault/`.** The expected team flow is: clone the
  code repo, clone the vault repo into `<project-root>/.ai_vault`, then run `install.py --author …`.
  The installer registers the Basic Memory project and writes config; it does not create,
  move, or delete vault content.
- The author name must be **slug-stable**: `slugify_project(author) == author`. `--author "Dmitrii M"`
  is rejected with a message suggesting `dmitrii-m`. Basic Memory slugifies directory names, and
  a name that differs from its slug has already cost this project a day of debugging (see
  `Vault Folder Convention` and `Project Root Resolution` in the knowledge base).

Absent `[vault] author`, every behaviour below degrades to exactly today's single-developer
behaviour. Backward compatibility is a hard requirement, not an aspiration.

---

## 5. Neighbour awareness: `lts digest`

### 5.1 Source of truth

| Source | Gives | Missing |
|---|---|---|
| **`git log --since` in the vault** | *who* (commit author) and *what* | needs git |
| `basic-memory recent_activity` | entities and relations | no authorship; it is a model-side call, not usable from a hook |
| `mtime` scan | no dependencies | no authorship; `git pull`/`checkout` rewrites mtimes and produces false positives |

**Decision:** git is the primary source; the `mtime` scan is the degradation path when the vault
is not a git repository (the normal single-developer case, where authorship is meaningless
anyway). Authorship is the one thing nothing else provides, and it is the stated primary goal.

### 5.2 Behaviour

`lts digest [--since TS] [--json]`, implemented in `lts/digest.py`.

- Baseline defaults to `anchor.updated` — the timestamp `/sleep` already writes.
- "Mine" is excluded by comparing the commit author against `git config user.name`.

  Note the two names are deliberately different things and must not be conflated. `[vault] author`
  is a **slug** that names a directory (`session-memory/dmitrii/`); `git config user.name` is
  whatever the developer's git identity says (`Dmitry Mitin`). The digest reports commit authors
  verbatim, and uses the git identity — not the slug — to decide which commits are the local
  developer's own.
- Ahead/behind comes from `git rev-list --left-right --count HEAD...@{upstream}`.
- Uncommitted files come from `git status --porcelain`.

```
$ lts digest
Since your last sleep (2026-07-10 10:08):
  knowledge-base:  Cart Service (petr), Kafka Topics (petr)
  session-memory:  2 notes by petr
  git:             7 commits behind origin/main, 4 uncommitted files
                   (relative to the last fetched origin/main; no network access)
```

### 5.3 Hooks and skills never mutate git

No `fetch`, no `pull`, no `commit`, no `push`, from any hook or skill. A hook must not touch the
network, and publishing a teammate-visible artifact is the developer's decision. The
ahead/behind count is therefore relative to the last fetched remote ref, and the output says so
rather than implying freshness it does not have.

### 5.4 Wiring

- **`SessionStart`** appends the digest after the anchor, only when non-empty. When un-slept
  material exists, the forced-sleep message takes priority and the digest is suppressed — one
  demand at a time.
- **`/memory-status`** always shows the digest and the git block.
- **`/sleep`** in team mode:
  - writes the session note to `session-memory/<author>/` with `author:` frontmatter and the
    unique title from `lts session-name`;
  - when augmenting a knowledge-base note, **appends** its facts, each carrying a byline link to
    its personal session note (§3);
  - **before writing**, inspects the vault: conflict markers → refuse and tell the user;
    behind the remote → warn, then proceed;
  - **after writing**, reminds the user to commit and push. It does not do so itself.

### 5.5 Writing into a note a teammate already created

This is the central collaboration path. Petr's model wrote `knowledge-base/Cart Service`; Vincent
now works on the same service.

1. `/sleep` step 4 already **searches before writing**. Vincent's model finds `Cart Service` and
   calls `edit_note` with `append`. One note, two authors, no duplication.
2. If the model skips the search and calls `write_note` on an existing title, Basic Memory
   returns an error rather than clobbering the note — but only because
   `write_note_overwrite_default` is `False`. That is **someone else's configuration**, documented
   as switchable back to `True` to "restore pre-v0.20 upsert behavior". `/sleep` must therefore
   pass **`overwrite=False` explicitly** on every `write_note` call and never rely on the default.
   A teammate's note must not be destructible by a config flag we do not own.
3. Concurrent edits are guarded by Basic Memory itself: `edit_note` applies the operation to the
   caller-supplied base content and rejects a stale base rather than silently editing whichever
   copy is newest (`EntityService.prepare_edit_entity_content`). `/sleep` never passes
   `skip_conflict_check`.
4. What remains is an ordinary git conflict when two developers append to the same note between
   pulls. Their humans resolve it in their own repository. `/sleep` refusing to write over
   conflict markers keeps a half-merged file from being consolidated as if it were coherent.

A near-miss worth naming: a model that writes `Cart service` or `Cart Service (API)` instead of
finding `Cart Service` creates a silent duplicate that no error catches. Search-before-write is
the only defence, so `/sleep` searches by both title and meaning, and prefers augmenting an
existing note over creating a near-synonym.

---

## 6. New CLI surface

| Command | Purpose |
|---|---|
| `lts digest [--since TS] [--json]` | What changed in the vault since the baseline, and by whom |
| `lts session-name` | Prints `Session_dmitrii_2026-07-10_1002` (or the single-dev form) |

`lts session-name` exists for the same reason `lts anchor write` and `lts pending list` do: the
model must not compose identifiers or paths from a shell whose working directory it does not
control. See the `Sidecar Path Discipline` knowledge-base note.

`lts status` gains `author` and `vault_path` in both its text and `--json` output.

---

## 7. Failure modes

| Condition | Behaviour |
|---|---|
| `[vault] path` unset in `config.toml` | Defaults to `<project-root>/.ai_vault`; only a missing directory is an error |
| Vault directory does not exist | `digest` reports `available: false` with a reason; hooks stay silent |
| Vault is not a git repository | `mtime` degradation; no git block |
| `git` not on PATH | Same as above |
| No upstream configured | Git block present, `ahead`/`behind` are `null` |
| No anchor yet (first ever session) | No baseline; digest is silent |
| Conflict markers in a vault note | `/sleep` refuses to write and reports the file |
| `write_note` on a title a teammate already owns | Basic Memory errors; `/sleep` passes `overwrite=False` explicitly rather than trusting the config default, then retries as an `edit_note` append |
| `edit_note` against a stale base | Basic Memory rejects the edit; `/sleep` re-reads the note and retries, and never passes `skip_conflict_check` |
| A near-synonym title (`Cart service`, `Cart Service (API)`) | **Not caught by any error.** Search-before-write is the only defence (§5.5) |
| `--author` is not slug-stable | `install.py` exits non-zero and suggests the slug |
| `[vault] author` absent | Every behaviour reverts to single-developer mode |

Two developers sleeping at the same moment can still conflict on the same knowledge-base note.
That is a normal text merge in a repository the humans own; we do not attempt to resolve it.
`/sleep` refusing to write over conflict markers is the guard that keeps a half-merged file from
being silently consolidated. See §5.5 for the full write path.

---

## 8. Testing

- **`tests/test_digest.py`** — a real temporary git repository: two commits by different authors
  produce the correct grouping; own commits are excluded; a vault with no `.git` takes the
  `mtime` path; `git init --bare` plus a push exercises `ahead`/`behind` parsing; no upstream
  yields `null`; a non-existent vault path yields `available: false`.
- **`tests/test_config.py`** — `author` and `path` parsed; both absent yields single-developer mode.
- **`tests/test_install.py`** — `--author` writes config; a pre-existing `.ai_vault/` is left byte
  for byte untouched; a non-slug author is rejected with the slug suggested; the interactive
  default is derived from `git config user.name` but an empty answer still yields
  single-developer mode; an unset git identity offers no default.
- **`tests/test_hooks.py`** — `SessionStart` appends the digest on the clean path and suppresses
  it when a forced sleep is pending.
- **`tests/test_cli.py`** — `lts digest --json` shape; `lts session-name` in both modes.

The `/sleep` write path in §5.5 is prompt behaviour, not code, so it is verified by a manual
end-to-end rehearsal rather than a unit test: two vault clones, two authors, one shared note;
assert that the second `/sleep` appends rather than errors or duplicates, and that a deliberate
`write_note` on an existing title fails with `overwrite=False` passed explicitly.

---

## 9. Explicitly out of scope

- Automatic git operations of any kind.
- Conflict resolution or three-way merging of notes.
- Per-developer trust levels or draft/vetted states in `knowledge-base/`.
- Cloud or server-side vault sharing (Basic Memory Cloud).
- Rewriting existing single-developer vaults into namespaced form. Adding `[vault] author` to a
  populated vault starts namespacing new session notes; old flat ones keep working and keep
  their titles.

### 9.1 Deferred: notes reviewed like code

Notes should be reviewed, proofread and kept current the way code is. Nothing here blocks that,
and two choices actively enable it: the vault is **its own git repository**, and humans — not the
system — commit and push. Pull-request review of memory therefore works today, with no code from
us.

What a later spec would add: a review skill that reads a vault diff and looks for contradictions
with existing notes, near-duplicate titles, unlinked facts and claims that can no longer be
verified; and some notion of a note going stale.

It is deferred rather than folded in because staleness and draft/vetted states reintroduce the
**trust axis**, which was deliberately ranked below attribution when this design started. Team
mode should be in real use before that axis is designed.

One property of this design exists to serve that future spec: knowledge-base writes are
**append-only, one fact per block, each with a byline link** (§3). That makes a memory diff
readable — a reviewer sees what a session added and who added it, not a rewritten file.

---

## 10. Operational note discovered while designing this

Basic Memory's MCP server background-syncs every active project on startup and then runs a file
watcher (`services/initialization.py::initialize_file_sync`), and that sync path batch-generates
vector embeddings (`sync_service.sync(..., sync_embeddings=True)`). Therefore notes arriving via
`git pull` are indexed automatically — full text, graph and vectors — by the running watcher, or
at the next Claude Code start. **No new synchronization tooling is required.**

Note the asymmetry: `write_note` over MCP indexes full text but *not* vectors, which is why
`/sleep` still runs `basic-memory reindex --embeddings`. Notes pulled from a teammate become
semantically searchable before notes the local model just wrote itself.