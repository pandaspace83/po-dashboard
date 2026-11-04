"""scripts/setup.sh against stub tools, so nothing is installed or downloaded."""

import os
import shutil
import stat
import subprocess
from pathlib import Path

SETUP = Path(__file__).parent.parent / "scripts" / "setup.sh"


def run_setup(tmp_path: Path, ollama_list: str, answer: str) -> tuple[subprocess.CompletedProcess[str], Path]:
    (tmp_path / "scripts").mkdir()
    shutil.copy(SETUP, tmp_path / "scripts" / "setup.sh")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    pulled = tmp_path / "pulled"
    stubs = {
        "uname": 'echo Darwin',
        "uv": "exit 0",
        "tesseract": "exit 0",
        "ollama": f'[ "$1" = pull ] && {{ echo "$2" > "{pulled}"; exit 0; }}\ncat <<OUT\n{ollama_list}\nOUT',
    }
    for name, body in stubs.items():
        stub = bin_dir / name
        stub.write_text(f"#!/bin/bash\n{body}\n")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:/usr/bin:/bin"}
    result = subprocess.run(
        ["/bin/bash", str(tmp_path / "scripts" / "setup.sh")],
        input=answer, env=env, capture_output=True, text=True, check=False,
    )  # fmt: skip
    return result, pulled


LISTED = "NAME ID SIZE MODIFIED\nqwen2.5:14b-instruct 7cdf5a0187d5 9.0 GB 3 weeks ago"


def test_does_not_offer_the_download_when_the_model_is_present(tmp_path: Path) -> None:
    result, pulled = run_setup(tmp_path, LISTED, answer="y\n")
    assert result.returncode == 0, result.stderr
    assert "already present" in result.stdout
    assert not pulled.exists()


def test_states_the_size_and_does_not_pull_without_a_yes(tmp_path: Path) -> None:
    result, pulled = run_setup(tmp_path, "NAME ID SIZE MODIFIED", answer="n\n")
    assert result.returncode == 0, result.stderr
    assert "9 GB" in result.stderr + result.stdout
    assert not pulled.exists()


def test_no_answer_at_all_does_not_pull(tmp_path: Path) -> None:
    result, pulled = run_setup(tmp_path, "NAME ID SIZE MODIFIED", answer="")
    assert result.returncode == 0, result.stderr
    assert not pulled.exists()


def test_pulls_on_a_yes(tmp_path: Path) -> None:
    result, pulled = run_setup(tmp_path, "NAME ID SIZE MODIFIED", answer="y\n")
    assert result.returncode == 0, result.stderr
    assert pulled.read_text().strip() == "qwen2.5:14b-instruct"
