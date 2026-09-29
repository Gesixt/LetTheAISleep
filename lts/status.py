"""Collect and render STM / sidecar memory metrics (LTM metrics come from Basic Memory, in the skill)."""

from __future__ import annotations

from pathlib import Path

from lts import anchor, paths, pending, stm, transcript
from lts.slug import slugify_project
from lts.config import Config


def collect(cfg: Config, *, transcript_path: Path | None = None) -> dict:
    stm_text = stm.read(paths.stm_file(cfg))
    stm_lines = len([ln for ln in stm_text.splitlines() if ln.strip()])
    stm_bytes = len(stm_text.encode("utf-8"))
    stm_tokens = len(stm_text) // 4

    snaps = pending.list_snapshots(paths.pending_dir(cfg))
    pending_bytes = sum(p.stat().st_size for p in snaps)

    pressure = None
    if transcript_path is not None:
        # `measure_context`, not `context_tokens`: `source` is what lets `_check_pressure` refuse
        # to green a figure that was estimated from a file holding no usage record at all.
        tokens, source = transcript.measure_context(Path(transcript_path))
        window = cfg.context_window
        pressure = {
            "tokens": tokens,
            "window": window,
            "ratio": round(tokens / window, 3) if window else 0.0,
            "level": transcript.pressure_level(
                tokens, window, cfg.pressure_warn, cfg.pressure_force
            ),
            "source": source,
        }

    a = anchor.read_anchor(paths.anchor_file(cfg))
    anchor_info = {
        "exists": bool(a),
        "updated": a.get("updated") if a else None,
        "active_notes": len(a.get("active_notes", [])) if a else 0,
        "active_topics": len(a.get("active_topics", [])) if a else 0,
    }

    return {
        "project": cfg.project,
        "project_slug": slugify_project(cfg.project),
        "author": cfg.author,
        "vault_path": str(paths.vault_root(cfg)),
        "stm": {"lines": stm_lines, "bytes": stm_bytes, "approx_tokens": stm_tokens},
        "pending": {"snapshots": len(snaps), "bytes": pending_bytes},
        "pressure": pressure,
        "anchor": anchor_info,
    }


def _name_hint(metrics: dict) -> str | None:
    """How to address this project on the Basic Memory CLI, per subcommand.

    There is no single "CLI name": `basic-memory project info` resolves projects by their
    **slug** and rejects the display name (with a misleading "cloud mode" error), while
    `basic-memory reindex -p` wants the **display name** and rejects the slug with
    "Project not found". Naming the wrong one sent readers straight into that error, so
    spell out both commands. Nothing to say when the two forms coincide.
    """
    name = metrics.get("project", "")
    slug = metrics.get("project_slug", "")
    if not slug or slug == name:
        return None
    return f"  Basic Memory: reindex -p {name}  ·  project info {slug}"


# One wording for both surfaces that print a figure to a human: `lts status` and `lts pressure`.
ESTIMATED = "estimated from file size — no usage record was found"


def _context_figure(pr: dict) -> str:
    """The context reading, with a percentage only where one can be true.

    This line printed "45413420/1000000 tok (4541%) — unknown": an honest level beside a figure
    that cannot be one, in the same sentence, in the report a human reads. A percentage above 100
    is never real, so the numbers are stated and no percentage is computed — the same rule
    `UserPromptSubmit._context_line` follows, kept identical on purpose.
    """
    tokens, window = pr["tokens"], pr["window"]
    if window <= 0:
        return f"{tokens} tok, no context window configured — set `[context] window` in config.toml"
    if tokens > window:
        return (f"{tokens}/{window} tok — impossible, so the measurement or `[context] window` "
                f"is wrong; treat the figure as unknown")
    warn = "  ⚠ time to /sleep" if pr["level"] == "force" else (
        "  ⚠ consider /sleep" if pr["level"] == "warn" else ""
    )
    # An estimate says so here rather than reading like a measurement: it is `len(file) // 4` over
    # a transcript that spans the whole project, which is how this figure went wrong to begin with.
    note = "" if pr.get("source", transcript.USAGE) == transcript.USAGE else f"  ({ESTIMATED})"
    return f"{tokens}/{window} tok ({round(pr['ratio'] * 100)}%) — {pr['level']}{warn}{note}"


def render(metrics: dict) -> str:
    s = metrics["stm"]
    p = metrics["pending"]
    a = metrics["anchor"]
    lines = [f"Memory status — project {metrics.get('project', '')}"]
    hint = _name_hint(metrics)
    if hint:
        lines.append(hint)
    if metrics.get("author"):
        lines.append(f"  Author:       {metrics['author']} (team mode)")
    lines += [
        f"  STM buffer:   {s['lines']} entries, {s['bytes']} B (~{s['approx_tokens']} tok)",
        f"  Sleep debt:   {p['snapshots']} pending snapshot(s), {p['bytes']} B"
        + ("  ⚠ un-slept material" if p["snapshots"] else ""),
    ]
    pr = metrics["pressure"]
    if pr is not None:
        lines.append(f"  Context:      {_context_figure(pr)}")
    anchor_state = "present" if a["exists"] else "absent"
    updated = f", updated {a['updated']}" if a["updated"] else ""
    lines.append(
        f"  Anchor:       {anchor_state}{updated}; {a['active_notes']} notes / {a['active_topics']} topics"
    )
    return "\n".join(lines)
