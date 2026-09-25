# Design: The memory map — making retrieval reliable

**Date:** 2026-09-25
**Status:** approved for implementation
**Builds on:** `2026-06-23-hybrid-memory-design.md` (v1.1), §2.Блок Д of `task.md`

---

## 0. Problem

The user reports that the model "very rarely and reluctantly" uses `/recall`, and consequently
contradicts or forgets what was already decided. The two places it shows up, in the user's own
account: **after a `/compact` inside a session**, and **when the topic changes inside a
session**. The decisive context: this user has worked in **one continuous session for over two
months**, using `/sleep` and `/compact`, and has never started a new one.

An instance of the failure is already on record: on 2026-08-26 the model claimed a hook fix was
"live immediately everywhere", while the answer ("which clone backs the global `lts`") was
sitting in the vault's `Installation and Environment` note from the previous session.

---

## 1. Root cause — the model has nothing to aim at

Not reluctance. Two findings, both verified against this project.

**1.1 The anchor is systematically suppressed.** `session_start.py` returns the sleep demand
*instead of* the anchor:

```python
if snapshots or stm_entries:
    # One demand at a time: finishing the sleep comes before reading anyone else's notes.
    return _FORCE_MSG.format(...)      # early return — the anchor is never appended
```

`SessionStart` does fire after every `/compact`, so the hook runs often. But in a continuous
session the STM buffer is non-empty almost always — it fills every turn — so the branch taken is
always the early one. The anchor therefore reaches the model only in the one narrow window
immediately after a sleep. Observed live in the session that produced this spec, with 29 STM
entries: the injected context was the sleep demand alone. No note titles, no active topics, no
signal that the vault holds 17 notes.

The comment `One demand at a time` was written for short sessions, where STM is often empty. That
assumption is false for the workflow the user actually has.

**1.2 Nothing else carries memory entry points.** `UserPromptSubmit` injects sleep pressure only.
So between two compactions there is no mechanism at all that reminds the model the vault exists.
`/recall` being a skill — the "soft", model-decided half of the reliability principle in the
`Architecture` note — is a secondary cause. The primary one is that there is nothing to aim it at.

**1.3 A map is cheap.** A titles-only index of this whole vault is **387 bytes** for 17 notes,
about 100 tokens. The North Star's "anchor is a table of contents, not the contents" can be
served on every single turn.

---

## 2. Decision: split the two payloads by frequency

The full anchor (last session, active topics, active notes, `next-task`) is ~600 characters.
`additionalContext` persists in the window, so injecting it on every turn accumulates —
~250 tokens × 100 turns ≈ 25k. So the two payloads go to two different hooks:

| Hook | Fires | Payload |
|---|---|---|
| `SessionStart` | every `/compact`, resume, startup | sleep demand (if owed) **and** the full anchor **and** the digest |
| `UserPromptSubmit` | every turn | the **memory map** — titles only, ~120 tokens — then the pressure line |

The `SessionStart` change is the removal of the early return: the sleep demand becomes one block
among several rather than a replacement for them. The "memory is not configured" branch stays an
early return, because in that case there is nothing to describe.

---

## 3. The memory map

```
## Memory map (19 notes) — read before re-deriving
active: session-memory/Session_2026-09-25_1534 · knowledge-base/Memory Map ·
  knowledge-base/Sleep Watermark · knowledge-base/Context Pressure Measurement · …
knowledge-base: Team Mode · Installation and Environment · Project Conventions · …
session-memory: Session_2026-08-26_1356 · Session_2026-07-11_0028 · …
If the question touches any of these, read the note instead of re-deriving it.
```

**Source: the vault filesystem.** A hook cannot call MCP tools, and shelling out to the
`basic-memory` CLI on every turn would add a dependency and latency to every prompt. Listing
`*.md` is instant. The title is the filename stem, which is what Basic Memory uses as the title.

**Grouping.** By top-level vault directory: `knowledge-base` first (permanent topical notes carry
the most meaning per title), other directories alphabetically, `session-memory` last. Team mode's
`session-memory/<author>/` collapses into the one `session-memory` group.

**Ranking (revised 2026-09-25, after measuring ppss).** Grouping alone is not an order. On a
247-note vault the budget bought 23 titles and spent all of them on the alphabetical head of
`knowledge-base` — the notes the last sleep had just marked as the live ones were not among them,
and `session-memory` got nothing at all. So the map ranks in three tiers:

1. **The anchor's `active_notes`, hoisted above the groups**, on their own `active:` line and in
   anchor order. This is the one ranking signal that the model did not produce this turn: the
   sleep chose those notes itself. They must clear the group tiers, because the entry points cross
   them — the last session note lives in `session-memory`, the first group truncation drops. They
   are printed as full addresses (`knowledge-base/Memory Map`), which is what `read_note` takes,
   and removed from their own group so the budget never pays for a title twice. An anchor entry
   whose note no longer exists is dropped: advertising a deleted note costs a wasted `read_note`
   and teaches the model that the map lies.
