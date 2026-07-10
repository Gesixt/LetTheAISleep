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
```
This prints: STM buffer size (entries / bytes / ~tokens), sleep debt (pending un-consolidated snapshots), context pressure (if a transcript is available), and anchor freshness. Pass `--json` if you want to post-process the numbers.

## 2. LTM metrics (Basic Memory)
Take the project name from the `lts status` output above. Do not go hunting for `config.toml`.

**Use the slug for this command.** `basic-memory project info` resolves projects only by their
**slug**, not the display name. `lts status` prints it as `Basic Memory CLI name: <slug>` (or the
`project_slug` field with `--json`). Passing the display name (e.g. `LetTheAISleep`) fails with a
misleading `set to cloud mode but no credentials` error, which really means "no such project". Run:
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
- **Sleep debt > 0** or **context pressure is `force`** → recommend running `/sleep` now.
- **Embedding Status is not "Up to date"** → recommend `basic-memory reindex --embeddings -p <project>` so `/recall` semantic search is complete.
- **Isolated (orphans) is high** → suggest linking those notes during the next `/sleep`.

If `basic-memory` is not installed or the project is missing, still report the STM/sidecar half and say the LTM half is unavailable.
