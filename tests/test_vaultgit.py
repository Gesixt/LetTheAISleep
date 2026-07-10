import subprocess
from pathlib import Path

from lts import vaultgit


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _commit(repo: Path, rel: str, body: str, author: str, email: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    _git(repo, "add", rel)
    # -c flags instead of a scrubbed env: never assume where git lives, and never let a
    # developer's global `commit.gpgsign = true` break the suite.
    _git(
        repo,
        "-c", f"user.name={author}", "-c", f"user.email={email}",
        "-c", "commit.gpgsign=false",
        "commit", "-m", f"add {rel}", "--author", f"{author} <{email}>",
    )


def _vault(tmp_path: Path) -> Path:
    repo = tmp_path / "vault"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Dmitry Mitin")
    _git(repo, "config", "user.email", "d@example.com")
    return repo


def test_not_a_git_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert vaultgit.is_git_repo(plain) is False
    assert vaultgit.commits_since(plain, "2026-01-01 00:00") == []
    assert vaultgit.ahead_behind(plain) is None
    assert vaultgit.dirty_count(plain) == 0


def test_missing_directory_is_not_a_repo(tmp_path: Path):
    assert vaultgit.is_git_repo(tmp_path / "nope") is False


def test_local_identity(tmp_path: Path):
    repo = _vault(tmp_path)
    assert vaultgit.local_identity(repo) == "Dmitry Mitin"


def test_commits_since_groups_files_by_author(tmp_path: Path):
    repo = _vault(tmp_path)
    _commit(repo, "knowledge-base/Cart Service.md", "cart", "Petr Ivanov", "p@example.com")
    _commit(repo, "session-memory/petr/Session_petr_2026-07-09_1730.md", "s", "Petr Ivanov", "p@example.com")
    _commit(repo, "knowledge-base/Anchor Design.md", "mine", "Dmitry Mitin", "d@example.com")

    commits = vaultgit.commits_since(repo, "2020-01-01 00:00")
    assert len(commits) == 3
    authors = [author for author, _ in commits]
    assert authors.count("Petr Ivanov") == 2
    assert authors.count("Dmitry Mitin") == 1
    petr_files = [f for author, files in commits if author == "Petr Ivanov" for f in files]
    assert "knowledge-base/Cart Service.md" in petr_files


def test_commits_since_respects_the_cutoff(tmp_path: Path):
    repo = _vault(tmp_path)
    _commit(repo, "knowledge-base/Old.md", "old", "Petr Ivanov", "p@example.com")
    assert vaultgit.commits_since(repo, "2099-01-01 00:00") == []


def test_dirty_count(tmp_path: Path):
    repo = _vault(tmp_path)
    _commit(repo, "knowledge-base/Cart Service.md", "cart", "Petr Ivanov", "p@example.com")
    assert vaultgit.dirty_count(repo) == 0
    (repo / "knowledge-base" / "Draft.md").write_text("draft", encoding="utf-8")
    assert vaultgit.dirty_count(repo) == 1


def test_no_upstream(tmp_path: Path):
    repo = _vault(tmp_path)
    _commit(repo, "knowledge-base/A.md", "a", "Petr Ivanov", "p@example.com")
    assert vaultgit.ahead_behind(repo) is None
    assert vaultgit.upstream_name(repo) is None


def test_ahead_and_behind_against_a_bare_remote(tmp_path: Path):
    repo = _vault(tmp_path)
    _commit(repo, "knowledge-base/A.md", "a", "Petr Ivanov", "p@example.com")

    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True, capture_output=True)
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")

    assert vaultgit.upstream_name(repo) == "origin/main"
    assert vaultgit.ahead_behind(repo) == (0, 0)

    _commit(repo, "knowledge-base/B.md", "b", "Petr Ivanov", "p@example.com")
    assert vaultgit.ahead_behind(repo) == (1, 0)