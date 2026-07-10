import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from lts import anchor, digest, paths
from lts.config import load_config
from tests.helpers import make_project


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, rel: str, author: str, email: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("body", encoding="utf-8")
    _git(repo, "add", rel)
    _git(
        repo,
        "-c", f"user.name={author}", "-c", f"user.email={email}",
        "-c", "commit.gpgsign=false",
        "commit", "-m", f"add {rel}", "--author", f"{author} <{email}>",
    )


def _project(tmp_path: Path, *, anchor_at: str | None = "2020-01-01 00:00") -> Path:
    make_project(tmp_path)
    cfg = load_config(tmp_path)
    if anchor_at:
        paths.ensure_sidecar(cfg)
        anchor.write_anchor(
            paths.anchor_file(cfg), updated=anchor_at,
            last_session="S", active_topics=[], active_notes=[],
        )
    return tmp_path


def _git_vault(root: Path) -> Path:
    vault = root / ".ai_vault"
    vault.mkdir()
    _git(vault, "init", "-q", "-b", "main")
    _git(vault, "config", "user.name", "Dmitry Mitin")
    _git(vault, "config", "user.email", "d@example.com")
    return vault


def test_missing_vault_is_unavailable(tmp_path: Path):
    _project(tmp_path)
    report = digest.collect(load_config(tmp_path))
    assert report["available"] is False
    assert "vault" in report["reason"]
    assert digest.render(report) == ""


def test_no_anchor_means_no_baseline(tmp_path: Path):
    _project(tmp_path, anchor_at=None)
    _git_vault(tmp_path)
    report = digest.collect(load_config(tmp_path))
    assert report["available"] is False
    assert "anchor" in report["reason"]
    assert digest.render(report) == ""


def test_git_source_groups_by_author_and_excludes_my_own(tmp_path: Path):
    _project(tmp_path)
    vault = _git_vault(tmp_path)
    _commit(vault, "knowledge-base/Cart Service.md", "Petr Ivanov", "p@example.com")
    _commit(vault, "session-memory/petr/Session_petr_2026-07-09_1730.md", "Petr Ivanov", "p@example.com")
    _commit(vault, "knowledge-base/My Own Note.md", "Dmitry Mitin", "d@example.com")

    report = digest.collect(load_config(tmp_path))
    assert report["available"] is True
    assert report["source"] == "git"
    kb = [n["note"] for n in report["knowledge_base"]]
    assert kb == ["Cart Service"]                       # my own commit is excluded
    assert report["knowledge_base"][0]["authors"] == ["Petr Ivanov"]
    assert [n["note"] for n in report["session_memory"]] == ["Session_petr_2026-07-09_1730"]
    assert report["empty"] is False

    text = digest.render(report)
    assert "Cart Service (Petr Ivanov)" in text
    assert "1 note by Petr Ivanov" in text
    assert "My Own Note" not in text


def test_git_block_reports_uncommitted_and_disclaims_freshness(tmp_path: Path):
    _project(tmp_path)
    vault = _git_vault(tmp_path)
    _commit(vault, "knowledge-base/Cart Service.md", "Petr Ivanov", "p@example.com")
    (vault / "knowledge-base" / "Draft.md").write_text("draft", encoding="utf-8")

    report = digest.collect(load_config(tmp_path))
    assert report["git"]["uncommitted"] == 1
    assert report["git"]["upstream"] is None
    text = digest.render(report)
    assert "1 uncommitted file" in text
    assert "no network access" in text


def test_mtime_fallback_when_the_vault_is_not_a_repo(tmp_path: Path):
    _project(tmp_path, anchor_at="2020-01-01 00:00")
    vault = tmp_path / ".ai_vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base" / "Cart Service.md").write_text("cart", encoding="utf-8")

    report = digest.collect(load_config(tmp_path))
    assert report["source"] == "mtime"
    assert [n["note"] for n in report["knowledge_base"]] == ["Cart Service"]
    assert report["knowledge_base"][0]["authors"] == []
    assert report["git"] is None
    assert "Cart Service" in digest.render(report)


def test_mtime_fallback_respects_the_cutoff(tmp_path: Path):
    future = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    _project(tmp_path, anchor_at=future)
    vault = tmp_path / ".ai_vault"
    (vault / "knowledge-base").mkdir(parents=True)
    (vault / "knowledge-base" / "Cart Service.md").write_text("cart", encoding="utf-8")

    report = digest.collect(load_config(tmp_path))
    assert report["empty"] is True
    assert digest.render(report) == ""


def test_explicit_since_overrides_the_anchor(tmp_path: Path):
    _project(tmp_path)
    vault = _git_vault(tmp_path)
    _commit(vault, "knowledge-base/Cart Service.md", "Petr Ivanov", "p@example.com")
    report = digest.collect(load_config(tmp_path), since="2099-01-01 00:00")
    assert report["since"] == "2099-01-01 00:00"
    assert report["knowledge_base"] == []
    assert report["empty"] is True