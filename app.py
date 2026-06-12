"""RhymePad — the web app. The rhyme engine itself lives in ``rhymes``
(framework-free); this module is the FastAPI shell around it: routes,
shared-draft link previews, and static serving.

Run it:  uv run uvicorn app:app --reload
"""

import base64
import gzip
import io
import json
import re
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import rhymes
from rhymes import (lookup_data, multis_for,  # noqa: F401  (test surface)
                    rhyme_char_start, word_data)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm the slow lazy bits at boot, not on the first keystroke
    rhymes.warm()
    yield


app = FastAPI(title="RhymePad", lifespan=lifespan)


class Draft(BaseModel):
    text: str


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/analyze")
def analyze(draft: Draft):
    try:
        return rhymes.analyze_text(draft.text)
    except ValueError:
        raise HTTPException(413, "draft too large")


@app.get("/api/word")
def word_info(word: str):
    """Phonetic anatomy of a word — or a phrase, read straight through."""
    return rhymes.word_data(word)


@app.get("/api/lookup")
def lookup(word: str, mode: str = "rhyme", limit: int = 60):
    return rhymes.lookup_data(word, mode=mode, limit=limit)

OG_PALETTE = ["#e8814a", "#4ea3e8", "#6fd08c", "#d46fb8",
              "#e8c54a", "#9b7ce8", "#e85a5a", "#46cabf",
              "#c0d44e", "#ee5d8f", "#6f8bf2", "#8fe85a",
              "#5ad8d8", "#e0985a", "#b88ce8", "#56c878"]
OG_BG, OG_INK, OG_DIM = (20, 17, 15), (242, 233, 221), (122, 112, 98)
FONT_PATH = Path(__file__).parent / "static" / "fonts" / "SplineSansMono.ttf"


def _hex_rgb(s: str) -> tuple[int, int, int]:
    return tuple(int(s[i:i + 2], 16) for i in (1, 3, 5))


def _mix(a, b, t: float) -> tuple[int, int, int]:
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _decode_d(d: str) -> dict:
    if len(d) > 20000:
        raise ValueError("too long")
    pad = "=" * (-len(d) % 4)
    raw = gzip.decompress(base64.urlsafe_b64decode(d + pad))
    if len(raw) > 64_000:
        raise ValueError("too large")
    obj = json.loads(raw)
    if not isinstance(obj.get("x"), str) or not obj["x"].strip():
        raise ValueError("no text")
    return obj


