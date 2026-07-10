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
        tokens = transcript.context_tokens(Path(transcript_path))
        window = cfg.context_window
        pressure = {
            "tokens": tokens,
            "window": window,
            "ratio": round(tokens / window, 3) if window else 0.0,
            "level": transcript.pressure_level(
                tokens, window, cfg.pressure_warn, cfg.pressure_force
            ),
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


def _project_line(metrics: dict) -> str:
    name = metrics.get("project", "")
    slug = metrics.get("project_slug", "")
    # the Basic Memory CLI only accepts the slug; surface it when it differs
    suffix = f" (Basic Memory CLI name: {slug})" if slug and slug != name else ""
    return f"Memory status — project {name}{suffix}"


def render(metrics: dict) -> str:
    s = metrics["stm"]
    p = metrics["pending"]
    a = metrics["anchor"]
    lines = [
        _project_line(metrics),
    ]
    if metrics.get("author"):
        lines.append(f"  Author:       {metrics['author']} (team mode)")
    lines += [
        f"  STM buffer:   {s['lines']} entries, {s['bytes']} B (~{s['approx_tokens']} tok)",
        f"  Sleep debt:   {p['snapshots']} pending snapshot(s), {p['bytes']} B"
        + ("  ⚠ un-slept material" if p["snapshots"] else ""),
    ]
    pr = metrics["pressure"]
    if pr is not None:
        pct = round(pr["ratio"] * 100)
        warn = "  ⚠ time to /sleep" if pr["level"] == "force" else (
            "  ⚠ consider /sleep" if pr["level"] == "warn" else ""
        )
        lines.append(
            f"  Context:      {pr['tokens']}/{pr['window']} tok ({pct}%) — {pr['level']}{warn}"
        )
    anchor_state = "present" if a["exists"] else "absent"
    updated = f", updated {a['updated']}" if a["updated"] else ""
    lines.append(
        f"  Anchor:       {anchor_state}{updated}; {a['active_notes']} notes / {a['active_topics']} topics"
    )
    return "\n".join(lines)
