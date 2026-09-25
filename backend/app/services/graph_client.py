"""Fetching an email's attachment BYTES from Microsoft Graph.

This module exists because of a specific dead end. The Outlook MCP connector
can find an email and list what came with it, but reading an attachment through
it returns a PDF as extracted text and a JPEG as a rendered picture — never the
original file. Writing either of those to disk would produce a corrupt document
that the interface would happily offer as a working link, which is worse than
saying the file is missing. Graph's `/$value` endpoint returns the real bytes,
so the backfill and the nightly task both come through here.

App-only (client credentials), so there is no signed-in user and no `/me`:
every call names `settings.GRAPH_MAILBOX` explicitly. That is also why the app
registration needs the APPLICATION permission `Mail.Read` with admin consent —
a delegated grant authenticates fine and then 403s on every message, which is
the most likely thing to go wrong on first setup.

Nothing here raises a bare exception at a route. Failures come back as
GraphError with a sentence a caller can show, because an attachment that cannot
be fetched must cost the attachment, never the entry it belongs to.
"""
import re
import threading
import time
from typing import List, Optional, Tuple

import httpx

from app.config import settings

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
_LOGIN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
_SCOPE = "https://graph.microsoft.com/.default"

# An internet message id, as the database stores it: "<abc@host>". Distinguished
# from a Graph id because the two need different lookups and the caller should
# not have to care which it is holding.
_INTERNET_ID = re.compile(r"^<[^>]+>$")

# Multi-deal entries carry a "#d2" suffix on the id so each split entry dedups
# separately. The provider never saw that suffix, so it is stripped before any
# lookup — see routes/activity.py, which writes it.
_DEAL_SUFFIX = re.compile(r"#d\d+$")

_token_lock = threading.Lock()
_token_cache = {"value": None, "expires_at": 0.0}


