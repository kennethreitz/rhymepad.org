"""Thin HTTP regression tests for the Responder shell."""

from app import MAX_REQUEST_BODY, api


def test_query_validation_errors_use_problem_details():
    resp = api.requests.get("/api/lookup?word=light&limit=nope")

    assert resp.status_code == 422
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["status"] == 422
    assert resp.json()["title"] == "Validation Error"
    assert resp.json()["errors"][0]["loc"] == ["query", "limit"]


def test_responder7_response_headers_are_enabled():
    resp = api.requests.get("/robots.txt")

    assert resp.status_code == 200
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert resp.headers["x-request-id"]


def test_get_routes_emit_and_honor_etags():
    resp = api.requests.get("/robots.txt")
    etag = resp.headers["etag"]

    cached = api.requests.get("/robots.txt", headers={"if-none-match": etag})

    assert cached.status_code == 304
    assert cached.content == b""


def test_oversized_json_body_is_rejected_before_analyze():
    body = b'{"text":"' + b"a" * (MAX_REQUEST_BODY + 1) + b'"}'

    resp = api.requests.post(
        "/api/analyze",
        content=body,
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 413
    assert resp.headers["content-type"] == "application/problem+json"
    assert resp.json()["status"] == 413
