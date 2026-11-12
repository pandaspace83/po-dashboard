"""Everything that reads or judges QuickBooks sales data (Phase 8, DEC-58 to DEC-60): `importers/` reads and normalises
an export, `snapshots/` keeps what was uploaded, and `reconciliation/` (PD-8.4) matches invoices to customer POs. The
purchasing "Open POs" load stays in `ledger/quickbooks.py`."""