class GraphError(RuntimeError):
    """Something went wrong talking to Graph, with a sentence worth showing."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def is_configured() -> bool:
    """True when all four settings are present. Read at call time."""
    return all([
        settings.GRAPH_CLIENT_ID,
        settings.GRAPH_TENANT_ID,
        settings.GRAPH_CLIENT_SECRET,
        settings.GRAPH_MAILBOX,
    ])


def missing_settings() -> List[str]:
    """Which of the four are absent, so the error can name them."""
    names = (
        "GRAPH_CLIENT_ID", "GRAPH_TENANT_ID",
        "GRAPH_CLIENT_SECRET", "GRAPH_MAILBOX",
    )
    return [n for n in names if not getattr(settings, n, None)]


def normalize_message_id(raw: Optional[str]) -> str:
    """Strip the multi-deal "#dN" suffix the entry id carries.

    The suffix is Deal Radar's, not the provider's: looking a message up with
    it attached would never match anything.
    """
    return _DEAL_SUFFIX.sub("", (raw or "").strip())


def _access_token(force: bool = False) -> str:
    """A client-credentials token, cached until shortly before it expires.

    Cached because a backfill fetches dozens of attachments and asking Entra
    for a fresh token each time is both slow and a good way to get throttled.
    The 60-second margin keeps a long fetch from running past expiry mid-call.
    """
    if not is_configured():
        raise GraphError(
            "Microsoft Graph is not configured; missing "
            + ", ".join(missing_settings())
            + ". Add them to backend/.env (see .env.example).",
            status_code=503,
        )

    with _token_lock:
        now = time.time()
        if not force and _token_cache["value"] and _token_cache["expires_at"] > now + 60:
            return _token_cache["value"]

        url = _LOGIN.format(tenant=settings.GRAPH_TENANT_ID)
        try:
            response = httpx.post(
                url,
                data={
                    "client_id": settings.GRAPH_CLIENT_ID,
                    "client_secret": settings.GRAPH_CLIENT_SECRET,
                    "scope": _SCOPE,
                    "grant_type": "client_credentials",
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise GraphError(f"Could not reach Microsoft login: {exc}", 502)

        if response.status_code != 200:
            # Entra's own description is the useful part here — it names an
            # unapproved app or a bad secret precisely.
            detail = ""
            try:
                body = response.json()
                detail = body.get("error_description") or body.get("error") or ""
            except ValueError:
                detail = response.text[:300]
            raise GraphError(
                f"Microsoft rejected the credentials ({response.status_code}): "
                f"{str(detail)[:400]}",
                status_code=401,
            )

        body = response.json()
        token = body.get("access_token")
        if not token:
            raise GraphError("Microsoft returned no access token.", 502)
        _token_cache["value"] = token
        _token_cache["expires_at"] = now + float(body.get("expires_in", 3600))
        return token


def _get(path: str, *, params: Optional[dict] = None, raw: bool = False):
    """One authenticated GET against Graph, retrying once on a stale token."""
    for attempt in (0, 1):
        token = _access_token(force=(attempt == 1))
        try:
            response = httpx.get(
                f"{GRAPH_ROOT}{path}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                timeout=120.0,     # a large attachment is a slow download
                follow_redirects=True,
            )
        except httpx.HTTPError as exc:
            raise GraphError(f"Could not reach Microsoft Graph: {exc}", 502)

        # A token rejected once is worth one retry with a fresh one; twice is a
        # permissions problem, not a stale token.
        if response.status_code == 401 and attempt == 0:
            continue

        if response.status_code == 403:
            raise GraphError(
                "Graph refused access to the mailbox (403). The app "
                "registration most likely has DELEGATED Mail.Read rather than "
                "the APPLICATION permission, or admin consent has not been "
                "granted.",
                status_code=403,
            )
        if response.status_code == 404:
            raise GraphError("Not found in the mailbox.", 404)
        if response.status_code >= 400:
            raise GraphError(
                f"Graph returned {response.status_code}: {response.text[:300]}",
                status_code=502,
            )
        return response.content if raw else response.json()

    raise GraphError("Graph authentication failed twice.", 401)


def resolve_message_id(message_id: str) -> str:
    """Return a Graph message id, looking one up from an internet id if needed.

    The database stores the internet message id ("<abc@host>") because that is
    what the provider puts on the mail and what dedup matches on. Graph needs
    its own opaque id, so an internet id is resolved through a $filter first.
    A value that is already a Graph id is passed straight through.
    """
    clean = normalize_message_id(message_id)
    if not clean:
        raise GraphError("No message id was given.", 400)
    if not _INTERNET_ID.match(clean):
        return clean

    mailbox = settings.GRAPH_MAILBOX
    # The value is quoted into an OData string, so an embedded quote has to be
    # doubled or the filter is malformed.
    quoted = clean.replace("'", "''")
    body = _get(
        f"/users/{mailbox}/messages",
        params={"$filter": f"internetMessageId eq '{quoted}'", "$select": "id", "$top": "1"},
    )
    values = body.get("value") or []
    if not values:
        raise GraphError(f"No message in the mailbox has id {clean}.", 404)
    return values[0]["id"]


def list_attachments(message_id: str) -> Tuple[str, List[dict]]:
    """Every attachment on a message: (graph_message_id, [metadata, ...]).

    Metadata only — name, contentType, size, isInline, id. The bytes are a
    second call per attachment, because a message with a 40MB video on it
    should not be downloaded just to find out the name.
    """
    graph_id = resolve_message_id(message_id)
    mailbox = settings.GRAPH_MAILBOX
    body = _get(
        f"/users/{mailbox}/messages/{graph_id}/attachments",
        params={"$select": "id,name,contentType,size,isInline"},
    )
    items = []
    for item in body.get("value") or []:
        items.append({
            "id": item.get("id"),
            "name": item.get("name") or "",
            "content_type": item.get("contentType"),
            "size": int(item.get("size") or 0),
            "inline": bool(item.get("isInline")),
            # itemAttachment/referenceAttachment have no downloadable bytes.
            "odata_type": item.get("@odata.type", ""),
        })
    return graph_id, items


def fetch_attachment_bytes(graph_message_id: str, attachment_id: str) -> bytes:
    """The attachment's real bytes, via /$value."""
    mailbox = settings.GRAPH_MAILBOX
    return _get(
        f"/users/{mailbox}/messages/{graph_message_id}/attachments/{attachment_id}/$value",
        raw=True,
    )


def reset_token_cache() -> None:
    """Drop the cached token. For tests, and for a credential change."""
    with _token_lock:
        _token_cache["value"] = None
        _token_cache["expires_at"] = 0.0
