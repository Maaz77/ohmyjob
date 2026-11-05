"""scripts/pii_gate.sh: searches contents and file names, skips only the root's .git/.venv/tmp and the caches, and
refuses to run without ripgrep."""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
TERM = "zyxwv-owner"  # a made-up term


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "pii_gate.sh", root / "scripts" / "pii_gate.sh")
    (root / "ok.txt").write_text("nothing here\n")
    (tmp_path / "terms.txt").write_text(f"\n{TERM}\n")
    return root


def gate(repo, path_env=None):
    env = {"PATH": path_env if path_env is not None else f"{Path(shutil.which('rg')).parent}:/usr/bin:/bin"}
    return subprocess.run(["bash", str(repo / "scripts" / "pii_gate.sh"), str(repo.parent / "terms.txt")],
                          capture_output=True, text=True, env=env, timeout=60)


def test_a_clean_repo_passes(repo):
    proc = gate(repo)
    assert (proc.returncode, proc.stdout) == (0, "")


def test_a_term_in_the_contents_is_a_hit(repo):
    (repo / "notes.md").write_text(f"hello {TERM.upper()}\n")
    proc = gate(repo)
    assert proc.returncode == 1 and "notes.md" in proc.stdout


def test_a_term_in_a_file_name_is_a_hit(repo):
    (repo / f"{TERM}_Resume.yaml").write_text("clean content\n")
    proc = gate(repo)
    assert proc.returncode == 1 and f"{TERM}_Resume.yaml" in proc.stdout


def test_a_folder_name_is_a_hit_too(repo):
    (repo / f"{TERM}").mkdir()
    (repo / TERM / "a.txt").write_text("clean\n")
    assert gate(repo).returncode == 1


def test_only_the_root_tmp_is_skipped(repo):
    (repo / "tmp").mkdir()
    (repo / "tmp" / "private.md").write_text(TERM)
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text(TERM)
    assert gate(repo).returncode == 0
    (repo / "docs" / "tmp").mkdir(parents=True)
    (repo / "docs" / "tmp" / "leak.md").write_text(TERM)
    proc = gate(repo)
    assert proc.returncode == 1 and "docs/tmp/leak.md" in proc.stdout


def test_without_ripgrep_it_exits_2_and_names_it(repo, tmp_path):
    bash = shutil.which("bash")
    empty = tmp_path / "nobin"
    empty.mkdir()
    for tool in ("grep", "mktemp", "rm", "dirname", "sed"):
        (empty / tool).symlink_to(shutil.which(tool))
    proc = subprocess.run([bash, str(repo / "scripts" / "pii_gate.sh"), str(repo.parent / "terms.txt")],
                          capture_output=True, text=True, env={"PATH": str(empty)}, timeout=60)
    assert proc.returncode == 2
    assert "ripgrep" in proc.stderr and proc.stderr.count("\n") == 1


@pytest.mark.parametrize("term", ["(?i)zyxwvfile", r"\p{L}yxwvfile", r"\x7ayxwvfile", "^\\./zyx.*file"])
def test_file_names_are_matched_with_the_same_regex_syntax_as_the_contents(repo, term):
    (repo.parent / "terms.txt").write_text(term + "\n")
    (repo / "zyxwvfile.yaml").write_text("clean\n")
    proc = gate(repo)
    assert proc.returncode == 1 and "zyxwvfile.yaml" in proc.stdout


def test_a_broken_term_ends_with_2_not_a_pass(repo):
    (repo.parent / "terms.txt").write_text("(unclosed\n")
    assert gate(repo).returncode == 2


def test_a_git_file_of_a_worktree_is_skipped(repo):
    (repo / ".git").write_text(f"gitdir: /x/{TERM}/.git/worktrees/a\n")
    assert gate(repo).returncode == 0
