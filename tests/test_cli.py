import getpass
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from po_dashboard import cli
from po_dashboard.auth import service
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PO_DASHBOARD_DATA", str(tmp_path / "data"))
    return tmp_path / "data"


def run(*argv: str) -> int:
    with pytest.raises(SystemExit) as done:
        cli.main(list(argv))
    return int(done.value.code or 0)


def lookup(data: Path, email: str) -> object:
    engine = upgrade(Settings(data_dir=data))
    with Session(engine) as db:
        user = service.find_user(db, email)
    engine.dispose()
    return user


def test_create_admin_uses_a_hidden_prompt_and_signs_in(data: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers = iter(["short", "a long enough passphrase", "a long enough passphrase"])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    assert run("user", "create-admin", "boss@example.com") == 0
    engine = upgrade(Settings(data_dir=data))
    with Session(engine) as db:
        assert service.sign_in(db, "boss@example.com", "a long enough passphrase").session.stage == "full"
    engine.dispose()


def test_there_is_no_way_to_pass_a_password_on_the_command_line() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["user", "create-admin", "a@example.com", "--password", "x"])


def test_create_admin_refuses_once_accounts_exist(
    data: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": "a long enough passphrase")
    assert run("user", "create-admin", "boss@example.com") == 0
    assert run("user", "create-admin", "other@example.com") == 1
    assert "already exist" in capsys.readouterr().err


def test_create_and_reset_print_a_temporary_password_once(data: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run("user", "create", "buyer@example.com") == 0
    out = capsys.readouterr().out
    assert "temporary password" in out
    assert run("user", "reset-password", "buyer@example.com") == 0
    assert run("user", "reset-password", "nobody@example.com") == 1
    assert run("user", "unlock", "buyer@example.com") == 0
    assert run("user", "list") == 0
    assert "buyer@example.com\tbuyer\tok" in capsys.readouterr().out