2. **Recency, inside each topical group**: most recently modified first, by filesystem mtime.
3. **The alphabet**, breaking recency ties — which, straight after a `git clone` that flattens
   every mtime to the checkout time, is the entire ranking. Degrading to the previous behaviour is
   the right failure here.

`session-memory` is ranked by title instead, descending. A session title *is* its date, exactly,
and it survives that same `git clone`; mtime could only corrupt it — fixing a typo in an old
session note must not make it look like the latest chapter.

**No exclusions.** Basic Memory indexes every `.md` and does not honour `.bmignore` (established
by experiment, recorded in `Basic Memory Behaviour`). The map therefore lists exactly what is
actually findable; inventing an exclusion list here would make the map lie.

**Budget with degradation.** 17 notes is 387 bytes, but 500 titles would be ~10k tokens per turn.
One setting, `[memory] map_budget` (characters, default 1500, `0` disables the map entirely),
truncates in the ranking order above; what does not fit collapses into `… +37 more (use /recall)`.
A single knob does both jobs, so there is no separate on/off flag.

**Silence, not noise.** `render` returns `""` — and the hook injects nothing — when the vault
directory is absent, the vault is empty, `map_budget` is `0`, or the project is not configured.
The map must never be a reason for a hook to fail or to chatter.

---

## 4. Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `lts/memorymap.py` | `render(cfg) -> str`: the map, grouped, ranked, within budget | `paths.vault_root`, `anchor` |
| `lts/config.py` | the `map_budget` field, default 1500 | — |
| `claude/hooks/session_start.py` | accumulate blocks instead of returning the sleep demand alone | `anchor`, `digest`, `pending`, `stm` |
| `claude/hooks/user_prompt_submit.py` | inject the map, then the pressure line | `memorymap`, `transcript` |
| `lts/cli.py` | `lts memory-map` — the same output, inspectable without a hook | `memorymap` |

No migration is needed: `sync` never touches `config.toml`, so existing projects pick up the
default the moment the code lands.

The `/recall` skill gains a step 0: when the map in context already names an obviously relevant
note, `read_note` it directly instead of running the full anchor + search + graph sequence. The
map makes the cheap path possible, and the skill should say so.

---

## 5. What this guarantees, and what it does not

This makes the **information** guaranteed, not the **behaviour**. STM capture is a hard guarantee:
the hook writes to the buffer and the model does not participate. Here the hook guarantees the
entry points are in front of the model; reading a note remains the model's decision. Today there
is nothing to aim at, so this is a large step — but it is not the same class of reliability, and
it should not be described as if it were.

If a week of use shows it is not enough, injecting real retrieval is a **strict extension**: the
hook already runs on every turn, so it is a matter of adding a search and splicing in snippets.
Nothing here has to be undone.

---

## 6. Rejected alternatives

**A — fix the anchor only.** Removing the early return costs nothing and cures the post-compact
amnesia, but leaves the topic-change failure, which is half of what the user reported. Kept as a
component of the chosen design rather than as the whole of it.

**C — full retrieval in the hook on every turn.** Closest to the RAG half of §2.Блок Д, and the
most automatic. Rejected for now on three grounds: it spawns a search on every prompt (FastEmbed
loads a model — latency on the critical path), it injects content the model may not need, and
ranking noise can actively mislead. Decisively, it moves **curation** into the hook, and the
`Architecture` note places curation on the model's side of the soft/hard line on purpose.

---

## 7. Testing

TDD throughout; every test watched failing first.

`tests/test_memorymap.py` — group order (`knowledge-base` first, `session-memory` last), newest
session first, budget truncation carrying a `+N more` tail, and the four silent cases (no vault,
empty vault, `map_budget = 0`, unconfigured project).

For the ranking: the anchor's notes lead the map and are not listed twice; a stale anchor entry is
dropped; no anchor means no `active:` line; a 247-note vault with room for ~25 titles still shows
the anchored note (the ppss case, stated as the test it is); recency beats the alphabet in a
topical group; and a session note's mtime does **not** outrank its title. Budget truncation is
pinned with every mtime set equal, which is both the `git clone` case and the only way to assert
the alphabet deterministically.

`tests/test_hooks.py` — that `SessionStart` emits the anchor **and** the sleep demand together.
This is the regression test for the root cause in §1.1, where the two are mutually exclusive.
Also that `UserPromptSubmit` emits the map followed by the pressure line, and neither outside a
project.

`tests/test_cli.py` — `lts memory-map`.

`tests/test_config.py` — the `map_budget` default and its override.

---

## 8. Rollout

Hook wiring does not change — the same four events, the same paths — so the deployed clone only
needs `git -C ~/tools/LetTheAISleep pull`. `lts update` is still required per project, because the
`CLAUDE.md` memory block and the `/recall` skill are copies; `lts update --check` reports it.

Verified afterwards the way the sleep-mark fix was: by running the deployed hooks against a
synthetic event and reading what they actually inject.
