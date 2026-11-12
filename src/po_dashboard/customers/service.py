"""Customers, the names QuickBooks uses for them, and their email domains (REQ-CO-01, DEC-57). Every change is audited.

A name is matched exactly (case and spacing ignored) and never because it looks similar: an export's name finds its
customer only through a name someone added here (DEC-59)."""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from po_dashboard.auth.service import audit
from po_dashboard.customers import config
from po_dashboard.customers.keys import name_key
from po_dashboard.db.customers import Customer, CustomerDomain, CustomerName, CustomerPO, SalesInvoice
from po_dashboard.db.models import AppUser
from po_dashboard.ledger.errors import LedgerError, check_seen, text
from po_dashboard.ledger.vendors import EMAIL, clean_domain, split_domains


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


def split_names(value: str | None) -> list[str]:
    """QuickBooks names, one per line or separated by semicolons (a name may contain a comma)."""
    parts = [" ".join(part.split()) for part in re.split(r"[\n;]+", value or "")]
    return list(dict.fromkeys(part for part in parts if part))


def name_owner(db: Session, key: str, except_id: int | None = None) -> str | None:
    """Who already uses this name, as a customer's own name or as a QuickBooks name for it."""
    own = select(Customer.name).where(Customer.name_key == key)
    alias = (
        select(Customer.name)
        .join(CustomerName, CustomerName.customer_id == Customer.id)
        .where(CustomerName.name_key == key)
    )
    if except_id is not None:
        own, alias = own.where(Customer.id != except_id), alias.where(Customer.id != except_id)
    return db.scalar(own) or db.scalar(alias)


def _taken_message(db: Session, key: str, shown: str, except_id: int | None = None) -> str | None:
    """Why a name cannot be used, or None if it is free."""
    own = select(Customer.id).where(Customer.name_key == key)
    if except_id is not None:
        own = own.where(Customer.id != except_id)
    if db.scalar(own) is not None:
        return "A customer with that name already exists."
    if (owner := name_owner(db, key, except_id)) is not None:
        return f"The name {shown[:60]} is already used for {owner}."
    return None


def _domain_owner(db: Session, domain: str) -> str | None:
    return db.scalar(select(Customer.name).join(CustomerDomain).where(CustomerDomain.domain == domain))


def create_customer(
    db: Session, *, name: str | None, contact_name: str | None = None, contact_email: str | None = None,
    notes: str | None = None, qb_names: str | None = None, domains: str | None = None,
    actor: AppUser | None = None, client_address: str | None = None,
) -> Customer:  # fmt: skip
    fields = _fields(name, contact_name, contact_email, notes)
    shown = fields["name"] or ""
    if (message := _taken_message(db, name_key(shown), shown)) is not None:
        raise LedgerError(message)
    aliases = [n for n in split_names(qb_names) if name_key(n) != name_key(shown)]
    for alias in aliases:
        if (owner := name_owner(db, name_key(alias))) is not None:
            raise LedgerError(f"The name {alias[:60]} is already used for {owner}.")
    wanted = split_domains(domains)
    for domain in wanted:
        if (owner := _domain_owner(db, domain)) is not None:
            raise LedgerError(f"{domain} already belongs to {owner}.")
    customer = Customer(name_key=name_key(shown), **fields)
    db.add(customer)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise LedgerError("A customer with that name already exists.") from None
    for alias in aliases:
        db.add(CustomerName(customer_id=customer.id, name=alias, name_key=name_key(alias)))
    for domain in wanted:
        db.add(CustomerDomain(customer_id=customer.id, domain=domain))
    db.flush()
    audit(db, "customer.create", actor, f"customer:{customer.id}", client_address,
          after={"name": shown, "qb_names": aliases, "domains": wanted})  # fmt: skip
    return customer


def update_customer(
    db: Session, customer: Customer, *, name: str | None, contact_name: str | None, contact_email: str | None,
    notes: str | None, actor: AppUser | None = None, client_address: str | None = None,
    seen: int | None = None, check: bool = False,
) -> None:  # fmt: skip
    if check:  # a form says which version it was drawn from (DEC-52)
        check_seen(customer.version, seen, "This customer")
    fields = _fields(name, contact_name, contact_email, notes)
    key = name_key(fields["name"] or "")
    if (message := _taken_message(db, key, fields["name"] or "", customer.id)) is not None:
        raise LedgerError(message)
    before = {k: getattr(customer, k) for k in fields}
    if before == fields:
        return
    customer.name_key = key
    for k, value in fields.items():
        setattr(customer, k, value)
    try:
        db.flush()
    except StaleDataError:
        raise LedgerError("This customer was changed by someone else. Reload the page.") from None
    audit(db, "customer.update", actor, f"customer:{customer.id}", client_address, before=before, after=fields)


def set_active(
    db: Session, customer: Customer, active: bool, *, actor: AppUser | None = None, client_address: str | None = None,
    seen: int | None = None, check: bool = False,
) -> None:  # fmt: skip
    """An inactive customer keeps every record but takes no new POs."""
    if check:
        check_seen(customer.version, seen, "This customer")
    if customer.active == active:
        return
    customer.active = active
    try:
        db.flush()
    except StaleDataError:
        raise LedgerError("This customer was changed by someone else. Reload the page.") from None
    audit(
        db, "customer.activate" if active else "customer.deactivate", actor, f"customer:{customer.id}", client_address
    )


