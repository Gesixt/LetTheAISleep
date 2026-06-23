---
name: recall
description: Retrieve relevant long-term memory for the current topic, prioritizing notes linked from the session anchor over purely semantic matches. Use when you need to remember prior decisions or context.
---

# /recall — Retrieve memory (graph-priority hybrid)

Goal: load only what is relevant, cheaply. Prefer notes directly linked from the anchor/last session over purely semantic hits; traverse links one level only (no recursion); pull full bodies on demand.

## Steps
1. **Anchor first.** Read the anchor entry points:
   `lts anchor render`
   These `active_notes` are the highest-priority candidates.
2. **Semantic candidates.** Run Basic Memory `search` with the user's topic to get semantically similar notes (snippets).
3. **Graph expansion (1 level).** For the anchor notes and the last session note, use `build_context` to pull directly linked neighbors — one level only.
4. **Rank with graph priority.** Order results so notes reachable via `[[links]]` from the anchor/last session rank above purely semantic hits of similar score. De-duplicate.
5. **Lazy bodies.** Present snippets. Only `read_note` the full body for the few notes you actually need to answer — do not load everything (keep memory tokens well under half the window).

If `search` returns nothing (e.g., semantic index degraded), fall back to the graph path (anchor + `build_context`) alone.
