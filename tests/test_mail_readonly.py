"""Mail is read-only (DEC-07, REQ-IN-02). The code under src/po_dashboard/mail has no way to send, move, flag or delete,
and nothing anywhere in the app imports a mail-sending library. The test reads the source, so a future provider that
adds one fails here before it can be run."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "po_dashboard"
MAIL = SRC / "mail"

SENDING_LIBRARIES = {"smtplib", "smtpd", "aiosmtplib", "yagmail", "imaplib", "poplib", "exchangelib"}
# method or attribute names that change a mailbox or write a file (imaplib's store, copy and append are
# covered by banning the library)
FORBIDDEN_CALLS = {
    "send", "sendmail", "send_message", "sendmessage", "trash", "untrash", "batchmodify", "modify", "expunge",
    "move", "delete", "batchdelete", "unlink", "rename", "write_bytes", "write_text", "mkdir", "rmdir", "touch",
    "chmod", "rmtree",
}
# Gmail and Graph address pieces that mean a write
FORBIDDEN_TEXT = ("/send", "/trash", "/untrash", "batchModify", ":modify", "batchDelete", "mail.google.com/",
                  "Mail.ReadWrite", "Mail.Send", "gmail.modify", "gmail.send", "gmail.compose", "gmail.insert",
                  "/sendMail", "/move", "/copy", "/forward", "/reply")


def trees(folder: Path) -> list[tuple[Path, ast.Module]]:
    return [(p, ast.parse(p.read_text(encoding="utf-8"))) for p in sorted(folder.rglob("*.py"))]


def test_no_module_in_the_app_can_send_mail() -> None:
    for path, tree in trees(SRC):
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            assert not SENDING_LIBRARIES & set(names), f"{path.name} imports {names}"


def test_the_mail_package_calls_nothing_that_changes_a_mailbox_or_a_file() -> None:
    found = trees(MAIL)
    assert found
    for path, tree in found:
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr.lower() in FORBIDDEN_CALLS:
                raise AssertionError(f"{path.name}:{node.lineno} uses .{node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                mode = ""
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                mode += "".join(str(k.value.value) for k in node.keywords
                                if k.arg == "mode" and isinstance(k.value, ast.Constant))
                assert not set(mode) & set("wax+"), f"{path.name}:{node.lineno} opens a file for writing"


def test_the_mail_package_names_no_write_address_or_scope() -> None:
    for path in sorted(MAIL.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for word in FORBIDDEN_TEXT:
            assert word not in text, f"{path.name} contains {word!r}"
