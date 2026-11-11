"""The scanner's clock (PD-3.4): an asyncio task inside the app that runs one scan every few minutes (DEC-12).

The scan itself is ordinary blocking code, so it runs in a worker thread and the web pages stay responsive. A scan
that is still running when the next tick arrives is skipped by the scanner's own guard, not queued."""

from __future__ import annotations

import asyncio
import logging

from po_dashboard.scheduler.scanner import Scanner

log = logging.getLogger("po_dashboard.scanner")

INTERVAL_SECONDS = 300  # ARCHITECTURE §2: every 5 minutes


async def run_forever(scanner: Scanner, interval: float = INTERVAL_SECONDS) -> None:
    """Scan, wait, repeat until cancelled. A scan that raises is logged by type and the loop carries on."""
    while True:
        try:
            report = await asyncio.to_thread(scanner.scan)
            if report.ran and (report.stored or report.errors):
                log.info("scan: %s stored, %s read, %s errors", report.stored, report.read, len(report.errors))
            wait = 5 if report.more else interval  # more waiting: carry on soon, not in five minutes
        except Exception as problem:
            log.warning("scan crashed: %s", type(problem).__name__)
            wait = interval
        await asyncio.sleep(wait)
