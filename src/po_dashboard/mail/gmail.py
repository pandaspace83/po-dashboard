"""Reading a Gmail mailbox (PD-3.5, DEC-50, ARCHITECTURE §7). Read-only: the scope is `gmail.readonly` (so Google itself
refuses a write), and this file makes GET requests only, to one fixed host.

- **Position** is Gmail's history id plus any message ids found but not yet handed over. A new mailbox starts from the
  mail that arrived since the mailbox's start date; if Google no longer has a saved history id the same window is read
  again and the scanner's dedupe (REQ-IN-05) absorbs the repeats.
- **Retries** only for what a retry can fix: connect errors and timeouts, 429, 500 to 504, and the two documented
  rate-limit answers Google sends as 403. Exponential backoff with jitter and a cap, honouring `Retry-After`. A 401
  gets one token refresh, then needs a reconnect; any other 403 is not retried.
- **Nothing from the mailbox decides an address:** message ids from Google are checked against a strict pattern before
  they are put in a path.
"""

from __future__ import annotations

import base64
import binascii
import json
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from po_dashboard.mail.gmail_auth import TIMEOUT, GmailError, NeedsReconnect
from po_dashboard.mail.source import FOLDERS, INBOX, MAX_MESSAGE_BYTES, SENT, FetchResult, Health, RawMessage, Skipped

API = "https://gmail.googleapis.com/gmail/v1/users/me"
LABELS = {INBOX: "INBOX", SENT: "SENT"}
ID = re.compile(r"[0-9A-Za-z_-]{1,64}")
RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
MAX_TRIES, BASE_DELAY, MAX_DELAY, MAX_RETRY_AFTER = 5, 1.0, 30.0, 60.0
BACKLOG_CAP = 500  # message ids remembered at once on a first read; a bigger backlog is the newest 500
FALLBACK_DAYS = 7  # the window re-read when Google no longer knows the saved position


