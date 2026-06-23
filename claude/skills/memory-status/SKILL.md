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
Take the project name from the `lts status` output above — the `Project:` line (or the `project`
field with `--json`). That value comes straight from this project's `config.toml`, so do not go
hunting for the file yourself. Then run:
```
basic-memory project info <project>
```
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
