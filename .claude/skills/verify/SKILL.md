---
name: verify
description: Launch and drive rhymepad.org end-to-end to verify a change against the real HTTP surface.
---

# Verifying rhymepad.org

The surface is HTTP: a Responder app (`app:api`) served by granian.

## Launch

```bash
uv run granian --interface asgi --host 127.0.0.1 --port 8791 app:api
```

Run it in the background. Boot includes `rhymes.warm()` (g2p model +
NLTK data) via the lifespan hook — poll `/healthz` for up to ~2 min
before declaring it dead. NLTK data missing locally? `make nltk-data`.

## Flows worth driving

- `GET /healthz` → `{"ok": true}` — also carries the global headers
  (security headers, ETag), so it doubles as a header check.
- `POST /api/analyze` with `{"text": "cat in a hat\nbat on a mat"}` →
  tokens/groups JSON. Body > 512 KB → 413 (framework cap);
  `text` > `rhymes.MAX_DRAFT` (100k chars) → 413 with detail.
- `GET /api/lookup?word=cat&limit=5` — `limit` is a validated Query
  param: non-int or outside 1–500 → 422 problem-details JSON.
- `GET /api/og?d=<payload>` → PNG + immutable Cache-Control. Build a
  payload: `base64.urlsafe_b64encode(gzip.compress(json.dumps({"x":
  "..."}).encode()))`, strip `=`. Garbage `d` → 404 "bad draft link".
  Rate-limited 30/min per client IP (X-Forwarded-For when present) —
  31 rapid hits show the 429.
- `GET /?d=<payload>` → title swapped into `<title>… · RhymePad</title>`
  plus `noindex` robots meta.
- Conditional GET: take any ETag, re-request with `If-None-Match` → 304.

## Gotchas

- Shell is fish; multi-line loops in Bash tool calls need `bash -c`.
- The rate limiter's memory backend persists per-process — restarting
  granian resets budgets; repeated verify runs against one process eat
  the og budget (send a fresh `X-Forwarded-For` to get a clean one).
