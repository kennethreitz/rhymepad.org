"""The Responder shell: routes, validation, limits, headers.

The rhyme engine has its own suite; these tests cover the web layer —
what a client actually sees on the wire.
"""

import base64
import gzip
import json

import pytest

import rhymes
from app import api


@pytest.fixture(scope="module")
def client():
    return api.requests


def encode_draft(obj):
    """The share-link encoding the frontend uses: gzip → urlsafe b64."""
    raw = json.dumps(obj).encode()
    return base64.urlsafe_b64encode(gzip.compress(raw)).decode().rstrip("=")


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_analyze(client):
    r = client.post("/api/analyze", json={"text": "cat in a hat\nbat on a mat"})
    assert r.status_code == 200
    assert r.json()["groups"]


def test_analyze_draft_too_large(client):
    r = client.post("/api/analyze", json={"text": "x" * (rhymes.MAX_DRAFT + 1)})
    assert r.status_code == 413
    assert r.json()["detail"] == "draft too large"


def test_oversized_body_rejected(client):
    # framework-level cap, before any JSON parsing
    r = client.post("/api/analyze", content=b"x" * 600_000,
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_lookup(client):
    r = client.get("/api/lookup", params={"word": "cat", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["known"]
    assert len(body["words"]) <= 5


def test_lookup_bad_limit_is_422_not_500(client):
    assert client.get("/api/lookup?word=cat&limit=abc").status_code == 422
    assert client.get("/api/lookup?word=cat&limit=9999").status_code == 422
    assert client.get("/api/lookup?word=cat&limit=0").status_code == 422


def test_zipf(client):
    r = client.get("/api/zipf", params={"word": "The"})
    assert r.status_code == 200
    body = r.json()
    assert body["word"] == "the"
    assert body["zipf"] > 6


def test_follows(client):
    r = client.get("/api/follows", params={"prev": "time", "prev2": "from"})
    assert r.status_code == 200
    assert r.json()["prev"] == "time"


def test_suggest(client):
    r = client.post("/api/suggest", json={"word": "cat", "text": "hat\nbat"})
    assert r.status_code == 200
    assert r.json()


def test_og_card(client):
    d = encode_draft({"x": "cat in a hat\nbat on a mat", "t": "Test"})
    r = client.get("/api/og", params={"d": d})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert "immutable" in r.headers["cache-control"]
    assert r.content[:4] == b"\x89PNG"


def test_og_card_bad_link(client):
    assert client.get("/api/og", params={"d": "notavalidpayload"}).status_code == 404


def test_index_plain(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "RhymePad" in r.text
    assert 'content="index, follow"' in r.text


def test_index_shared_draft(client):
    d = encode_draft({"x": "cat in a hat", "t": "My Verse"})
    r = client.get("/", params={"d": d})
    assert r.status_code == 200
    assert "<title>My Verse · RhymePad</title>" in r.text
    assert 'content="noindex"' in r.text


def test_security_headers(client):
    r = client.get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "referrer-policy" in r.headers


def test_auto_etag_304(client):
    r = client.get("/robots.txt")
    etag = r.headers["etag"]
    r2 = client.get("/robots.txt", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_sitemap_content_type(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/xml"


def test_rate_limit_headers_on_expensive_routes(client):
    d = encode_draft({"x": "one lone bone"})
    r = client.get("/api/og", params={"d": d})
    assert "x-ratelimit-remaining" in r.headers