@lru_cache(maxsize=256)
def _render_og(d: str) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    obj = _decode_d(d)
    text = obj["x"]
    title = (obj.get("t") or "").strip().lstrip("#").strip()
    if title == "Untitled":
        title = ""
    res = rhymes.analyze_text(text)
    lines = res["lines"]
    # the title is drawn big — drop a first header line that repeats it
    while lines and (not lines[0].strip()
                     or lines[0].strip().lstrip("#").strip() == title):
        lines = lines[1:]
        res = {**res,
               "tokens": [{**t, "l": t["l"] - 1} for t in res["tokens"] if t["l"] > 0],
               "open": [{**o, "l": o["l"] - 1} for o in res.get("open", []) if o["l"] > 0]}

    W, H, PAD = 1200, 630, 64
    img = Image.new("RGBA", (W, H), OG_BG + (255,))
    dr = ImageDraw.Draw(img, "RGBA")

    def fnt(size, weight=400):
        f = ImageFont.truetype(str(FONT_PATH), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f

    top = PAD
    if title:
        tf = fnt(38, 600)
        dr.text((PAD, top), title[:40], font=tf, fill=OG_INK)
        top += 66

    # pick a font size the longest visible line can live with
    bottom = H - 58
    fs = 32
    body = fnt(fs)
    line_h = int(fs * 1.85)
    max_lines = max(1, (bottom - top) // line_h)
    shown = lines[:max_lines]
    while fs > 19:
        body = fnt(fs)
        if max(dr.textlength(l, font=body) for l in shown) <= W - 2 * PAD:
            break
        fs -= 2
        line_h = int(fs * 1.85)
        max_lines = max(1, (bottom - top) // line_h)
        shown = lines[:max_lines]

    by_line: dict[int, list] = defaultdict(list)
    for t in res["tokens"]:
        by_line[t["l"]].append(t)
    open_by: dict[int, list] = defaultdict(list)
    for o in res.get("open", []):
        open_by[o["l"]].append(o)
    gcolor = {g["id"]: _hex_rgb(OG_PALETTE[g["color"] % len(OG_PALETTE)])
              for g in res["groups"]}

    xat = lambda line, c: PAD + dr.textlength(line[:c], font=body)
    hdr_f = fnt(fs, 700)
    for i, line in enumerate(shown):
        y = top + i * line_h
        toks = [t for t in by_line.get(i, []) if t["e"] <= len(line)]
        words = [t for t in toks if not t["ph"]]
        # fills first (phrases under words), strength-scaled like the app
        for t in [t for t in toks if t["ph"]] + words:
            base = (34 if t["end"] else 19) if not t["ph"] else (24 if t["end"] else 14)
            s = t.get("str", 1.0)
            alpha = round(base * (0.4 + 0.6 * s) * 2.1)
            x0, x1 = xat(line, t["s"]), xat(line, t["e"])
            dr.rounded_rectangle([x0 - 4, y - 5, x1 + 4, y + fs + 7],
                                 radius=7, fill=gcolor[t["g"]] + (alpha,))
        for o in open_by.get(i, []):
            if o["e"] > len(line):
                continue
            x0, x1 = xat(line, o["s"]), xat(line, o["e"])
            dr.rounded_rectangle([x0 - 4, y - 5, x1 + 4, y + fs + 7],
                                 radius=7, fill=OG_INK + (33,))
        # base text — headers bold-bright, annotations dim, lyric ink
        if line.lstrip().startswith("#"):
            dr.text((PAD, y), line, font=hdr_f, fill=(138, 125, 108))
        elif line.lstrip()[:1] in "[":
            dr.text((PAD, y), line, font=body, fill=(106, 95, 82))
        else:
            dr.text((PAD, y), line, font=body, fill=OG_INK)
            # tinted rhyming words over the ink, like the editor
            for t in words:
                s = t.get("str", 1.0)
                tint = _mix(OG_INK, gcolor[t["g"]], 0.28 + 0.32 * s)
                dr.text((xat(line, t["s"]), y), line[t["s"]:t["e"]],
                        font=body, fill=tint)
    if len(lines) > len(shown):
        dr.text((PAD, top + len(shown) * line_h), "\u2026",
                font=body, fill=OG_DIM)

    foot = fnt(20)
    label = "rhymepad.org"
    dr.text((W - PAD - dr.textlength(label, font=foot), H - 44),
            label, font=foot, fill=OG_DIM)

    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG")
    return out.getvalue()


@app.get("/api/og", include_in_schema=False)
def og_card(d: str) -> Response:
    try:
        png = _render_og(d)
    except Exception:
        raise HTTPException(404, "bad draft link")
    return Response(png, media_type="image/png",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


INDEX_HTML = (Path(__file__).parent / "static" / "index.html")


@app.get("/", include_in_schema=False)
def index(d: str | None = None) -> Response:
    html = INDEX_HTML.read_text()
    if d:
        try:
            obj = _decode_d(d)
            title = (obj.get("t") or "").strip().lstrip("#").strip()
            if not title or title == "Untitled":
                title = "RhymePad draft"
            first = [l.strip() for l in obj["x"].split("\n")
                     if l.strip() and not l.strip().startswith(("#", "["))]
            desc = " / ".join(first[:3])[:160]
            t_esc = escape(title)
            d_esc = escape(desc)
            qd = quote(d, safe="")
            ogimg = f"https://rhymepad.org/api/og?d={qd}"
            url = f"https://rhymepad.org/?d={qd}"
            html = html.replace(
                "<title>RhymePad — rhyme scheme analyzer &amp; writing pad for poets and rappers</title>",
                f"<title>{t_esc} · RhymePad</title>")
            # shared drafts are personal pages: keep them out of the index
            html = html.replace(
                '<meta name="robots" content="index, follow">',
                '<meta name="robots" content="noindex">')
            html = re.sub(r'(property="og:title" content=")[^"]*', r"\g<1>" + t_esc, html)
            html = re.sub(r'(name="twitter:title" content=")[^"]*', r"\g<1>" + t_esc, html)
            html = re.sub(r'(property="og:description" content=")[^"]*', r"\g<1>" + d_esc, html)
            html = re.sub(r'(name="twitter:description" content=")[^"]*', r"\g<1>" + d_esc, html)
            html = re.sub(r'(property="og:image" content=")[^"]*', r"\g<1>" + ogimg, html)
            html = re.sub(r'(name="twitter:image" content=")[^"]*', r"\g<1>" + ogimg, html)
            html = re.sub(r'(property="og:url" content=")[^"]*', r"\g<1>" + url, html)
        except Exception:
            pass
    return Response(html, media_type="text/html")


@app.get("/robots.txt", include_in_schema=False)
def robots() -> Response:
    body = ("User-agent: *\n"
            "Allow: /\n"
            "Sitemap: https://rhymepad.org/sitemap.xml\n")
    return Response(body, media_type="text/plain")


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap() -> Response:
    body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            '  <url><loc>https://rhymepad.org/</loc>'
            '<changefreq>weekly</changefreq><priority>1.0</priority></url>\n'
            '</urlset>\n')
    return Response(body, media_type="application/xml")


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")
