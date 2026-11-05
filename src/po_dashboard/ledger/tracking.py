"""A link to the carrier's own tracking page. The app makes no carrier request itself (DEC-01, DEC-40): the person's
browser opens the link when they click it. Only well-known carriers get a link, and only for a plain tracking number."""

from __future__ import annotations

import re
from urllib.parse import quote

PLAIN_NUMBER = re.compile(r"[A-Za-z0-9]{6,40}")
CARRIERS = (
    ("ups", "https://www.ups.com/track?tracknum={}"),
    ("fedex", "https://www.fedex.com/fedextrack/?trknbr={}"),
    ("usps", "https://tools.usps.com/go/TrackConfirmAction?tLabels={}"),
    ("dhl", "https://www.dhl.com/global-en/home/tracking.html?tracking-id={}"),
)


def tracking_url(carrier: str | None, number: str | None) -> str | None:
    """The carrier's page for this number, or None when the carrier is not known or the number is not plain."""
    if not carrier or not number:
        return None
    digits = number.replace(" ", "")
    if not PLAIN_NUMBER.fullmatch(digits):
        return None
    name = carrier.casefold()
    for key, pattern in CARRIERS:
        if key in name:
            return pattern.format(quote(digits, safe=""))
    return None
