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

## 0. Check the vault before writing (team mode)
Run:
```
lts digest
```
- If it reports the vault is **behind** the remote, say so and recommend a `git pull` in the vault
  before sleeping. Then proceed — a stale base is a warning, not a stop.
- If any vault note contains git conflict markers (`<<<<<<<`), **stop**. Report the file and let the
  user resolve it. Consolidating a half-merged note produces confident nonsense.

Never run `git fetch`, `git pull`, `git commit` or `git push` yourself. Publishing memory is the user's decision.

## 1. Collect
- Read the curated STM buffer:
  `lts stm read`
- If a previous session left raw material, also account for it:
  `lts pending list` → prints the absolute path of each snapshot (nothing if there are none).
  Read those paths with your file tools.

  A snapshot holds **only what was said after the last sleep** — the sleep mark
  (`.ai_memory/sleep-mark.json`) keeps everything already consolidated out of it. So treat a
  snapshot as new material and consolidate it. Do not open the session's notes to check whether
  it duplicates them; it does not, and that check used to burn a whole turn every session.
- Add the significant remaining conversation context (exclude noise).

## 2. Extract (with preservation, never compress)
Pull out: key decisions, **ALL numbers**, names, architectural choices, contentious points, open questions. Rule: every number, name, and rationale is carried over **verbatim**. When in doubt, carry it over.

## 3. Write the session note
Ask the CLI for the name and directory — never compose them yourself:
```
lts session-name --json
```
It prints `{"title": "Session_dmitrii_2026-07-10_1002", "directory": "session-memory/dmitrii"}` in
team mode, and the flat single-developer form otherwise.

Use Basic Memory `write_note` with that exact `title` and `directory`, and **always pass
`overwrite=False` explicitly** — never rely on the server's default, which a `write_note_overwrite_default`
setting we do not own can flip to destructive upsert. Frontmatter: `type: session`, `date`, `topics`,
`kb_refs`, `status: consolidated`, and in team mode `author: <the author from lts status>`. Body is
coherent prose under: `## Context`, `## Key decisions`, `## Open questions`, `## Links`.

## 4. Link into the knowledge base
`knowledge-base/` is **shared with your teammates**. It is flat: never create a per-person folder there.

For each distinct topic, `search` the vault first — by title *and* by meaning. A near-synonym
(`Cart service`, `Cart Service (API)`) creates a silent duplicate that no error will catch, so prefer
augmenting an existing note over creating a new one.

- If a note exists: `edit_note` with `append`. Appending is what lets two developers touch one note
  without a git conflict. Never `replace_section` on a section you did not write, and never pass
  `skip_conflict_check` — Basic Memory rejects edits made against a stale base on purpose.
- If no note exists: `write_note` with `directory: "knowledge-base"` and `overwrite=False`.

Give every fact a **byline link** to the session note it came from, so authorship is readable from
the note itself:

    ## Redis cache
    TTL is 900s. — [[Session_petr_2026-07-09_1730]]

Place `[[links]]` both ways — in the note body **and** in the session note's frontmatter `kb_refs`.
Build coherent linked prose, not a bullet list.

## 4b. Rebuild embeddings so the new notes are semantically searchable
`write_note`/`edit_note` do NOT update the vector index automatically — only full-text and graph links are live immediately. So that `/recall` can find what you just wrote by meaning, rebuild embeddings with bash:
```
basic-memory reindex --embeddings -p <project>
```
Use the project name from `config.toml` `[vault] project` (omit `-p` to use the default project). This is incremental and fast.

## 5. Self-check (anti-loss)
Re-scan the dialogue and STM for key entities (numbers, proper nouns). For each, confirm it appears in a written note. Append anything missing via `edit_note`. Only proceed once nothing is missing.

## 6. Free STM, update the anchor, then clear context
- Update the anchor with the notes you just wrote/touched (repeat `--topic` / `--note` per item).
  Use the **exact `title` and `directory` from Step 3** — do not retype the flat `Session_<date>` form,
  or in team mode the anchor will point at a note that does not exist (the author segment is missing)
  and the anchor → session-note link breaks:
  ```
  lts anchor write --last-session "<title from Step 3>" \
    --topic "..." --topic "..." \
    --note "<directory from Step 3>/<title from Step 3>" --note "knowledge-base/..."
  ```
- Clear the STM buffer **only after** the writes above succeeded:
  `lts stm clear`

  This also places the **sleep mark** — the point in the transcript that consolidation reached.
  Everything behind it is now off the hooks' books: the Stop hook drops this turn instead of
  refilling the buffer you just emptied with the story of the sleep itself, and the `/compact`
  below snapshots nothing. Never edit `sleep-mark.json` by hand.
- If you consumed pending snapshots, drop them: `lts pending clear`
- In team mode, remind the user to commit and push the vault repository so teammates see this work.
  Do not do it for them.
- Then run `/compact` to clear context.

If interrupted before step 6, leave the session note `status: pending` and do **not** clear STM — the next session's SessionStart hook will resume this.
