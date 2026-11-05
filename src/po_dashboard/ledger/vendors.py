"""Vendors and the email domains they write from (REQ-PO-04). Every change is audited."""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from po_dashboard.auth.service import audit
from po_dashboard.db.ledger import PurchaseOrder, Vendor, VendorDomain
from po_dashboard.db.models import AppUser
from po_dashboard.ledger.errors import LedgerError, text

DOMAIN = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def clean_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("@").rstrip(".")
    if not DOMAIN.fullmatch(domain):
        raise LedgerError(
            f"'{value.strip()[:60]}' is not an email domain. Write it like acme.com, with no @ or spaces."
        )
    return domain


def split_domains(value: str | None) -> list[str]:
    parts = [p for p in re.split(r"[,\s;]+", value or "") if p]
    return list(dict.fromkeys(clean_domain(p) for p in parts))


def _fields(
    name: str | None, contact_name: str | None, contact_email: str | None, notes: str | None
) -> dict[str, str | None]:
    email = text(contact_email, "Contact email", longest=254)
    if email and not EMAIL.fullmatch(email):
        raise LedgerError("Contact email does not look like an email address.")
    return {
        "name": text(name, "Name", longest=200, required=True),
        "contact_name": text(contact_name, "Contact name", longest=200),
        "contact_email": email,
        "notes": text(notes, "Notes", longest=4000, multiline=True),
    }


def _name_taken(db: Session, name: str, except_id: int | None = None) -> bool:
    query = select(Vendor.id).where(func.lower(Vendor.name) == name.lower())
    if except_id is not None:
        query = query.where(Vendor.id != except_id)
    return db.scalar(query) is not None


def _domain_owner(db: Session, domain: str) -> str | None:
    return db.scalar(select(Vendor.name).join(VendorDomain).where(VendorDomain.domain == domain))


def create_vendor(
    db: Session, *, name: str | None, contact_name: str | None = None, contact_email: str | None = None,
    notes: str | None = None, domains: str | None = None, actor: AppUser | None = None,
    client_address: str | None = None,
) -> Vendor:  # fmt: skip
    fields = _fields(name, contact_name, contact_email, notes)
    vendor_name = fields["name"] or ""
    if _name_taken(db, vendor_name):
        raise LedgerError("A vendor with that name already exists.")
    wanted = split_domains(domains)
    for domain in wanted:
        if (owner := _domain_owner(db, domain)) is not None:
            raise LedgerError(f"{domain} already belongs to {owner}.")
    vendor = Vendor(**fields)
    db.add(vendor)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise LedgerError("A vendor with that name already exists.") from None
    for domain in wanted:
        db.add(VendorDomain(vendor_id=vendor.id, domain=domain))
    db.flush()
    audit(
        db,
        "vendor.create",
        actor,
        f"vendor:{vendor.id}",
        client_address,
        after={"name": vendor_name, "domains": wanted},
    )
    return vendor


def update_vendor(
    db: Session, vendor: Vendor, *, name: str | None, contact_name: str | None, contact_email: str | None,
    notes: str | None, actor: AppUser | None = None, client_address: str | None = None,
) -> None:  # fmt: skip
    fields = _fields(name, contact_name, contact_email, notes)
    if _name_taken(db, fields["name"] or "", except_id=vendor.id):
        raise LedgerError("A vendor with that name already exists.")
    before = {key: getattr(vendor, key) for key in fields}
    if before == fields:
        return
    for key, value in fields.items():
        setattr(vendor, key, value)
    db.flush()
    audit(db, "vendor.update", actor, f"vendor:{vendor.id}", client_address, before=before, after=fields)


def add_domains(
    db: Session, vendor: Vendor, domains: str | None, actor: AppUser | None = None, client_address: str | None = None
) -> list[str]:
    wanted = split_domains(domains)
    if not wanted:
        raise LedgerError("Write at least one domain, like acme.com.")
    have = set(db.scalars(select(VendorDomain.domain).where(VendorDomain.vendor_id == vendor.id)))
    added = []
    for domain in wanted:
        if domain in have:
            continue
        if (owner := _domain_owner(db, domain)) is not None:
            raise LedgerError(f"{domain} already belongs to {owner}.")
        added.append(domain)
    for domain in added:
        db.add(VendorDomain(vendor_id=vendor.id, domain=domain))
    db.flush()
    for domain in added:
        audit(db, "vendor.domain_add", actor, f"vendor:{vendor.id}", client_address, after=domain)
    return added


def remove_domain(
    db: Session, vendor: Vendor, domain_id: int, actor: AppUser | None = None, client_address: str | None = None
) -> None:
    row = db.get(VendorDomain, domain_id)
    if row is None or row.vendor_id != vendor.id:
        raise LedgerError("That domain was not found for this vendor.")
    db.delete(row)
    db.flush()
    audit(db, "vendor.domain_remove", actor, f"vendor:{vendor.id}", client_address, before=row.domain)


def vendor_for_name(db: Session, name: str) -> Vendor | None:
    return db.scalar(select(Vendor).where(func.lower(Vendor.name) == name.strip().lower()))


def po_counts(db: Session) -> dict[int, int]:
    return {
        row[0]: row[1]
        for row in db.execute(select(PurchaseOrder.vendor_id, func.count()).group_by(PurchaseOrder.vendor_id))
    }
