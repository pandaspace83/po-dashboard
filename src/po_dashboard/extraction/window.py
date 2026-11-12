"""The reading window (DEC-54, ARCHITECTURE section 6.1). The model reads 8,192 tokens and may answer with 2,048, so a
document gets about 15,000 characters (3 characters a token, cautious for part numbers).

A longer text loses its quoted earlier messages first (everything after the quote note, said so in a note), then is cut
and flagged `read_in_part`. A document is never silently cut and never split into parts in version 1."""

from __future__ import annotations

from dataclasses import dataclass

from po_dashboard.intake.text import QUOTE_NOTE

WINDOW_CHARS = 15_000
QUOTES_LEFT_OUT = "[the quoted earlier messages were left out to fit the reading window]"
CUT_OFF = "[the rest of this document was left out to fit the reading window]"


@dataclass(frozen=True)
class Windowed:
    text: str
    left_out_quotes: bool = False
    read_in_part: bool = False


def fit(document_text: str, limit: int = WINDOW_CHARS) -> Windowed:
    if len(document_text) <= limit:
        return Windowed(document_text)
    quoted_at = document_text.find(QUOTE_NOTE)
    text = document_text
    left_out = False
    if quoted_at != -1:
        text, left_out = document_text[:quoted_at].rstrip() + "\n" + QUOTES_LEFT_OUT + "\n", True
    if len(text) <= limit:
        return Windowed(text, left_out_quotes=left_out)
    return Windowed(text[: max(limit - len(CUT_OFF) - 2, 0)].rstrip() + "\n" + CUT_OFF + "\n", left_out, True)
