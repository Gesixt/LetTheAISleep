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

`lts doctor` runs eleven health checks and **exits non-zero when any of them fails**. Levels are `✓ ok`,
`! warn`, `✗ fail` and `· skip`; a `skip` is a check that could not run and says why — never read it
as a pass. A `? ` line is a level `lts` itself cannot interpret: it counts as a failure and is
reported like one, because a verdict nobody can read is not a clean bill. Report every `fail` prominently with its `Fix:` line, and do not describe memory as
healthy while one is outstanding. `capture` and `pressure` show as `skip` unless you pass
`--transcript <path>`, and `capture_live` cannot reach `fail` without one. Of the three only
`capture` parses the whole transcript, which `SessionStart` deliberately does not spend on every
`/compact`; the other two read only a bounded window at its end — the tail read `pressure` uses
measured 0.0002–0.0003 s per call on five real transcripts from 7.4 MB to 208 MB, and `capture_live`
as a whole measured 0.263 ms, both on 2026-10-01. So passing `--transcript` costs the `capture` parse
and effectively nothing for the other two. Pass it when you are investigating; the trend checks cover
the same failure classes between sessions, and only `capture_live` covers them mid-session.

`capture_live` is the eleventh check, and the only one that notices *mid-session* that the `Stop`
hook has stopped capturing — the trend checks read a journal that gains a record only at
`SessionStart`, so between two compactions they answer `ok` about the same records. It answers from
`turn-state.json`, which the `UserPromptSubmit` hook writes on every prompt, so from this skill you
are reading the state **as of the last prompt**, which the message dates — it is not a statement
about this instant, and from a `doctor` run by hand that prompt can be hours ago. A `fail` means the
`Stop` hook stopped capturing while a session was running, so **treat the STM buffer as incomplete
from that timestamp onward**: say so plainly, do not call the session captured, and do not treat
`lts status`'s buffer figures as the record of what was discussed since. The gap is recoverable — the
next working `Stop` captures everything after the mark, not just the current turn — but until it
runs, `/sleep` would consolidate a buffer that is missing exchanges. Without `--transcript` this
check reports `ok`, or a `skip` naming how many consecutive prompts found the capture mark unmoved;
that `skip` is a reason to re-run with `--transcript <path>`, never a pass.

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
- **`lts doctor` exits non-zero** → one or more of the eleven checks failed. Report each failing check id with its `Fix:` line, and do not call memory healthy while any of them stands. If the failure is `config` or `sidecars`, stop before writing anything new: those two mean writes would land in the wrong place. If it is `capture_live`, name the timestamp in its message and say that the STM buffer is incomplete from there onward.
- **`lts update --check` reports anything stale** → this project is running older copies than the clone ships. Recommend `git -C <clone> pull` (if the clone is behind) followed by `lts update`. Never run git yourself.
- **Sleep debt > 0** or **context pressure is `force`** → recommend running `/sleep` now.
- **Embedding Status is not "Up to date"** → recommend `basic-memory reindex --embeddings -p <project>` so `/recall` semantic search is complete.
- **Isolated (orphans) is high** → suggest linking those notes during the next `/sleep`.

If `basic-memory` is not installed or the project is missing, still report the STM/sidecar half and say the LTM half is unavailable.
