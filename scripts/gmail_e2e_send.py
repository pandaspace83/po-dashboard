"""Send the made-up emails from `gmail_e2e.py build` into the TEST Gmail account (PD-4.8). Not part of the app.

    uv run python scripts/gmail_e2e_send.py OUT_DIR

The account sends each email to itself, so it lands in its Inbox. It needs a Google *app password* for the TEST account
(Google Account > Security > 2-Step Verification on > App passwords). The address and the app password are typed at
prompts: the password is hidden, and neither is saved, printed or put in an argument. Only the made-up emails are sent
(DEC-13). Gmail rewrites From, so the vendors' domains are lost: expect `sender_not_vendor` flags.
"""

from __future__ import annotations

import getpass
import smtplib
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    files = sorted((Path(sys.argv[1]) / "Inbox").glob("E*.eml"))
    if not files:
        print("No E*.eml files in OUT_DIR/Inbox. Run gmail_e2e.py build first.")
        return 2
    address = input("TEST Gmail address (the account that must hold only made-up mail): ").strip()
    password = getpass.getpass("App password (hidden): ").replace(" ", "")
    if input(f"Send {len(files)} made-up emails from and to {address}? Type yes: ").strip().lower() != "yes":
        print("Nothing sent.")
        return 1
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(address, password)
        for path in files:
            msg = BytesParser(policy=policy.SMTP).parsebytes(path.read_bytes())
            for header in ("From", "To", "Message-ID", "Date"):
                del msg[header]
            msg["From"] = msg["To"] = address
            smtp.send_message(msg)
            print(f"sent {path.stem}")
    print("Done. Open Gmail and look for 18 messages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
