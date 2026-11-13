# Codex memory integration

The same project can use Claude Code and Codex with one Basic Memory vault, STM
buffer, anchor and pending-snapshot directory. The implementation was tested against
local codex-cli 0.169.0 rollouts on 2026-11-13.

## Install and activate

First follow [the shared installation steps](../README.md#install) to install Basic Memory
and register your project vault. Then select Codex when attaching memory, from the source clone:

```sh
python3 install.py --target /path/to/project --client claude_code,codex
```

Use `--client codex` for a Codex-only project. Omitting the option preserves the
existing selection; old projects default to Claude Code. The selection is stored in
`[clients] enabled` in the project's LTS `config.toml`.

The installer writes `.codex/hooks.json`, `.agents/skills/{sleep,recall,memory-status}`
and a marked block in `AGENTS.md`, preserving text outside that block. Other Codex
hook handlers are retained, including handlers for the same events. It does not
register MCP servers or grant hook trust. If Basic Memory is not connected yet:

```sh
codex mcp add basic-memory -- basic-memory mcp
```

Restart/resume Codex in the target directory, trust the project if prompted, then
review the four LTS hooks with `/hooks`. Changed hooks need review again. Use
`$recall`, `$sleep`, and `$memory-status`. Sleep has implicit invocation disabled;
the model should recommend it and execute it when explicitly requested.

Generated Codex skills invoke `run_lts.py` by absolute path, so they use the same
source clone as the hooks even if an older `lts` executable remains on PATH.
`lts update` refreshes all clients selected in the config; `--check` is read-only.

## Verify activation

After a real user/assistant turn, run from the source clone:

```sh
python3 run_lts.py doctor --root /path/to/project --client codex --transcript /path/to/rollout.jsonl
python3 run_lts.py status --root /path/to/project --client codex --transcript /path/to/rollout.jsonl
python3 run_lts.py stm read --root /path/to/project
```

Confirm that the latest conversation was captured. `hooks: ok` verifies files and
client selection only. `hook_trust: skip` explicitly means this version does not
verify Codex's persisted trust state; inspect `/hooks` and actual capture. An all-green
filesystem check is not proof that Codex executed a hook.

## Transcript and lifecycle contracts

Only `response_item` records of type `message`, role `user` or `assistant`, are
captured. Input/output text blocks are kept, including assistant commentary and final
answers. Developer/system instructions, environment/AGENTS injections, reasoning,
tool calls, tool outputs and event duplicates are excluded. IDs come from message
IDs, with a stable file-position/hash fallback. Timestamps normalize to UTC,
milliseconds and `Z`; undated/invalid timestamps stay absent rather than guessed.

Context uses `event_msg/token_count.info.last_token_usage.input_tokens`; cached input
is already included. The denominator is the recorded `model_context_window`, not the
Claude-specific LTS config value. Without a window the level is unknown; without usage
the file-size fallback is explicitly an estimate.

Hook bodies live in `lts/hookimpl`; old `claude/hooks` paths remain valid. Codex
entry points default to Codex and generated wiring explicitly passes `--client codex`.
Nullable transcript paths are tolerated. Four events are wired: SessionStart,
UserPromptSubmit, Stop and PreCompact.

## Minimum isolation required by the port

Sleep flags belong to clients. Claude retains its legacy flag path; Codex gets a flag
under its own client state directory. `stm clear --client codex` arms only Codex's flag.
A client-local `consolidation-mark.json` is written alongside the project sleep mark.
Codex and projects configured for both clients use the local frontier for PreCompact
and mark comparisons: another client's sleep cannot claim their unflushed messages.
The shared sleep mark still records project-level evidence of consolidation.

On upgrade, a missing client-local frontier can cause a conservative re-snapshot of
older dialogue. This is preferable to skipping material that no note contains.
Concurrent sessions of the same client still share state; concurrent buffer clear/
append operations and overlapping sleeps remain outside this change. Coordinate
sleep while another client is actively writing.

## Evidence and remaining activation check

Tests cover sanitized records from a real rollout, timestamp normalization, usage,
isolated flags/frontiers, nullable payload fields, entry-point subprocesses, installer
idempotence, preservation of other Codex hooks, CLI selection and trust reporting.
The adapter was also read-tested against a real local rollout without modifying it.

Live validation on 2026-11-13 confirmed SessionStart anchor delivery after `/compact`,
UserPromptSubmit memory-map delivery, and automatic Stop consumption of the sleep flag.
Ordinary Stop capture and PreCompact snapshot creation still need separate live verification;
the automated tests exercise those paths. Repeat the activation check above for your own setup.

If the agent sandbox mounts `.codex` or `.agents` read-only, run the installer from your
host terminal. Installing files does not grant hook trust.

Official hook contract: https://learn.chatgpt.com/docs/hooks.md
