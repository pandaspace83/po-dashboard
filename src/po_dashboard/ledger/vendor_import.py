"""Vendors from a file, in one fixed format (DEC-44). Safe to upload again and again.

A vendor that is new is created. A vendor that already exists (matched by name, ignoring case) is never rejected:
anything new in the row is added, and nothing already there is overwritten. A row with a problem is listed and
skipped, and the other rows still go in, because uploading the same file again changes nothing already done.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.ledger import Vendor, VendorDomain
from po_dashboard.db.models import AppUser
from po_dashboard.ledger.errors import LedgerError, text
from po_dashboard.ledger.orders import MAX_CSV_BYTES, MAX_CSV_ROWS, RowError, decode_csv
from po_dashboard.ledger.vendors import (
    EMAIL,
    _domain_owner,
    add_domains,
    create_vendor,
    split_domains,
    update_vendor,
    vendor_for_name,
)

REQUIRED = ("vendor_name", "contact_email")
OPTIONAL = ("contact_name", "email_domains", "notes")
COLUMNS = REQUIRED + OPTIONAL
TEMPLATE = (
    ",".join(COLUMNS) + "\n"
    "Acme Fasteners,pat@acme.com,Pat Lee,mail.acme.com,Net 30\n"
    "Acme Fasteners,sam@acme.co.uk,Sam Ode,,\n"
)
# Many people share these, so an address at one of them says nothing about which vendor it is.
SHARED_MAIL = frozenset(
    "gmail.com googlemail.com yahoo.com outlook.com hotmail.com live.com msn.com icloud.com me.com aol.com "
    "proton.me protonmail.com comcast.net verizon.net att.net sbcglobal.net".split()
)
MAX_SHOWN = 100


@dataclass
class VendorImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[tuple[str, str]] = field(default_factory=list)  # (vendor, what was added)
    unchanged: list[str] = field(default_factory=list)
    skipped: list[RowError] = field(default_factory=list)
    ignored_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # things kept as they were, worth the person seeing


class VendorFileRejected(ValueError):
    """The file as a whole could not be read (not text, too big, or a required column missing)."""


def _rows(raw: bytes) -> tuple[list[tuple[int, dict[str, str]]], list[str]]:
    if len(raw) > MAX_CSV_BYTES:
        raise VendorFileRejected(f"The file is larger than {MAX_CSV_BYTES // (1024 * 1024)} MB.")
    content = decode_csv(raw)
    if content is None:
        raise VendorFileRejected("The file does not look like a CSV text file, so it was not read.")
    reader = csv.reader(io.StringIO(content))
    try:
        header = [cell.strip().lower() for cell in next(reader, [])]
        if not any(header):
            raise VendorFileRejected("The file is empty. The first row must name the columns.")
        repeated = sorted({name for name in header if name in COLUMNS and header.count(name) > 1})
        if repeated:
            raise VendorFileRejected(f"A column appears twice: {', '.join(repeated)}.")
        missing = [name for name in REQUIRED if name not in header]
        if missing:
            have = ", ".join(name for name in header if name)
            raise VendorFileRejected(
                f"Missing column: {', '.join(missing)}. The columns it reads are: {', '.join(COLUMNS)}. "
                f"The file has: {have}."
            )
        ignored = list(dict.fromkeys(name for name in header if name and name not in COLUMNS))
        rows: list[tuple[int, dict[str, str]]] = []
        for index, cells in enumerate(reader, start=2):
            if index > MAX_CSV_ROWS + 1:
                raise VendorFileRejected(f"More than {MAX_CSV_ROWS} rows. Split the file.")
            if not any(cell.strip() for cell in cells):
                continue
            record = {column: "" for column in COLUMNS}
            record.update({name: value for name, value in zip(header, cells, strict=False) if name in COLUMNS})
            rows.append((index, record))
    except csv.Error as problem:
        raise VendorFileRejected(f"The file could not be read as CSV: {problem}") from None
    if not rows:
        raise VendorFileRejected("The file has a header but no rows.")
    return rows, ignored


def _wanted_domains(record: dict[str, str]) -> list[str]:
    """The domains named in the row, plus the one in the contact email unless it is a shared mail service."""
    found = split_domains(record["email_domains"])
    email = record["contact_email"].strip()
    if email and EMAIL.fullmatch(email):
        domain = email.rsplit("@", 1)[1].lower().rstrip(".")
        if domain not in SHARED_MAIL and domain not in found:
            found.append(split_domains(domain)[0])
    return found


def _apply(
    db: Session, record: dict[str, str], result: VendorImportResult, actor: AppUser | None, where: str | None
) -> None:
    name = text(record["vendor_name"], "Vendor name", longest=200, required=True) or ""
    email = record["contact_email"].strip()
    if not email:
        raise LedgerError("Contact email is required: the app recognises a vendor by its email.")
    if not EMAIL.fullmatch(email):
        raise LedgerError("Contact email does not look like an email address.")
    vendor = vendor_for_name(db, name)
    other = db.scalar(
        select(Vendor.name).where(
            func.lower(Vendor.contact_email) == email.lower(), Vendor.id != (vendor.id if vendor else 0)
        )
    )
    if other is not None:
        raise LedgerError(f"{email} is already the contact email of {other}, so this row was not used.")
    wanted = _wanted_domains(record)
    if vendor is None:
        create_vendor(
            db, name=name, contact_name=record["contact_name"], contact_email=email, notes=record["notes"],
            domains=",".join(wanted), actor=actor, client_address=where,
        )  # fmt: skip
        result.created.append(name)
        return

    held = set(db.scalars(select(VendorDomain.domain).where(VendorDomain.vendor_id == vendor.id)))
    new = [domain for domain in wanted if domain not in held]
    for domain in new:
        owner = _domain_owner(db, domain)
        if owner is not None:
            raise LedgerError(f"{domain} already belongs to {owner}, so nothing was added for {vendor.name}.")
    added: list[str] = []
    if new:
        add_domains(db, vendor, ",".join(new), actor=actor, client_address=where)
        added.append("email domain " + ", ".join(new))
    # Contact details are filled only where the vendor has none: a person's entry is never overwritten.
    fill = {
        "contact_name": record["contact_name"].strip() or None,
        "contact_email": email or None,
        "notes": record["notes"].strip() or None,
    }
    changes = {key: value for key, value in fill.items() if value and not getattr(vendor, key)}
    if changes:
        update_vendor(
            db, vendor, name=vendor.name, contact_name=changes.get("contact_name", vendor.contact_name),
            contact_email=changes.get("contact_email", vendor.contact_email), notes=changes.get("notes", vendor.notes),
            actor=actor, client_address=where,
        )  # fmt: skip
        added.append(", ".join(key.replace("_", " ") for key in changes))
    if email and vendor.contact_email and vendor.contact_email.lower() != email.lower():
        result.notes.append(
            f"{vendor.name}: kept the contact email {vendor.contact_email}; {email} was not used as contact."
        )
    if added:
        result.updated.append((vendor.name, "added " + "; ".join(added)))
    else:
        result.unchanged.append(vendor.name)


def import_vendors(
    db: Session, raw: bytes, *, actor: AppUser | None = None, client_address: str | None = None
) -> VendorImportResult:
    """Apply every good row. Raises VendorFileRejected when the file cannot be read at all. The caller commits."""
    rows, ignored = _rows(raw)
    result = VendorImportResult(ignored_columns=ignored)
    for index, record in rows:
        try:
            with db.begin_nested():
                _apply(db, record, result, actor, client_address)
        except LedgerError as problem:
            result.skipped.append(RowError(index, str(problem)))
    audit(
        db, "vendor.import", actor, None, client_address,
        after={"created": len(result.created), "updated": len(result.updated), "unchanged": len(result.unchanged),
               "skipped": len(result.skipped)},
    )  # fmt: skip
    return result
