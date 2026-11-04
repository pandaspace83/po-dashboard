import subprocess
import sys
from pathlib import Path

GUARD = Path(__file__).parent.parent / "scripts" / "check_no_real_docs.py"


def run(*files: str) -> int:
    return subprocess.run([sys.executable, str(GUARD), *files], check=False).returncode  # noqa: S603


def test_refuses_eml_pdf_db_env_outside_tests_data() -> None:
    for f in ["a.eml", "docs/x.pdf", "app.db", ".env", "x.sqlite3", "real.docx"]:
        assert run(f) == 1, f


def test_allows_synthetic_data_and_code() -> None:
    assert run("tests/data/a.eml", "tests/data/p/x.pdf", "src/po_dashboard/web.py") == 0
