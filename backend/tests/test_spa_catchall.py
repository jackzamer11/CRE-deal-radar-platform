"""The SPA catch-all must never swallow /api/* requests.

Regression for the blank-page bug: when a frontend build (dist) exists, the
catch-all route is registered. A slash-mismatched API GET must 404 as JSON, not
return index.html (which crashes the frontend when it parses HTML as JSON).
"""

import os

from fastapi.testclient import TestClient


def test_unknown_api_path_404s_as_json_when_build_present(tmp_path, monkeypatch):
    # Fake a built frontend so the catch-all + /assets mount are registered.
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><html><body>SPA</body></html>")

    import app.main as main
    monkeypatch.setattr(main, "FRONTEND_DIST", str(dist))
    client = TestClient(main.create_app())

    # A non-API route serves the SPA shell...
    spa = client.get("/some/react/route")
    assert spa.status_code == 200
    assert "<!doctype html>" in spa.text.lower()

    # ...but an unknown /api/* path must 404, and must NOT be HTML.
    resp = client.get("/api/does-not-exist")
    assert resp.status_code == 404
    assert "html" not in resp.headers.get("content-type", "")
    assert resp.json()["detail"] == "Not Found"
