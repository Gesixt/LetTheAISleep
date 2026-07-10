---
name: sleep
description: Consolidate the current session into long-term memory (Basic Memory) without losing any detail, then clear the STM buffer. Use when context is filling up, at the end of a logical chapter, or when prompted by sleep pressure.
---

# /sleep — Consolidate memory (the AI's sleep)

Goal: move everything worth keeping from this session into long-term notes with explicit `[[links]]`, preserving every number, name, and rationale — then free the STM buffer. This is **not** a lossy summary.

**Target the right vault.** Read `[vault] project` from this project's `config.toml` and pass it as the `project` parameter to **every** Basic Memory tool call (`write_note`, `edit_note`, `read_note`, `search`, `build_context`). Without it the tools default to Basic Memory's `main` vault, not this project's.

**Never type a sidecar path yourself.** Do not read or write `.ai_memory/...` directly — a
relative path resolves against whatever directory your shell happens to be in, which creates an
orphaned `.ai_memory/` in a subdirectory and silently splits memory in two. Every sidecar read and
write below goes through `lts`, which resolves the project root from `config.toml`. If any `lts`
command reports `no lts project at or above ...`, stop and tell the user — do not create anything.

Run these steps in order.

## 1. Collect
- Read the curated STM buffer:
  `lts stm read`
- If a previous session left raw material, also account for it:
  `lts pending list` → prints the absolute path of each snapshot (nothing if there are none).
  Read those paths with your file tools.
- Add the significant remaining conversation context (exclude noise).

## 2. Extract (with preservation, never compress)
Pull out: key decisions, **ALL numbers**, names, architectural choices, contentious points, open questions. Rule: every number, name, and rationale is carried over **verbatim**. When in doubt, carry it over.

## 3. Write the session note
Use Basic Memory `write_note` (or `edit_note` if today's session note exists) with `directory: "session-memory"`, named `Session_<YYYY-MM-DD_HHMM>`. (Use exactly `session-memory` — that is Basic Memory's slug; do not invent variants like `_Session_Memory`.) Frontmatter: `type: session`, `date`, `topics`, `kb_refs`, `status: consolidated`. Body is coherent prose under: `## Context`, `## Key decisions`, `## Open questions`, `## Links`.

## 4. Link into the knowledge base
For each distinct topic: `search` the vault. If a knowledge-base note exists, `edit_note` to augment it; otherwise `write_note` a new one with `directory: "knowledge-base"` (use exactly `knowledge-base`). Place `[[links]]` both ways — in the note body **and** in the session note's frontmatter `kb_refs`. Build coherent linked prose, not a bullet list.

## 4b. Rebuild embeddings so the new notes are semantically searchable
`write_note`/`edit_note` do NOT update the vector index automatically — only full-text and graph links are live immediately. So that `/recall` can find what you just wrote by meaning, rebuild embeddings with bash:
```
basic-memory reindex --embeddings -p <project>
```
Use the project name from `config.toml` `[vault] project` (omit `-p` to use the default project). This is incremental and fast.

## 5. Self-check (anti-loss)
Re-scan the dialogue and STM for key entities (numbers, proper nouns). For each, confirm it appears in a written note. Append anything missing via `edit_note`. Only proceed once nothing is missing.

## 6. Free STM, update the anchor, then clear context
- Update the anchor with the notes you just wrote/touched (repeat `--topic` / `--note` per item):
  ```
  lts anchor write --last-session "Session_<YYYY-MM-DD_HHMM>" \
    --topic "..." --topic "..." \
    --note "session-memory/Session_<YYYY-MM-DD_HHMM>" --note "knowledge-base/..."
  ```
- Clear the STM buffer **only after** the writes above succeeded:
  `lts stm clear`
- If you consumed pending snapshots, drop them: `lts pending clear`
- Then run `/compact` to clear context.

If interrupted before step 6, leave the session note `status: pending` and do **not** clear STM — the next session's SessionStart hook will resume this.
