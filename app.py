"""RhymePad — phoneme-aware rhyme analysis for poets & rappers.

Uses the CMU Pronouncing Dictionary (via ``pronouncing``) to detect
perfect rhymes, internal rhymes, and slant (vowel) rhymes — no
spelling heuristics.

Run it:  uv run uvicorn app:app --reload
"""

import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pronouncing
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from wordfreq import zipf_frequency

app = FastAPI(title="RhymePad")

_g2p = None


def g2p_phones(word: str) -> str | None:
    """Neural grapheme-to-phoneme fallback for words the CMU dict and our
    heuristics can't resolve — pronounces anything (lazily loaded)."""
    global _g2p
    if _g2p is None:
        from g2p_en import G2p
        _g2p = G2p()
    phones = [p for p in _g2p(word) if re.fullmatch(r"[A-Z]+[012]?", p)]
    return " ".join(phones) or None

WORD_RE = re.compile(r"[A-Za-z][A-Za-z']*")
DIGITS = re.compile(r"\d")
COLORS = 12  # matches --r0..--r11 in the stylesheet

#: function words too common to flag as internal rhymes
#: (they still count when they end a line)
STOPWORDS = frozenset(
    """i a an the and or but as at of in on is it its was were are am be been
    do does did to too so no not nor that this these those with for from has
    had have what when then than there here you your we he she they them his
    her our us if by my em im 's t s d ll re ve
    i'm i'll i'd i've it's that's you're we're they're he's she's""".split()
)


# --------------------------------------------------------------------------
# pronunciation
# --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def phones_candidates(word: str) -> tuple[str, ...]:
    """Plausible pronunciations for a word: CMU dict variants, a few
    lyric-friendly repairs (droppin' the g, possessives, wheee, whisps),
    and for unknown words BOTH the g2p model's guess and a compound
    split (heresay = here + say) — we can't know which the writer means,
    so the rhyme passes get to match on any of them."""
    w = word.lower().strip("'")
    cands = list(pronouncing.phones_for_word(w)[:3])
    if not cands and w.endswith("in"):  # runnin' -> running
        cands = pronouncing.phones_for_word(w + "g")[:1]
    if not cands and w.endswith("'s"):
        base = pronouncing.phones_for_word(w[:-2])
        if base:
            cands = [base[0] + " Z"]
    if not cands and re.search(r"(.)\1\1", w):  # wheee -> whee
        for collapsed in (re.sub(r"(.)\1{2,}", r"\1\1", w),
                          re.sub(r"(.)\1{2,}", r"\1", w)):
            opts = pronouncing.phones_for_word(collapsed)
            if opts:
                cands = opts[:1]
                break
    if not cands and w.startswith("wh"):  # whisps -> wisps
        cands = pronouncing.phones_for_word("w" + w[2:])[:1]
    if not cands and w.endswith("s") and len(w) > 3:
        base = pronouncing.phones_for_word(w[:-1])  # plural of an OOV stem
        if base:
            voiced = base[0].split()[-1] not in {"P", "T", "K", "F", "TH"}
            cands = [base[0] + (" Z" if voiced else " S")]
    if not cands:
        if re.fullmatch(r"[a-z']+", w):
            try:
                g = g2p_phones(w)
                if g:
                    cands.append(g)
            except Exception:
                pass  # g2p model unavailable
        if len(w) >= 6:
            # creative compounds & misspellings: heresay -> here + say
            for i in range(3, len(w) - 2):
                a = pronouncing.phones_for_word(w[:i])
                b = pronouncing.phones_for_word(w[i:])
                if a and b:
                    cands.append(a[0] + " " + b[0])
                    break
    return tuple(dict.fromkeys(cands))


def phones_for(word: str) -> str | None:
    """Primary pronunciation (used for slant/consonance keys)."""
    cands = phones_candidates(word)
    return cands[0] if cands else None


def _rime_from_phones(phones: str) -> str:
    """Everything from the last stressed vowel on, stress markers stripped.
    This is the classic 'perfect rhyme' key: light/night/tonight all share AY T."""
    return DIGITS.sub("", pronouncing.rhyming_part(phones))


def _tail_vowels(phones: str) -> list[str]:
    """Vowel sounds from the last stressed vowel on, stress-stripped."""
    pl = phones.split()
    start = 0
    for i in range(len(pl) - 1, -1, -1):
        if pl[i][-1] in "12":
            start = i
            break
    return [DIGITS.sub("", p) for p in pl[start:] if p[-1].isdigit()]


