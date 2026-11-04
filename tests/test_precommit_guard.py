import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
GUARD = SCRIPTS / "check_no_real_docs.py"


def run(*files: str) -> int:
    return subprocess.run([sys.executable, str(GUARD), *files], check=False).returncode


@pytest.mark.parametrize(
    "path",
    ["a.eml", "docs/x.pdf", "X.PDF", "app.db", "app.db-wal", ".env", ".env.local", "x.sqlite3",
     "real.docx", "orders.csv", "scan.tiff", "server.pem", "real-docs/note.txt", "tests/data/real-docs/a.txt"],
)  # fmt: skip
def test_refuses_outside_tests_data(path: str) -> None:
    assert run(path) == 1


def test_allows_synthetic_data_and_code() -> None:
    assert run("tests/data/a.eml", "tests/data/p/x.pdf", "src/po_dashboard/web.py", ".env.example") == 0


# The hook itself, against real staged files: the argument handling is where the guard can leak.


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", ".")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "test")
    (tmp_path / "scripts").mkdir()
    shutil.copy(GUARD, tmp_path / "scripts")
    shutil.copy(SCRIPTS / "pre-commit", tmp_path / ".git" / "hooks" / "pre-commit")
    return tmp_path


def stage(repo: Path, name: str) -> None:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("synthetic")
    subprocess.run(["git", "add", "--", name], cwd=repo, check=True)


def hook(repo: Path) -> int:
    return subprocess.run([".git/hooks/pre-commit"], cwd=repo, check=False, capture_output=True).returncode


@pytest.mark.parametrize("name", ["docs/plain.pdf", "docs/Rechnung_Müller.pdf", "docs/my invoice.pdf"])
def test_hook_refuses_staged_documents(repo: Path, name: str) -> None:
    stage(repo, name)
    assert hook(repo) != 0


@pytest.mark.parametrize("name", ["tests/data/my sample.pdf", "tests/data/Müller.eml", "src/app.py"])
def test_hook_allows_synthetic_files_and_code(repo: Path, name: str) -> None:
    stage(repo, name)
    assert hook(repo) == 0


def test_hook_refuses_a_rename_out_of_tests_data(repo: Path) -> None:
    stage(repo, "tests/data/a.pdf")
    subprocess.run(["git", "commit", "-qm", "base", "--no-verify"], cwd=repo, check=True)
    (repo / "docs").mkdir()
    subprocess.run(["git", "mv", "tests/data/a.pdf", "docs/a.pdf"], cwd=repo, check=True)
    assert hook(repo) != 0


def test_hook_passes_with_nothing_staged(repo: Path) -> None:
    assert hook(repo) == 0
