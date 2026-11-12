"""The scanner's clock (PD-3.4): an asyncio task inside the app that runs one scan every few minutes (DEC-12).

The scan itself is ordinary blocking code, so it runs in a worker thread and the web pages stay responsive. A scan
that is still running when the next tick arrives is skipped by the scanner's own guard, not queued."""

from __future__ import annotations

import asyncio
import logging

from po_dashboard.extraction.runner import Extractor
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


# The extractor's pace: straight on to the next document after one, a short rest when nothing is due, a longer one
# while the model server cannot be reached (nothing is counted against any document for that).
IDLE_SECONDS, MODEL_DOWN_SECONDS = 5.0, 60.0


async def run_extractor(extractor: Extractor, idle: float = IDLE_SECONDS, down: float = MODEL_DOWN_SECONDS) -> None:
    """Recover what a crash left half-read, then read queued documents one at a time until cancelled. Apart from the
    scanner, so mail keeps arriving while the model works (DEC-54)."""
    try:
        await asyncio.to_thread(extractor.recover)
    except Exception as problem:
        log.warning("extractor recovery failed: %s", type(problem).__name__)
    while True:
        try:
            outcome = await asyncio.to_thread(extractor.run_once)
            wait = {"idle": idle, "model_down": down}.get(outcome.kind, 0.0)
        except Exception as problem:
            log.warning("extractor crashed: %s", type(problem).__name__)
            wait = idle
        await asyncio.sleep(wait)