def add_names(
    db: Session, customer: Customer, names: str | None, actor: AppUser | None = None, client_address: str | None = None
) -> list[str]:
    wanted = split_names(names)
    if not wanted:
        raise LedgerError("Write at least one name, as QuickBooks shows it.")
    added = []
    for name in wanted:
        key = name_key(name)
        owner = name_owner(db, key)
        if owner is not None:
            raise LedgerError(f"The name {name[:60]} is already used for {owner}.")
        db.add(CustomerName(customer_id=customer.id, name=name, name_key=key))
        added.append(name)
    db.flush()
    for name in added:
        audit(db, "customer.name_add", actor, f"customer:{customer.id}", client_address, after=name)
    return added


def remove_name(
    db: Session, customer: Customer, name_id: int, actor: AppUser | None = None, client_address: str | None = None
) -> None:
    row = db.get(CustomerName, name_id)
    if row is None or row.customer_id != customer.id:
        raise LedgerError("That name was not found for this customer.")
    db.delete(row)
    db.flush()
    audit(db, "customer.name_remove", actor, f"customer:{customer.id}", client_address, before=row.name)


def add_domains(
    db: Session, customer: Customer, domains: str | None, actor: AppUser | None = None,
    client_address: str | None = None,
) -> list[str]:  # fmt: skip
    wanted = split_domains(domains)
    if not wanted:
        raise LedgerError("Write at least one domain, like globex.com.")
    have = set(db.scalars(select(CustomerDomain.domain).where(CustomerDomain.customer_id == customer.id)))
    added = []
    for domain in wanted:
        if domain in have:
            continue
        if (owner := _domain_owner(db, domain)) is not None:
            raise LedgerError(f"{domain} already belongs to {owner}.")
        added.append(domain)
    if not added:
        raise LedgerError(f"{', '.join(wanted)} already added to this customer.")
    for domain in added:
        db.add(CustomerDomain(customer_id=customer.id, domain=domain))
    db.flush()
    for domain in added:
        audit(db, "customer.domain_add", actor, f"customer:{customer.id}", client_address, after=domain)
    return added


def remove_domain(
    db: Session, customer: Customer, domain_id: int, actor: AppUser | None = None, client_address: str | None = None
) -> None:
    row = db.get(CustomerDomain, domain_id)
    if row is None or row.customer_id != customer.id:
        raise LedgerError("That domain was not found for this customer.")
    db.delete(row)
    db.flush()
    audit(db, "customer.domain_remove", actor, f"customer:{customer.id}", client_address, before=row.domain)


# ---- finding a customer: exactly, never by resemblance


def find_by_name(db: Session, name: str, *, job_split: bool = False) -> Customer | None:
    """The customer whose own name, or a QuickBooks name, equals `name` (case and spacing ignored). With `job_split`, a
    "Customer:Job" that matches nothing as written is tried as the part before the first colon (OPEN-24)."""
    key = name_key(name)
    if not key:
        return None
    found = db.scalar(select(Customer).where(Customer.name_key == key)) or db.scalar(
        select(Customer).join(CustomerName, CustomerName.customer_id == Customer.id).where(CustomerName.name_key == key)
    )
    if found is None and job_split and ":" in name:
        return find_by_name(db, name.split(":", 1)[0])
    return found


def find_for_export(db: Session, name: str) -> Customer | None:
    """For a name written in a QuickBooks export: the Customer:Job rule is a setting."""
    return find_by_name(db, name, job_split=config.flag(db, "customer_job_split"))


def find_by_domain(db: Session, domain: str) -> Customer | None:
    return db.scalar(
        select(Customer)
        .join(CustomerDomain, CustomerDomain.customer_id == Customer.id)
        .where(CustomerDomain.domain == clean_domain(domain))
    )


def search(db: Session, query: str, limit: int = 8) -> list[Customer]:
    wanted = query.strip().lower()
    if not wanted:
        return []
    return list(db.scalars(
        select(Customer).where(Customer.active.is_(True), Customer.name_key.contains(wanted, autoescape=True))
        .order_by(Customer.name_key).limit(limit)
    ))  # fmt: skip


def counts(db: Session) -> dict[int, tuple[int, int]]:
    """For each customer, how many customer POs (original numbers, not revisions) and sales invoices it has."""
    orders: dict[int, int] = {}
    for customer_id, _key in db.execute(select(CustomerPO.customer_id, CustomerPO.po_key).distinct()):
        orders[customer_id] = orders.get(customer_id, 0) + 1
    invoices: dict[int, int] = {}
    for invoice_customer in db.scalars(select(SalesInvoice.customer_id).where(SalesInvoice.customer_id.is_not(None))):
        if invoice_customer is not None:
            invoices[invoice_customer] = invoices.get(invoice_customer, 0) + 1
    return {cid: (orders.get(cid, 0), invoices.get(cid, 0)) for cid in set(orders) | set(invoices)}
