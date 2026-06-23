---
name: sleep
description: Consolidate the current session into long-term memory (Basic Memory) without losing any detail, then clear the STM buffer. Use when context is filling up, at the end of a logical chapter, or when prompted by sleep pressure.
---

# /sleep — Consolidate memory (the AI's sleep)

Goal: move everything worth keeping from this session into long-term notes with explicit `[[links]]`, preserving every number, name, and rationale — then free the STM buffer. This is **not** a lossy summary.

**Target the right vault.** Read `[vault] project` from this project's `config.toml` and pass it as the `project` parameter to **every** Basic Memory tool call (`write_note`, `edit_note`, `read_note`, `search`, `build_context`). Without it the tools default to Basic Memory's `main` vault, not this project's.

Run these steps in order.

## 1. Collect
- Read the curated STM buffer:
  `lts stm read --session "$CLAUDE_SESSION_ID"`
- If a previous session left raw material, also account for it:
  `lts pending has` → if `yes`, read the snapshots under `.ai_memory/pending_consolidation/` with your file tools.
- Add the significant remaining conversation context (exclude noise).

## 2. Extract (with preservation, never compress)
Pull out: key decisions, **ALL numbers**, names, architectural choices, contentious points, open questions. Rule: every number, name, and rationale is carried over **verbatim**. When in doubt, carry it over.

## 3. Write the session note
Use Basic Memory `write_note` (or `edit_note` if today's session note exists) in the `_Session_Memory/` folder, named `Session_<YYYY-MM-DD_HHMM>`. Frontmatter: `type: session`, `date`, `topics`, `kb_refs`, `status: consolidated`. Body is coherent prose under: `## Context`, `## Key decisions`, `## Open questions`, `## Links`.

## 4. Link into the knowledge base
For each distinct topic: `search` the vault. If a `_Knowledge_Base/` note exists, `edit_note` to augment it; otherwise `write_note` a new one. Place `[[links]]` both ways — in the note body **and** in the session note's frontmatter `kb_refs`. Build coherent linked prose, not a bullet list.

## 4b. Rebuild embeddings so the new notes are semantically searchable
`write_note`/`edit_note` do NOT update the vector index automatically — only full-text and graph links are live immediately. So that `/recall` can find what you just wrote by meaning, rebuild embeddings with bash:
```
basic-memory reindex --embeddings -p <project>
```
Use the project name from `config.toml` `[vault] project` (omit `-p` to use the default project). This is incremental and fast.

## 5. Self-check (anti-loss)
Re-scan the dialogue and STM for key entities (numbers, proper nouns). For each, confirm it appears in a written note. Append anything missing via `edit_note`. Only proceed once nothing is missing.

## 6. Free STM, update the anchor, then clear context
- Update the anchor file `.ai_memory/anchor.json` with the latest `last_session`, `active_topics`, `active_notes` (the notes you just wrote/touched).
- Clear the STM buffer **only after** the writes above succeeded:
  `lts stm clear --session "$CLAUDE_SESSION_ID"`
- If you consumed pending snapshots, delete them after a successful write.
- Then run `/compact` to clear context.

If interrupted before step 6, leave the session note `status: pending` and do **not** clear STM — the next session's SessionStart hook will resume this.