class GmailSource:
    def __init__(
        self, token: Callable[[], str], renew: Callable[[], str], *, since: datetime | None = None,
        http: httpx.Client | None = None, sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:  # fmt: skip
        self._token, self._renew, self._since = token, renew, since
        self._http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        self._sleep, self._jitter = sleep, jitter

    def folders(self) -> tuple[str, ...]:
        return FOLDERS

    # ---------------------------------------------------------------- the one way out

    def _get(
        self, path: str, params: dict[str, str | int] | None = None, *, missing_ok: bool = False
    ) -> dict[str, Any]:
        """GET `API + path`, with the retries above. Returns the decoded JSON; raises GmailError with fixed words.
        A 404 raises _NotFound only where the caller expects one (a removed message, a forgotten history id)."""
        refreshed = False
        for attempt in range(MAX_TRIES):
            try:
                response = self._http.get(
                    API + path, params=params, headers={"Authorization": f"Bearer {self._token()}"}
                )
            except (httpx.ConnectError, httpx.TimeoutException):
                self._wait(attempt, None)
                continue
            except httpx.HTTPError:
                raise GmailError("Could not read Gmail (the connection failed).") from None
            code = response.status_code
            if code == 200:
                try:
                    body = response.json()
                except ValueError:
                    raise GmailError("Gmail sent an answer that could not be read.") from None
                if not isinstance(body, dict):
                    raise GmailError("Gmail sent an answer that could not be read.")
                return body
            if code == 401:
                if refreshed:
                    raise NeedsReconnect("Google no longer accepts this sign-in. Connect the mailbox again.")
                refreshed = True
                self._renew()
                continue
            if code in (429, 500, 502, 503, 504) or (code == 403 and self._rate_limited(response)):
                self._wait(attempt, response.headers.get("Retry-After"))
                continue
            if code == 404 and missing_ok:
                raise _NotFound
            raise GmailError(self._refusal(response))
        raise GmailError("Gmail did not answer after several tries.")

    @staticmethod
    def _refusal(response: httpx.Response) -> str:
        """Fixed words for the refusals a person can fix. Only Google's short reason codes are looked at, and they are
        never repeated back: the message is chosen from this list."""
        try:
            error = response.json()["error"]
            reasons = {str(e.get("reason")) for e in error.get("errors", []) if isinstance(e, dict)}
            reasons |= {str(d.get("reason")) for d in error.get("details", []) if isinstance(d, dict)}
        except (ValueError, KeyError, TypeError, AttributeError):
            reasons = set()
        if response.status_code == 403 and reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
            return ("Gmail refused the request (403): the Gmail API is not turned on for this Google project. "
                    "In the Google Cloud console open APIs & Services, Library, Gmail API, and click Enable.")
        if response.status_code == 403 and reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
            return ("Gmail refused the request (403): read access was not granted. Connect again and tick the box "
                    "that lets the app view your email.")  # fmt: skip
        return f"Gmail refused the request (status {response.status_code})."

    @staticmethod
    def _rate_limited(response: httpx.Response) -> bool:
        try:
            errors = response.json()["error"]["errors"]
            return any(isinstance(e, dict) and e.get("reason") in RATE_LIMIT_REASONS for e in errors)
        except (ValueError, KeyError, TypeError):
            return False

    def _wait(self, attempt: int, retry_after: str | None) -> None:
        if attempt == MAX_TRIES - 1:
            return  # the last try failed: the loop ends and raises
        if retry_after and retry_after.strip().isdigit():
            delay = min(float(retry_after), MAX_RETRY_AFTER)
        else:
            delay = min(MAX_DELAY, BASE_DELAY * 2**attempt) * (0.5 + self._jitter() / 2)
        self._sleep(delay)

    # ---------------------------------------------------------------- the interface

    def fetch_new(self, folder: str, position: str | None, limit: int = 50) -> FetchResult:
        if folder not in LABELS:
            raise ValueError(f"unknown folder {folder!r}")
        history, queued = _read_position(position)
        if history is None:
            history, queued = self._start(folder)
        else:
            try:
                found, latest = self._added_since(folder, history)
            except _NotFound:  # Google forgot the position: read the recent window again; dedupe absorbs repeats
                history, queued = self._start(folder)
            else:
                queued, history = _merge(queued, found), latest
        ready, rest = queued[:limit], queued[limit:]
        messages: list[RawMessage] = []
        skipped: list[Skipped] = []
        for message_id in ready:
            try:
                raw = self._raw(message_id)
            except _NotFound:  # deleted since it was listed: nothing to read
                skipped.append(Skipped(message_id, folder, "the message was removed before it was read"))
                continue
            if len(raw) > MAX_MESSAGE_BYTES:
                skipped.append(Skipped(message_id, folder, "larger than the 50 MB limit"))
                continue
            messages.append(RawMessage(message_id, folder, raw))
        return FetchResult(messages, json.dumps({"h": history, "q": rest}), skipped, more=bool(rest))

    def account(self) -> str:
        """The address of the signed-in Gmail account, as Google gives it."""
        address = self._get("/profile").get("emailAddress")
        if not isinstance(address, str) or "@" not in address:
            raise GmailError("Gmail did not say which account this is.")
        return address.lower()

    def health(self) -> Health:
        try:
            self._get("/profile")
        except NeedsReconnect:
            return Health(False, "Google no longer accepts the sign-in. Connect the mailbox again.")
        except GmailError as problem:
            return Health(False, str(problem))
        return Health(True, "Readable.")

    # ---------------------------------------------------------------- pieces

    def _now_id(self) -> str:
        return str(self._get("/profile")["historyId"])

    def _start(self, folder: str) -> tuple[str, list[str]]:
        """The history id as of now (taken first, so nothing arriving during the listing is missed) and the ids of the
        mail since the start date, oldest first."""
        baseline = self._now_id()
        since = self._since or datetime.now(UTC) - timedelta(days=FALLBACK_DAYS)
        ids: list[str] = []
        token = ""
        while len(ids) < BACKLOG_CAP:
            params: dict[str, str | int] = {"labelIds": LABELS[folder], "q": f"after:{int(since.timestamp())}",
                                            "maxResults": 100}  # fmt: skip
            if token:
                params["pageToken"] = token
            page = self._get("/messages", params)
            ids += [m["id"] for m in page.get("messages", []) if _clean(m.get("id"))]
            token = str(page.get("nextPageToken") or "")
            if not token:
                break
        return baseline, list(reversed(ids[:BACKLOG_CAP]))

    def _added_since(self, folder: str, history: str) -> tuple[list[str], str]:
        found: list[str] = []
        token = ""
        while True:
            params: dict[str, str | int] = {"startHistoryId": history, "historyTypes": "messageAdded",
                                            "labelId": LABELS[folder], "maxResults": 500}  # fmt: skip
            if token:
                params["pageToken"] = token
            page = self._get("/history", params, missing_ok=True)
            for entry in page.get("history", []):
                found += [a["message"]["id"] for a in entry.get("messagesAdded", []) if _clean(a["message"].get("id"))]
            token = str(page.get("nextPageToken") or "")
            if not token:
                return found, str(page.get("historyId") or history)

    def _raw(self, message_id: str) -> bytes:
        body = self._get(f"/messages/{quote(message_id, safe='')}", {"format": "raw"}, missing_ok=True)
        try:
            return base64.urlsafe_b64decode(str(body["raw"]) + "=" * (-len(str(body["raw"])) % 4))
        except (KeyError, binascii.Error, ValueError):
            raise GmailError("Gmail sent a message that could not be read.") from None


class _NotFound(Exception):
    """Google answered 404: the thing asked for is gone (a message, or a history id it no longer keeps)."""


def _clean(value: object) -> bool:
    return isinstance(value, str) and ID.fullmatch(value) is not None


def _read_position(position: str | None) -> tuple[str | None, list[str]]:
    try:
        value = json.loads(position or "")
    except ValueError:
        return None, []
    if not isinstance(value, dict) or not isinstance(value.get("h"), str) or not value["h"].isdigit():
        return None, []
    queued = value.get("q")
    if not isinstance(queued, list) or not all(_clean(i) for i in queued):
        return None, []
    return value["h"], list(queued)


def _merge(queued: list[str], found: list[str]) -> list[str]:
    seen = set(queued)
    return queued + [i for i in dict.fromkeys(found) if i not in seen]