def _all_vowels(phones: str) -> list[str]:
    return [DIGITS.sub("", p) for p in phones.split() if p[-1].isdigit()]


def _slant_from_phones(phones: str) -> str | None:
    """Just the vowel sounds from the last stressed vowel on — the assonance
    key used for slant rhymes (time/light, hold/coal)."""
    return " ".join(_tail_vowels(phones)) or None


#: the schwa family — reduced vowels rappers treat as interchangeable
#: (orange = AO R *AH* N JH, door hinge = AO R HH *IH* N JH)
REDUCED = {"AH", "IH", "UH", "ER"}

ARPA_VOWELS = {"AA", "AE", "AH", "AO", "AW", "AY", "EH", "ER", "EY",
               "IH", "IY", "OW", "OY", "UH", "UW"}


def founding_projections(key: str) -> dict[str, str]:
    """Project a group's FOUNDING key (the sound that formed it) into the
    weaker key spaces, so later passes only attach members that match the
    sound the group is actually about — never some member's other
    pronunciation (that's how what's/peanuts once swallowed sleep/people)."""
    out = {}
    if key.startswith("p:"):  # perfect rime, e.g. "AH T S"
        ph = key[2:].split()
        vowels = [p for p in ph if p in ARPA_VOWELS]
        if vowels:
            out["slant"] = "v:" + " ".join(vowels)
            if len(ph) > 1 and ph[1] not in ARPA_VOWELS:
                out["vc"] = "c:" + ph[0] + " " + ph[1]
            else:
                out["vc"] = "c:" + ph[0]
            mk = _multi_key(vowels)
            if mk:
                out["multi"] = mk
    elif key.startswith("v:"):  # vowel tail
        out["slant"] = key
        mk = _multi_key(key[2:].split())
        if mk:
            out["multi"] = mk
    elif key.startswith("m:"):
        out["multi"] = key
    elif key.startswith("c:"):
        out["vc"] = key
    return out


def _multi_key(vowels: list[str]) -> str | None:
    """Key for multisyllabic slant rhymes: a 2+ vowel run where the first
    vowel must match exactly and later reduced vowels are merged."""
    if len(vowels) < 2:
        return None
    return "m:" + " ".join(
        [vowels[0]] + ["x" if v in REDUCED else v for v in vowels[1:]])


def _grapheme_tail(raw: str) -> str | None:
    """Spelling-based fallback key for words the CMU dict doesn't know,
    so made-up words can still rhyme with each other."""
    w = re.sub(r"[^a-z']", "", raw.lower())
    if not w:
        return None
    for pat, rep in (
        (r"ies$", "ee"), (r"ied$", "ide"), (r"igh", "i"),
        (r"[ts]ion$", "shun"), (r"ph", "f"), (r"ck", "k"), (r"qu", "kw"),
    ):
        w = re.sub(pat, rep, w)
    if re.search(r"[^aeiou]e$", w) and len(w) > 2:
        w = w[:-1]
    m = re.search(r"([aeiouy]+[^aeiouy]*)$", w)
    tail = m.group(1) if m else w
    tail = re.sub(r"(.)\1+", r"\1", tail)
    for pat, rep in (
        (r"^[ae]y", "ai"), (r"^ei", "ai"), (r"^oa", "o"), (r"^ow$", "o"),
        (r"^oe", "o"), (r"^ea", "ee"), (r"^ie", "ee"), (r"^oo", "u"),
        (r"^ou", "ow"), (r"^ew", "u"),
    ):
        tail = re.sub(pat, rep, tail)
    return tail


def rime_keys(word: str) -> tuple[str, ...]:
    """Perfect-rhyme keys, one per candidate pronunciation."""
    cands = phones_candidates(word)
    if cands:
        return tuple(dict.fromkeys("p:" + _rime_from_phones(p) for p in cands))
    tail = _grapheme_tail(word)
    return ("g:" + tail,) if tail else ()


def vc_key(word: str) -> str | None:
    """Last stressed vowel + first coda consonant — a consonance-aware
    slant key, so bliss / whisps / exist all share IH S."""
    phones = phones_for(word)
    if not phones:
        return None
    pl = phones.split()
    start = 0
    for i in range(len(pl) - 1, -1, -1):
        if pl[i][-1] in "12":
            start = i
            break
    tail = pl[start:]
    if not tail or not tail[0][-1].isdigit():
        return None
    key = DIGITS.sub("", tail[0])
    if len(tail) > 1:
        key += " " + tail[1]
    return "c:" + key


