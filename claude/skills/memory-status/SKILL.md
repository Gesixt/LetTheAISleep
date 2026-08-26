---
name: memory-status
description: Show a dashboard of memory load — STM buffer, sleep debt, context pressure (sidecar), and LTM note/link counts and embedding freshness (Basic Memory). Use when the user asks how full memory is, whether to sleep, or for memory health.
---

# /memory-status — Memory dashboard

Goal: report the state of both memory tiers in one place, and flag anything that needs action (sleep now, reindex embeddings, orphaned notes).

## 1. STM / sidecar metrics (our layer)
Run:
```
lts status
lts doctor
lts digest
lts update --check
```
`lts status` prints: STM buffer size (entries / bytes / ~tokens), sleep debt (pending un-consolidated snapshots), context pressure (if a transcript is available), and anchor freshness. Pass `--json` if you want to post-process the numbers.

`lts doctor` prints which project root resolved and whether any **stray sidecars** exist — `.ai_memory/` directories in subdirectories, holding memory that `/sleep` will never read. It exits non-zero when something is wrong. If it reports a stray sidecar or an unconfigured root, say so prominently: the user's `config.toml` was probably moved or deleted, and memory is being split in two.

`lts digest` prints what changed in the vault since your last sleep and who changed it, plus the
vault's git state (commits behind/ahead of the last fetched remote ref, uncommitted files). It is
silent when there is nothing to report. In team mode, surface it prominently: it is how the user
learns a teammate has already solved the thing they are about to investigate.

`lts update --check` writes nothing and reports whether this project's **copies** — the skills,
the hook wiring in `.claude/settings.json`, the `CLAUDE.md` memory block — still match the clone
they came from. It also names the clone in use and how far behind its remote it is (as of the
last fetch). Staleness here is silent otherwise: a project can run a months-old `/sleep` while
the clone has moved on. It exits non-zero when anything is stale.

## 2. LTM metrics (Basic Memory)
Take the project name from the `lts status` output above. Do not go hunting for `config.toml`.

**Use the slug for this command.** `basic-memory project info` resolves projects only by their
**slug**, not the display name. `lts status` prints both forms next to the subcommand each one
belongs to (`Basic Memory: reindex -p <name>  ·  project info <slug>`), or the `project_slug`
field with `--json`. Passing the display name (e.g. `LetTheAISleep`) fails with a misleading
`set to cloud mode but no credentials` error, which really means "no such project". Run:
```
basic-memory project info <project_slug>
```

The CLI is inconsistent: `basic-memory reindex -p` takes the **display name** instead (that is why
`/sleep` passes the plain `project`). MCP tool calls such as `write_note` also take the display name.
From that dashboard report:
- **Entities** — number of notes
- **Relations** — number of `[[links]]`
- **Isolated** — orphan notes (no relations)
- **Chunks** + **Status** — embedding coverage; `Status: Up to date` means semantic search is current, otherwise embeddings are stale
- **Semantic Search** — Enabled/Disabled and the model

## 3. Present a combined dashboard and flag actions
Summarize both tiers together. Raise a clear flag when:
- **The vault is behind the remote, or has uncommitted notes** → recommend a `git pull` / commit in the vault repository. Never run git yourself.
- **`lts doctor` exits non-zero** → memory is misplaced. Report the stray path and stop before writing anything new.
- **`lts update --check` reports anything stale** → this project is running older copies than the clone ships. Recommend `git -C <clone> pull` (if the clone is behind) followed by `lts update`. Never run git yourself.
- **Sleep debt > 0** or **context pressure is `force`** → recommend running `/sleep` now.
- **Embedding Status is not "Up to date"** → recommend `basic-memory reindex --embeddings -p <project>` so `/recall` semantic search is complete.
- **Isolated (orphans) is high** → suggest linking those notes during the next `/sleep`.

If `basic-memory` is not installed or the project is missing, still report the STM/sidecar half and say the LTM half is unavailable.
