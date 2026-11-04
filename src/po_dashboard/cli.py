"""Command line: `po-dashboard serve` and `po-dashboard user ...` (ARCHITECTURE §10.1, OPERATIONS §10)."""

from __future__ import annotations

import argparse
import getpass
import sys

import uvicorn
from sqlalchemy import select
from sqlalchemy.orm import Session

from po_dashboard.auth import passwords, service
from po_dashboard.config import Settings
from po_dashboard.db.migrate import upgrade
from po_dashboard.db.models import AppUser
from po_dashboard.web import create_app


def _ask_new_password(email: str) -> str:
    """A hidden prompt, never an argument, so the password stays out of the shell history."""
    while True:
        first = getpass.getpass("Password: ")
        try:
            passwords.check_rules(first, email)
        except passwords.PasswordRejected as problem:
            print(problem)
            continue
        if getpass.getpass("Again: ") == first:
            return first
        print("The two did not match.")


def _user(db: Session, email: str) -> AppUser:
    user = service.find_user(db, email)
    if user is None:
        raise service.AuthError(f"No account for {email}.")
    return user


def run_user_command(db: Session, args: argparse.Namespace) -> int:
    """Actions at the Mac are recorded in the audit trail with no person attached."""
    if args.user_command == "create-admin":
        if db.scalar(select(AppUser.id).limit(1)) is not None:
            raise service.AuthError(
                "Accounts already exist. Use `user create` to add someone, or `user reset-password`."
            )
        email = service.normalise_email(args.email)
        service.create_user(db, email, "admin", password=_ask_new_password(email))
        print(f"Created the admin {email}. Start the app and sign in.")
    elif args.user_command == "create":
        _, temporary = service.create_user(db, args.email, args.role)
        print(f"Created {args.email}. Give them this temporary password; it is shown once, works for 72 hours "
              f"and must be changed at first sign-in:\n\n  {temporary}\n")  # fmt: skip
    elif args.user_command == "reset-password":
        temporary = service.reset_password(db, _user(db, args.email))
        print(f"Temporary password for {args.email} (shown once, 72 hours, must be changed):\n\n  {temporary}\n")
    elif args.user_command == "unlock":
        service.unlock(db, _user(db, args.email))
        print(f"Unlocked {args.email}.")
    elif args.user_command == "list":
        for u in db.scalars(select(AppUser).order_by(AppUser.email)).all():
            state = "disabled" if u.disabled_at else ("locked" if u.locked_until else "ok")
            print(f"{u.email}\t{u.role}\t{state}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="po-dashboard")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the app")
    user = commands.add_parser("user", help="manage sign-in accounts, at this Mac")
    actions = user.add_subparsers(dest="user_command", required=True)
    actions.add_parser("create-admin", help="the first admin, before anyone has signed in").add_argument("email")
    create = actions.add_parser("create", help="add a person; prints a temporary password")
    create.add_argument("email")
    create.add_argument("--role", choices=service.ROLES, default="buyer")
    actions.add_parser("reset-password", help="print a new temporary password").add_argument("email")
    actions.add_parser("unlock", help="clear a lockout").add_argument("email")
    actions.add_parser("list", help="list accounts")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()
    engine = upgrade(settings)
    if args.command == "serve":
        engine.dispose()
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, access_log=False)
        return
    try:
        with Session(engine) as db:
            code = run_user_command(db, args)
    except (service.AuthError, passwords.PasswordRejected) as problem:
        print(problem, file=sys.stderr)
        code = 1
    finally:
        engine.dispose()
    sys.exit(code)