def slant_key(word: str) -> str | None:
    phones = phones_for(word)
    if phones:
        v = _slant_from_phones(phones)
        return ("v:" + v) if v else None
    tail = _grapheme_tail(word)
    if not tail:
        return None
    m = re.match(r"[aeiouy]+", tail)
    return "gv:" + (m.group(0) if m else tail)


# --------------------------------------------------------------------------
# meter
# --------------------------------------------------------------------------

FEET = {
    "iambic": "01", "trochaic": "10", "anapestic": "001",
    "dactylic": "100", "amphibrachic": "010",
}
METER_NAMES = {1: "monometer", 2: "dimeter", 3: "trimeter", 4: "tetrameter",
               5: "pentameter", 6: "hexameter", 7: "heptameter", 8: "octameter"}


def line_meter(line: str) -> dict | None:
    """Syllable count and best-fit metrical foot for a line.

    Stress comes from the CMU markers (1/2 stressed, 0 unstressed).
    Monosyllables flex in real speech, so function words read as
    unstressed and content monosyllables as wildcards.
    """
    stress = ""
    for w in WORD_RE.findall(line):
        ph = phones_for(w)
        if not ph:
            stress += "x"  # unknown word: one flexible syllable, at least
            continue
        syls = [p[-1] for p in ph.split() if p[-1].isdigit()]
        if len(syls) == 1:
            stress += "0" if w.lower() in STOPWORDS else "x"
        else:
            stress += "".join("1" if s in "12" else "0" for s in syls)
    n = len(stress)
    if n == 0:
        return None
    best_label, best_score = None, 0.0
    if n >= 4:  # too short to call a meter
        for name, foot in FEET.items():
            pat = (foot * (n // len(foot) + 1))[:n]  # final foot may truncate
            score = sum(a == "x" or a == b for a, b in zip(stress, pat)) / n
            if score > best_score:
                feet_count = round(n / len(foot))
                meter = METER_NAMES.get(feet_count, f"{feet_count}-foot")
                best_label, best_score = f"{name} {meter}", score
    return {
        "syl": n,
        "stress": stress,
        "label": best_label if best_score >= 0.75 else None,
        "score": round(best_score, 2),
    }


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------

class Draft(BaseModel):
    text: str


@app.post("/api/analyze")
def analyze(draft: Draft):
    lines = draft.text.split("\n")

    # stanza ids (blank-line separated)
    sids: list[int | None] = []
    sid, prev_blank = -1, True
    for line in lines:
        if not line.strip():
            sids.append(None)
            prev_blank = True
            continue
        if prev_blank:
            sid += 1
        prev_blank = False
        sids.append(sid)

    tokens = []
    for i, line in enumerate(lines):
        if sids[i] is None:
            continue
        matches = list(WORD_RE.finditer(line))
        for j, m in enumerate(matches):
            tokens.append({
                "line": i, "start": m.start(), "end": m.end(),
                "word": m.group(0), "is_end": j == len(matches) - 1,
                "sid": sids[i], "gid": None, "slant": False,
            })

    # phrase tokens: adjacent word pairs, so multi-word rhymes can match
    # single words (orange / door hinge). Anchored at the first word's
    # stressed vowel; competes in the multisyllabic slant pass only.
    phrases = []
    line_toks = defaultdict(list)
    for t in tokens:
        line_toks[t["line"]].append(t)
    for toks in line_toks.values():
        for a, b in zip(toks, toks[1:]):
            if a["word"].lower() in STOPWORDS or b["word"].lower() in STOPWORDS:
                continue
            pa, pb = phones_for(a["word"]), phones_for(b["word"])
            if not (pa and pb):
                continue
            phrases.append({
                "line": a["line"], "start": a["start"], "end": b["end"],
                "word": a["word"].lower() + " " + b["word"].lower(),
                "is_end": b["is_end"], "sid": a["sid"], "gid": None,
                "slant": False, "vowels": _tail_vowels(pa) + _all_vowels(pb),
            })

    # pass 1: perfect rhymes (shared rime), anywhere in a line — this is
    # what catches internal rhymes
    by_rime = defaultdict(list)
    for t in tokens:
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or len(w) < 2):
            continue
        for key in rime_keys(t["word"]):
            by_rime[key].append(t)

    # biggest buckets claim their tokens first, so a word with several
    # candidate pronunciations joins its best-supported rhyme group.
    # Each group remembers its founding key — the sound it's about.
    raw_groups: list[dict] = []
    claimed: set[int] = set()
    for key, toks in sorted(by_rime.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        toks = [t for t in toks if id(t) not in claimed]
        if len(toks) < 2:
            continue
        distinct = {t["word"].lower() for t in toks}
        end_count = sum(t["is_end"] for t in toks)
        if len(distinct) < 2 and end_count < 2:
            continue  # the same word repeated mid-line isn't a rhyme
        raw_groups.append({"toks": toks, "slant": False, "key": key})
        claimed.update(id(t) for t in toks)

    grouped = {id(t) for g in raw_groups for t in g["toks"]}

    def gmap_for(kind: str) -> dict[tuple, int]:
        """(sid, projected founding key) -> group index, for attaching."""
        out: dict[tuple, int] = {}
        for gi, g in enumerate(raw_groups):
            k = founding_projections(g["key"]).get(kind)
            if k:
                for s in {t["sid"] for t in g["toks"]}:
                    out.setdefault((s, k), gi)
        return out

    def attach_or_collect(t, key, bucket, gmap):
        gi = gmap.get((t["sid"], key))
        if gi is not None:
            raw_groups[gi]["toks"].append(t)
            t["slant"] = True
            grouped.add(id(t))
        else:
            bucket[(t["sid"], key)].append(t)

    # pass 2: slant rhymes for still-unmatched line endings, per stanza.
    # A leftover ending first tries to JOIN an existing group whose
    # founding sound shares its vowel tail (time -> the mind/find group);
    # otherwise leftovers form a new slant group among themselves.
    group_by_slant = gmap_for("slant")
    by_slant = defaultdict(list)
    for t in tokens:
        if t["is_end"] and id(t) not in grouped:
            key = slant_key(t["word"])
            if key:
                attach_or_collect(t, key, by_slant, group_by_slant)
    for (sid, key), toks in by_slant.items():
        if len(toks) >= 2 and len({t["word"].lower() for t in toks}) >= 2:
            raw_groups.append({"toks": toks, "slant": True, "key": key})
            grouped.update(id(t) for t in toks)

    # pass 3: multisyllabic slant rhymes anywhere in a line, per stanza —
    # tokens sharing a 2+ vowel run from the stressed syllable on
    # (placement / creation both carry EY AH; orange / door hinge both
    # carry AO + schwa). Single-vowel assonance is too noisy to flag
    # mid-line, so it stays end-of-line only (pass 2).
    group_by_multi = gmap_for("multi")
    by_multi = defaultdict(list)
    for t in tokens:
        if id(t) in grouped:
            continue
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or len(w) < 2):
            continue
        ph = phones_for(t["word"])
        key = _multi_key(_tail_vowels(ph)) if ph else None
        if key:
            attach_or_collect(t, key, by_multi, group_by_multi)

    # a phrase only competes when no word inside it already rhymes
    grouped_spans = defaultdict(list)
    for t in tokens:
        if id(t) in grouped:
            grouped_spans[t["line"]].append((t["start"], t["end"]))
    for p in phrases:
        if any(s < p["end"] and p["start"] < e
               for s, e in grouped_spans[p["line"]]):
            continue
        key = _multi_key(p["vowels"])
        if key:
            attach_or_collect(p, key, by_multi, group_by_multi)

    for (sid, key), toks in by_multi.items():
        if len(toks) >= 2 and len({t["word"].lower() for t in toks}) >= 2:
            raw_groups.append({"toks": toks, "slant": True, "key": key})
            grouped.update(id(t) for t in toks)

    # pass 4: consonance-aware slant anywhere in a line — last stressed
    # vowel + first coda consonant, so bliss / whisps / exist (IH S) group
    # even though their full codas differ
    group_by_vc = gmap_for("vc")
    # a word inside an already-grouped phrase sits this pass out,
    # so "door" doesn't fight "door hinge" for the highlight
    phrase_spans = defaultdict(list)
    for p in phrases:
        if id(p) in grouped:
            phrase_spans[p["line"]].append((p["start"], p["end"]))
    by_vc = defaultdict(list)
    for t in tokens:
        if id(t) in grouped:
            continue
        if any(s < t["end"] and t["start"] < e
               for s, e in phrase_spans[t["line"]]):
            continue
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or len(w) < 2):
            continue
        key = vc_key(t["word"])
        if key:
            attach_or_collect(t, key, by_vc, group_by_vc)
    for (sid, key), toks in by_vc.items():
        if len(toks) >= 2 and len({t["word"].lower() for t in toks}) >= 2:
            raw_groups.append({"toks": toks, "slant": True, "key": key})

    # stable colors: order groups by first appearance
    raw_groups.sort(key=lambda g: min((t["line"], t["start"]) for t in g["toks"]))
    groups_out = []
    for gid, g in enumerate(raw_groups):
        for t in g["toks"]:
            t["gid"] = gid
        groups_out.append({"id": gid, "color": gid % COLORS, "slant": g["slant"]})

    # stanza rhyme schemes from line-ending groups
    # (a grouped end word wins over a grouped end phrase)
    end_gid: dict[int, int] = {}
    for t in [*tokens, *phrases]:
        if t["is_end"] and t["gid"] is not None:
            end_gid.setdefault(t["line"], t["gid"])
    stanza_lines = defaultdict(list)
    for i, s in enumerate(sids):
        if s is not None:
            stanza_lines[s].append(i)

    stanzas = []
    for s in sorted(stanza_lines):
        letters, order = [], {}
        for i in stanza_lines[s]:
            gid = end_gid.get(i)
            key = gid if gid is not None else f"solo:{i}"
            if key not in order:
                order[key] = len(order)
            letters.append(order[key])
        legend, seen = [], set()
        for pos, i in enumerate(stanza_lines[s]):
            n = letters[pos]
            if n in seen:
                continue
            seen.add(n)
            gid = end_gid.get(i)
            legend.append({
                "ch": chr(97 + n % 26),
                "color": (gid % COLORS) if gid is not None else None,
                "slant": groups_out[gid]["slant"] if gid is not None else False,
            })
        stanzas.append({
            "lines": stanza_lines[s],
            "scheme": "".join(chr(97 + n % 26) for n in letters),
            "legend": legend,
        })

    toks_out = [
        {"l": t["line"], "s": t["start"], "e": t["end"], "g": t["gid"],
         "end": t["is_end"],
         "slant": t["slant"] or groups_out[t["gid"]]["slant"]}
        for t in [*tokens, *phrases] if t["gid"] is not None
    ]
    meter = []
    for i, line in enumerate(lines):
        if sids[i] is None:
            continue
        m = line_meter(line)
        if m:
            meter.append({"l": i, **m})

    return {"lines": lines, "tokens": toks_out,
            "groups": groups_out, "stanzas": stanzas, "meter": meter}


