"""Frontend/backend URL contract.

Every path `frontend/src/api/client.ts` calls must resolve to a real backend
route that accepts that HTTP method.

Why this needs a test: `main.py` registers a GET-only SPA catch-all
(`@app.get("/{full_path:path}")`) so the built frontend can deep-link. That
catch-all matches ANY path, so an /api URL with a typo — notably a stray
trailing slash — never reaches Starlette's redirect_slashes handling. It falls
through to the catch-all and comes back 405 Method Not Allowed instead of a
loud 404. Two real bugs shipped this way:

    api.patch(`/activity/${entryId}/notes/`)   -> 405, note never saved
    api.patch(`/activity/${entryId}/stage/`)   -> 405, stage never persisted

The stage one was invisible in the UI because the page applies an optimistic
update first, so the pill moved and then silently reverted on the next reload.

Inputs are the checked-in client.ts plus the in-process route table — no live
DB, no network.
"""
import os
import re

import pytest

from app.main import app

CLIENT_TS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "frontend", "src", "api", "client.ts",
)

# api.patch(`/activity/${id}/notes`, ...) / api.get('/activity/', ...)
_CALL_RE = re.compile(r"api\.(get|post|patch|delete|put)\(\s*[`'\"]([^`'\"]+)[`'\"]")

# The SPA catch-all matches everything; it must never be what satisfies an /api call.
_SPA_CATCHALL_PREFIX = "/{full_path"


def _client_calls():
    with open(CLIENT_TS, encoding="utf-8") as fh:
        src = fh.read()
    return _CALL_RE.findall(src)


def _api_routes():
    return [
        (r.path_regex, set(r.methods or []), getattr(r, "path", ""))
        for r in app.routes
        if hasattr(r, "path_regex") and hasattr(r, "methods")
    ]


def _resolves(method: str, url: str) -> bool:
    return any(
        rx.match(url) and method in methods and not path.startswith(_SPA_CATCHALL_PREFIX)
        for rx, methods, path in _api_routes()
    )


def test_client_ts_is_readable():
    """Guard the guard — a moved/renamed client.ts must fail loudly, not vacuously pass."""
    assert os.path.exists(CLIENT_TS), f"client.ts not found at {CLIENT_TS}"
    assert _client_calls(), "no api.<method>(...) calls parsed out of client.ts"


def test_every_frontend_call_resolves_to_a_real_route():
    unresolved = []
    for method, path in _client_calls():
        # Template params (`${entryId}`) stand in as a concrete id.
        url = "/api" + re.sub(r"\$\{[^}]+\}", "1", path)
        if not _resolves(method.upper(), url):
            unresolved.append(f"{method.upper()} {path} -> {url}")

    assert not unresolved, (
        "frontend calls that do not match any backend route (these return 405 via "
        "the SPA catch-all, not 404 — check for a stray trailing slash):\n  "
        + "\n  ".join(unresolved)
    )


@pytest.mark.parametrize("path", ["/api/activity/1/notes", "/api/activity/1/stage"])
def test_activity_patch_routes_have_no_trailing_slash(path):
    """The two paths that regressed, pinned explicitly."""
    assert _resolves("PATCH", path), f"{path} should accept PATCH"
    assert not _resolves("PATCH", path + "/"), (
        f"{path}/ must NOT resolve — if it does, this test's premise changed"
    )
