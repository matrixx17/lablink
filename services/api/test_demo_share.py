from __future__ import annotations

import base64
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402

from app import app  # noqa: E402


client = TestClient(app)


def test_demo_share_returns_public_url_and_qr_code():
    response = client.get("/demo/share", params={"domain": "compchem"})

    assert response.status_code == 200
    body = response.json()
    assert body["url"] == "https://lablink.io/demo?ref=shared&domain=compchem"
    assert body["expires"] == "never — link always works"
    assert base64.b64decode(body["qr_code"]).startswith(b"\x89PNG\r\n\x1a\n")


def test_demo_share_rejects_invalid_domain():
    response = client.get("/demo/share", params={"domain": "sales"})

    assert response.status_code == 422