# --------------------------------------------------------------------------
# rhyme / near-rhyme lookup
# --------------------------------------------------------------------------

_slant_index: dict[str, set[str]] | None = None


def get_slant_index() -> dict[str, set[str]]:
    """vowel-tail -> words, over the whole CMU dict (built once, lazily)."""
    global _slant_index
    if _slant_index is None:
        pronouncing.init_cmu()
        idx: dict[str, set[str]] = defaultdict(set)
        for w, phones in pronouncing.pronunciations:
            k = _slant_from_phones(phones)
            if k:
                idx[k].add(w)
        _slant_index = idx
    return _slant_index


def _ranked(words, exclude: set[str], limit: int) -> list[dict]:
    scored = []
    for w in set(words):
        if w in exclude or not w.isalpha():
            continue
        z = zipf_frequency(w, "en")
        if z < 1.8:  # drop cmudict junk and very rare proper nouns
            continue
        scored.append((z, w))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out = []
    for z, w in scored[:limit]:
        ph = phones_for(w)
        out.append({"word": w, "syl": pronouncing.syllable_count(ph) if ph else 0})
    return out


@app.get("/api/lookup")
def lookup(word: str, mode: str = "rhyme", limit: int = 60):
    w = word.strip().lower()
    phones = phones_for(w)
    if not phones:
        return {"word": w, "mode": mode, "known": False, "words": []}
    if mode == "near":
        k = _slant_from_phones(phones)
        perfect = set(pronouncing.rhymes(w))
        cands = (get_slant_index().get(k, set()) - perfect) if k else set()
        words = _ranked(cands, {w}, limit)
    else:
        words = _ranked(pronouncing.rhymes(w), {w}, limit)
    return {"word": w, "mode": mode, "known": True, "words": words}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")
