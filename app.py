"""RhymePad — phoneme-aware rhyme analysis for poets & rappers.

Uses the CMU Pronouncing Dictionary (via ``pronouncing``) to detect
perfect rhymes, internal rhymes, and slant (vowel) rhymes — no
spelling heuristics.

Run it:  uv run uvicorn app:app --reload
"""

import re
from collections import Counter, defaultdict
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

import pronouncing
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from wordfreq import zipf_frequency


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm the slow lazy bits at boot, not on the first keystroke
    try:
        g2p_phones("warmup")
    except Exception:
        pass  # model unavailable; spelling fallbacks still work
    try:
        get_wordnet()
    except Exception:
        pass
    get_slant_index()
    get_multi_indexes()
    yield


app = FastAPI(title="RhymePad", lifespan=lifespan)

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
COLORS = 16  # matches --r0..--r15 in the stylesheet

#: function words too common to flag as internal rhymes
#: (they still count when they end a line)
STOPWORDS = frozenset(
    """i a an the and or but as at of in on is it its was were are am be been
    do does did to too so no not nor that this these those with for from has
    had have what when then than there you your we he she they them his
    her our us if by my em im 's t s d ll re ve
    i'm i'll i'd i've it's that's you're we're they're he's she's
    just like gonna wanna cause don't won't ain't yeah even me""".split()
)


# --------------------------------------------------------------------------
# pronunciation
# --------------------------------------------------------------------------

def _norm_r(phones: str) -> str:
    """Neutralize contrasts most American English doesn't keep: IH/IY and
    UH/UW merge before R (fear/hear, cure/tour — the NEAR vowel), and
    AO merges into AA everywhere else (the cot-caught merger: thought/
    lot, off/forgotten). Before R the AA/AO split survives (car/core)."""
    pl = phones.split()
    for i in range(len(pl)):
        if not pl[i][-1].isdigit():
            continue
        base, stress = pl[i][:-1], pl[i][-1]
        if i + 1 < len(pl) and pl[i + 1][0] == "R":
            if base == "IH":
                pl[i] = "IY" + stress
            elif base == "UH":
                pl[i] = "UW" + stress
        elif base == "AO":
            pl[i] = "AA" + stress
    return " ".join(pl)


#: corrections for the CMU dict's occasional howlers — the dict entry is
#: kept as a secondary candidate, the fix leads
OVERRIDES = {
    "stasis": "S T EY1 S IH0 S",  # CMU: "STAH-seez"
    "kinda": "K AY1 N D AH0",     # CMU: "KIH-nda"
}


@lru_cache(maxsize=None)
def phones_candidates(word: str) -> tuple[str, ...]:
    """Plausible pronunciations for a word: CMU dict variants, a few
    lyric-friendly repairs (droppin' the g, possessives, wheee, whisps),
    and for unknown words BOTH the g2p model's guess and a compound
    split (heresay = here + say) — we can't know which the writer means,
    so the rhyme passes get to match on any of them."""
    w = word.lower().strip("'")
    cands = list(pronouncing.phones_for_word(w)[:3])
    if w in OVERRIDES:
        cands.insert(0, OVERRIDES[w])
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
                if w.endswith("ine"):
                    # -ine is ambiguous (valentine vs ketamine); keep the
                    # "-een" reading as a candidate too
                    g = g2p_phones(w[:-3] + "een")
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
    return tuple(dict.fromkeys(_norm_r(c) for c in cands))


def phones_for(word: str) -> str | None:
    """Primary pronunciation (used for slant/consonance keys)."""
    cands = phones_candidates(word)
    return cands[0] if cands else None


def _rime_from_phones(phones: str) -> str:
    """Everything from the last stressed vowel on, stress markers stripped.
    This is the classic 'perfect rhyme' key: light/night/tonight all share
    AY T. Unstressed pronunciations (DH IH0 S) return their onset too —
    strip to the vowel so 'this' keys on IH S, not DH IH S."""
    ph = DIGITS.sub("", pronouncing.rhyming_part(phones)).split()
    while ph and ph[0] not in ARPA_VOWELS:
        ph.pop(0)
    return " ".join(ph)


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
                out["vc"] = "c:" + ph[0] + " " + _coda_class(ph[1])
            else:
                out["vc"] = "c:" + ph[0]
            mk = _multi_key(vowels)
            if mk:
                out["multi"] = mk
            mk2 = _m2_key(ph)
            if mk2:
                out["multi2"] = mk2
            # the founding rime's final syllable, for weak-ending joins
            for i in range(len(ph) - 1, -1, -1):
                if ph[i] in ARPA_VOWELS:
                    out["weak"] = "w:" + " ".join(
                        [ph[i]] + [_coda_class(c) for c in ph[i + 1:]])
                    break
    elif key.startswith("v:"):  # vowel tail
        out["slant"] = key
        mk = _multi_key(key[2:].split())
        if mk:
            out["multi"] = mk
    elif key.startswith("m:"):
        out["multi"] = key
    elif key.startswith("m2:"):
        out["multi2"] = key
    elif key.startswith("c:"):
        out["vc"] = key
    elif key.startswith("w:"):
        out["weak"] = key
    return out


def _multi_key(vowels: list[str]) -> str | None:
    """Key for multisyllabic slant rhymes: a 2+ vowel run where the first
    vowel must match exactly and later reduced vowels are merged. Trailing
    schwas are trimmed so militia (IH-AH) still catches commissioner
    (IH-AH-ER) — the tail falls off the beat."""
    if len(vowels) < 2:
        return None
    parts = [vowels[0]] + ["x" if v in REDUCED else v for v in vowels[1:]]
    while len(parts) > 2 and parts[-1] == "x":
        parts.pop()
    return "m:" + " ".join(parts)


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


NASALS = {"M", "N", "NG"}


def _coda_class(c: str) -> str:
    """damn/hand/plans ride one nasal class in delivery; final s/z
    voicing neutralizes too (vamonos/dominoes)."""
    if c in NASALS:
        return "N"
    return "S" if c == "Z" else c


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
        key += " " + _coda_class(tail[1])
    return "c:" + key


def _m2_key(seq: list[str]) -> str | None:
    """Vowel + coda consonant + one reduced vowel — the consonant-supported
    variant of a 2-vowel key. V+schwa alone is too weak for phrases:
    door hinge shares orange's R, but sloth hugs has nothing of shoulder."""
    ph = [DIGITS.sub("", p) for p in seq]
    vowels = [p for p in ph if p in ARPA_VOWELS]
    if len(vowels) != 2 or vowels[1] not in REDUCED:
        return None
    vi = ph.index(vowels[0])
    if vi + 1 >= len(ph) or ph[vi + 1] in ARPA_VOWELS:
        return None  # open syllable — no coda to lean on
    return f"m2:{vowels[0]} {_coda_class(ph[vi + 1])} x"


def _final_coda_tag(pl: list[str]) -> str:
    """Coda-class string after the LAST vowel ('.' for open)."""
    last = -1
    for i, p in enumerate(pl):
        if p[-1].isdigit():
            last = i
    coda = "".join(_coda_class(DIGITS.sub("", p)) for p in pl[last + 1:])
    if coda.endswith("S"):
        coda = coda[:-1]  # trailing plural/3rd-person sibilant is transparent
    return coda or "."


WEAK_MK = re.compile(r"^m:[A-Z]+ x$")


def _coda_nest(ta: str, tb: str) -> bool:
    if ta == tb:
        return True
    if ta == "." or tb == ".":
        return False
    return (ta.startswith(tb) or tb.startswith(ta)
            or ta.endswith(tb) or tb.endswith(ta))


def multi_keys(word: str) -> tuple[str, ...]:
    """Multisyllabic keys across all candidate pronunciations, anchored at
    the last stressed vowel AND the first primary stress — KET-a-mine can
    rhyme from its first syllable (meth-am-PHET-a-mine) even though its
    dictionary stress sits at the end."""
    out = []
    for ph in phones_candidates(word):
        pl = ph.split()
        anchors = set()
        for i in range(len(pl) - 1, -1, -1):
            if pl[i][-1] in "12":
                anchors.add(i)
                break
        for i, p in enumerate(pl):
            if p[-1] == "1":
                anchors.add(i)
                break
        for a in anchors or {0}:
            vs = [DIGITS.sub("", p) for p in pl[a:] if p[-1].isdigit()]
            k = _multi_key(vs)
            if k:
                out.append(k)
            k2 = _m2_key(pl[a:])  # joinable by consonant-supported phrases
            if k2:
                out.append(k2)
    return tuple(dict.fromkeys(out))


def weak_end_key(word: str) -> str | None:
    """Final-syllable rime, stress be damned — at line ends poets rhyme
    the weak syllable (infancy / see), but the coda still has to agree
    (divinity does not rhyme screams)."""
    ph = phones_for(word)
    if not ph:
        return None
    pl = ph.split()
    for i in range(len(pl) - 1, -1, -1):
        if pl[i][-1].isdigit():
            syl = [DIGITS.sub("", p) for p in pl[i:]]
            if syl[0] in REDUCED:
                return None  # a bare schwa tail (-le, -able) rhymes
                # everything; weak endings need a full final vowel
                # (infancy/see on IY, not middle/unavoidable on AH-L)
            out = [syl[0]] + [_coda_class(c) for c in syl[1:]]
            return "w:" + " ".join(out)
    return None


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


MAX_DRAFT = 100_000  # chars — far beyond any song, well short of abuse


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/analyze")
def analyze(draft: Draft):
    if len(draft.text) > MAX_DRAFT:
        raise HTTPException(413, "draft too large")
    lines = draft.text.split("\n")

    # stanza ids (blank-line separated)
    sids: list[int | None] = []
    sid, prev_blank = -1, True
    for line in lines:
        stripped = line.strip()
        if not stripped:
            sids.append(None)
            prev_blank = True
            continue
        if stripped[0] in "#([":
            # annotation line ([Chorus], (yeah), # notes) — no highlighting,
            # no scheme letter, and it doesn't split the stanza either
            sids.append(None)
            continue
        if prev_blank:
            sid += 1
        prev_blank = False
        sids.append(sid)

    tokens = []
    for i, line in enumerate(lines):
        if sids[i] is None:
            continue
        # parentheticals can rhyme internally, but the line-ending slot
        # belongs to the last word OUTSIDE parens — "(yeah)" tails and
        # backing vocals never set the scheme
        adlib, depth, astart = [], 0, None
        for j, ch in enumerate(line):
            if ch == "(":
                if depth == 0:
                    astart = j
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
                if depth == 0:
                    adlib.append((astart, j + 1))
        if depth:
            adlib.append((astart, len(line)))
        all_matches = list(WORD_RE.finditer(line))
        outside = [m for m in all_matches
                   if not any(s <= m.start() < e for s, e in adlib)]
        last_out = outside[-1] if outside else None
        for m in all_matches:
            tokens.append({
                "line": i, "start": m.start(), "end": m.end(),
                "word": m.group(0), "is_end": m is last_out,
                "sid": sids[i], "gid": None, "slant": False,
            })

    # words the draft leans on as refrain/filler (4+ uses) stop lighting
    # up mid-line — their line-end uses still count
    counts = Counter(t["word"].lower() for t in tokens)
    refrain = {w for w, c in counts.items() if c >= 4}

    # phrase tokens: adjacent word pairs, so multi-word rhymes can match
    # single words (orange / door hinge). Anchored at the first word's
    # stressed vowel; competes in the multisyllabic slant pass only.
    phrases = []
    line_toks = defaultdict(list)
    for t in tokens:
        line_toks[t["line"]].append(t)
    for toks in line_toks.values():
        for a, b in zip(toks, toks[1:]):
            if a["word"].lower() in refrain:
                continue  # «Forever ever» carpets obey refrain muting too
            pa, pb = phones_for(a["word"]), phones_for(b["word"])
            if not (pa and pb):
                continue
            # the phrase's rime runs from the anchor's stressed vowel
            # through the end of the tail: "stir up" = ER AH P, which is
            # a PERFECT rhyme with syrup
            pl = pa.split()
            start = 0
            for i in range(len(pl) - 1, -1, -1):
                if pl[i][-1] in "12":
                    start = i
                    break
            phrases.append({
                "line": a["line"], "start": a["start"], "end": b["end"],
                "word": a["word"].lower() + " " + b["word"].lower(),
                "is_end": b["is_end"], "sid": a["sid"], "gid": None,
                "slant": False, "vowels": _tail_vowels(pa) + _all_vowels(pb),
                "rime": DIGITS.sub("", " ".join(pl[start:] + pb.split())),
                # a phrase touching a stopword ("were up") may still match
                # perfectly, but never competes in the vowel-only passes
                "weak": (a["word"].lower() in STOPWORDS
                         or b["word"].lower() in STOPWORDS
                         or b["word"].lower() in refrain),
            })

    # mosaic triples: three-word runs whose vowel run is the rhyme —
    # "mean to it" / "seen do it" / "theme music" (IY UW x). Anchor must
    # carry content; the tail words may be anything pronounceable.
    for toks in line_toks.values():
        for a, b, c in zip(toks, toks[1:], toks[2:]):
            if a["word"].lower() in STOPWORDS:
                continue
            if a["word"].lower() in refrain:
                continue
            pa, pb, pc = (phones_for(a["word"]), phones_for(b["word"]),
                          phones_for(c["word"]))
            if not (pa and pb and pc):
                continue
            tail_vowels = _all_vowels(pb) + _all_vowels(pc)
            if not any(v not in REDUCED for v in tail_vowels):
                continue  # the mosaic must span words: "mean TO it" does,
                          # "methamphetamine with the" is just its anchor
            vowels = _tail_vowels(pa) + tail_vowels
            if len(vowels) < 3:
                continue
            pl = pa.split()
            start = 0
            for i in range(len(pl) - 1, -1, -1):
                if pl[i][-1] in "12":
                    start = i
                    break
            phrases.append({
                "line": a["line"], "start": a["start"], "end": c["end"],
                "word": " ".join(w["word"].lower() for w in (a, b, c)),
                "is_end": c["is_end"], "sid": a["sid"], "gid": None,
                "slant": False, "vowels": vowels,
                "rime": DIGITS.sub(
                    "", " ".join(pl[start:] + pb.split() + pc.split())),
                "weak": False,
            })

    # pass 1: perfect rhymes (shared rime), anywhere in a line — this is
    # what catches internal rhymes. Phrases compete too, so "stir up"
    # perfect-rhymes "syrup" even while its "up" rhymes with "cup".
    by_rime = defaultdict(list)
    for t in tokens:
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or w in refrain or len(w) < 2):
            continue
        for key in rime_keys(t["word"]):
            by_rime[(t["sid"], key)].append(t)
    for p in phrases:
        # all phrases compete on exact rime — "is it" matches "visit"
        # phone-for-phone; the qualification below stops stopword-anchored
        # phrases from pairing with nothing but each other
        by_rime[(p["sid"], "p:" + p["rime"])].append(p)

    # biggest buckets claim their tokens first, so a word with several
    # candidate pronunciations joins its best-supported rhyme group.
    # Each group remembers its founding key — the sound it's about.
    raw_groups: list[dict] = []
    claimed: set[int] = set()
    for (sid, key), toks in sorted(by_rime.items(),
                                   key=lambda kv: (-len(kv[1]), kv[0][1])):
        toks = [t for t in toks if id(t) not in claimed]
        # a SECONDARY pronunciation only reaches nearby partners (the
        # verb pred-i-KATE shouldn't hand "predicate" to a hook's "eight"
        # forty lines away when the noun rhymes with its neighbor)
        kept = []
        for t in toks:
            if "rime" in t or len(rime_keys(t["word"])) == 1:
                kept.append(t)  # phrases and unambiguous words: anywhere
            elif any(abs(o["line"] - t["line"]) <= 4
                     for o in toks if o is not t):
                kept.append(t)
        toks = kept
        if len(toks) < 2:
            continue
        # distinctness by anchor word, so "fire burns" can't pose as a
        # rhyme partner for the "fire" it starts with
        distinct = {t["word"].split()[0].lower() for t in toks}
        if len(distinct) < 2:
            continue  # repetition is refrain, not rhyme — a group only
            # colors once a DIFFERING word rhymes into it
        if all(" " in t["word"] and t["word"].split()[0] in STOPWORDS
               for t in toks):
            continue  # stopword-anchored phrases need a real-word partner
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
            if kind == "multi":
                # members may advertise their own HIGH-SPECIFICITY mosaic
                # anchors (2+ full vowels): mastermind founds on AY N D
                # with rind, but its AE..AY run is what "pass the time"
                # needs to find
                for t in g["toks"]:
                    if " " in t["word"]:
                        continue
                    for mk in set(multi_keys(t["word"])):
                        if (mk.startswith("m:")
                                and sum(v != "x" for v in mk[2:].split()) >= 2):
                            out.setdefault((t["sid"], mk), gi)
            elif kind in ("multi2", "vc"):
                # vowel-only founding keys can't carry a coda, but if 2+
                # members agree on one (orange + pourage both AO-R-schwa;
                # hand + plans both AE-nasal), it's part of the group's
                # sound and others may join on it
                counts: Counter = Counter()
                for t in g["toks"]:
                    if kind == "vc":
                        mks = [vc_key(t["word"])] if vc_key(t["word"]) else []
                    else:
                        mks = [m for m in set(multi_keys(t["word"]))
                               if m.startswith("m2:")]
                    for mk in mks:
                        counts[mk] += 1
                for mk, c in counts.items():
                    if c >= 2:
                        for s in {t["sid"] for t in g["toks"]}:
                            out.setdefault((s, mk), gi)
        return out

    def attach_or_collect(t, key, bucket, gmap):
        gi = gmap.get((t["sid"], key))
        if gi is not None and any(abs(m["line"] - t["line"]) <= 8
                                  for m in raw_groups[gi]["toks"]):
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
    # line-ending PHRASES get the same end-position privilege as words:
    # pure vowel-run matching ("forgotten" / "off of" — AA-schwa)
    end_spans = defaultdict(list)
    for g in raw_groups:
        for t in g["toks"]:
            if " " not in t["word"]:
                end_spans[t["line"]].append((t["start"], t["end"]))
    for p in phrases:
        if not p["is_end"] or id(p) in grouped or len(p["vowels"]) < 2:
            continue
        if any(s < p["end"] <= e for s, e in end_spans[p["line"]]):
            continue  # the tail word already claimed this line's ending
        key = "v:" + " ".join(p["vowels"])
        gi = group_by_slant.get((p["sid"], key))
        if gi is not None:
            raw_groups[gi]["toks"].append(p)
            p["slant"] = True
            grouped.add(id(p))
        else:
            by_slant[(p["sid"], key)].append(p)

    for (sid, key), toks in sorted(by_slant.items(),
                                   key=lambda kv: (-len(kv[1]), kv[0][1])):
        toks = [t for t in toks if id(t) not in grouped]
        if len(toks) >= 2 and len({t["word"].split()[0] for t in toks}) >= 2:
            raw_groups.append({"toks": toks, "slant": True, "key": key})
            grouped.update(id(t) for t in toks)

    # pass 3: multisyllabic slant rhymes anywhere in a line, per stanza —
    # tokens sharing a 2+ vowel run from the stressed syllable on
    # (placement / creation both carry EY AH; orange / door hinge both
    # carry AO + schwa). Single-vowel assonance is too noisy to flag
    # mid-line, so it stays end-of-line only (pass 2).
    group_by_multi = {**gmap_for("multi"), **gmap_for("multi2")}
    by_multi = defaultdict(list)
    for t in tokens:
        if id(t) in grouped:
            continue
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or w in refrain or len(w) < 2):
            continue
        keys = multi_keys(t["word"])
        t_tag = _final_coda_tag(phones_for(t["word"]).split()) \
            if phones_for(t["word"]) else "."
        joined = False
        for key in keys:  # join an existing family if any anchor fits
            gi = group_by_multi.get((t["sid"], key))
            if gi is None:
                continue
            # a bare V-x key is too weak to attach on alone: the token's
            # coda class must nest with the group's (garbage JH / dollar
            # R don't, even though both are AA-x)
            if WEAK_MK.match(key):
                gtags = {_final_coda_tag(phones_for(m["word"]).split())
                         for m in raw_groups[gi]["toks"]
                         if " " not in m["word"] and phones_for(m["word"])}
                if gtags and not any(_coda_nest(t_tag, gt) for gt in gtags):
                    continue
            raw_groups[gi]["toks"].append(t)
            t["slant"] = True
            grouped.add(id(t))
            joined = True
            break
        if not joined:
            for key in keys:
                by_multi[(t["sid"], key)].append(t)

    # which group holds each already-rhyming word, per line
    grouped_spans = defaultdict(list)
    for gi, g in enumerate(raw_groups):
        for t in g["toks"]:
            if " " not in t["word"]:
                grouped_spans[t["line"]].append((t["start"], t["end"], gi))
    by_par = defaultdict(list)
    for p in phrases:
        if p["weak"]:
            continue
        vs = p["vowels"]
        full = sum(1 for v in vs if v not in REDUCED)
        if len(vs) >= 3 or (len(vs) == 2 and vs[1] not in REDUCED):
            key = _multi_key(vs)
        else:
            # V+schwa phrases must bring consonant support
            key = _m2_key(p["rime"].split())
        if not key:
            continue
        if p["word"].count(" ") == 2:
            # mosaic triples must carry at least two full vowels —
            # anchor + schwa-tails ("Sleep is the") prove nothing
            if sum(v != "x" for v in key[2:].split()) < 2:
                continue
        if full < 2 and not key.startswith("m2:"):
            # a schwa-heavy run can't barge into a word family ("Two
            # bitches" -> the Tuna chain), but parallel phrases may pair
            # with each other (clock's ticking / stop tripping)
            by_par[(p["sid"], key)].append(p)
            continue
        spans = grouped_spans[p["line"]]
        a_gi = next((gi for s, e, gi in spans if s <= p["start"] < e), None)
        b_gi = next((gi for s, e, gi in spans if s < p["end"] <= e), None)
        p["halves"] = (a_gi, b_gi)
        if a_gi is not None and b_gi is not None:
            # both ends already rhyme — the phrase still matters if it
            # ties somewhere beyond its anchor's own family (four-inch
            # joining the orange clan; "pass the time" reaching the
            # group where mastermind advertises its AE..AY run)
            gi = group_by_multi.get((p["sid"], key))
            if gi is not None and gi != a_gi:
                raw_groups[gi]["toks"].append(p)
                p["slant"] = True
                grouped.add(id(p))
            else:
                # or if it can seed a family with non-mirror siblings
                # (mean to it / seen do it / theme music)
                by_multi[(p["sid"], key)].append(p)
            continue
        attach_or_collect(p, key, by_multi, group_by_multi)

    for (sid, key), toks in by_par.items():
        toks = sorted((t for t in toks if id(t) not in grouped),
                      key=lambda t: t["line"])
        runs, cur = [], []
        for t in toks:
            if cur and t["line"] - cur[-1]["line"] > 6:
                runs.append(cur)
                cur = []
            cur.append(t)
        if cur:
            runs.append(cur)
        for run in runs:
            if len(run) >= 2 and len({t["word"].split()[0] for t in run}) >= 2:
                raw_groups.append({"toks": run, "slant": True, "key": key})
                grouped.update(id(t) for t in run)

    # biggest buckets claim first (a token may sit in several via its
    # anchors); distinctness by anchor word, so the phrase "fire burns"
    # can't pose as a different word than the "fire" it starts with
    def _flush_multi(toks, key):
        toks = sorted((t for t in toks if id(t) not in grouped),
                      key=lambda t: t["line"])
        # vowel evidence is local: split on gaps of more than 6 lines
        runs, cur = [], []
        for t in toks:
            if cur and t["line"] - cur[-1]["line"] > 6:
                runs.append(cur)
                cur = []
            cur.append(t)
        if cur:
            runs.append(cur)
        for run in runs:
            _flush_multi_run(run, key)

    def _flush_multi_run(toks, key):
        if len(toks) < 2 or len({t["word"].split()[0] for t in toks}) < 2:
            return
        # an all-phrase bucket whose members mirror the same two word
        # groups is pure redundancy (oh my / go rhyme over oh+go, my+rhyme)
        halves = {t.get("halves") for t in toks}
        if (len(halves) == 1
                and None not in halves
                and None not in next(iter(halves))):
            return
        raw_groups.append({"toks": toks, "slant": True, "key": key})
        grouped.update(id(t) for t in toks)

    def _word_tag(t):
        ph = phones_for(t["word"])
        return _final_coda_tag(ph.split()) if ph else "."

    def _tags_ok(ta, tb):
        if ta == tb:
            return True
        if ta == "." or tb == ".":
            return False
        return (ta.startswith(tb) or tb.startswith(ta)
                or ta.endswith(tb) or tb.endswith(ta))

    for (sid, key), toks in sorted(by_multi.items(),
                                   key=lambda kv: (-len(kv[1]), kv[0][1])):
        if not WEAK_MK.match(key):
            _flush_multi(toks, key)
            continue
        # a bare V-x signature is too weak on its own: subdivide the
        # bucket by nesting final-coda classes, so placement (NT) keeps
        # creation (N) but forever (.) lets go of sequential (L)
        words = [t for t in toks if " " not in t["word"]]
        phs = [t for t in toks if " " in t["word"]]
        tags = [_word_tag(t) for t in words]
        par = list(range(len(words)))

        def _f(i):
            while par[i] != i:
                par[i] = par[par[i]]
                i = par[i]
            return i

        for i in range(len(words)):
            for j in range(i + 1, len(words)):
                if _tags_ok(tags[i], tags[j]):
                    par[_f(i)] = _f(j)
        clus = defaultdict(list)
        for i, t in enumerate(words):
            clus[_f(i)].append(t)
        subsets = sorted(clus.values(), key=len, reverse=True)
        if phs:
            if subsets:
                subsets[0].extend(phs)  # phrases ride the main cluster
            else:
                subsets = [phs]
        for sub in subsets:
            _flush_multi(sub, key)

    # pass 4: consonance-aware slant anywhere in a line — last stressed
    # vowel + first coda consonant, so bliss / whisps / exist (IH S) group
    # even though their full codas differ
    group_by_vc = gmap_for("vc")
    # a word inside an already-grouped phrase sits this pass out,
    # so "door" doesn't fight "door hinge" for the highlight
    # (words inside grouped phrases used to sit this pass out; with
    # fills-only rendering, word and phrase paint coexist — "time" can
    # rhyme "mind" even while «all this time» rides a mosaic)
    by_vc = defaultdict(list)
    for t in tokens:
        if id(t) in grouped:
            continue
        w = t["word"].lower()
        if not t["is_end"] and (w in STOPWORDS or w in refrain or len(w) < 2):
            continue
        key = vc_key(t["word"])
        if key:
            attach_or_collect(t, key, by_vc, group_by_vc)
    def flush_cluster(cluster, key):
        if (len(cluster) >= 2
                and len({t["word"].lower() for t in cluster}) >= 2):
            raw_groups.append({"toks": list(cluster), "slant": True,
                               "key": key})
            grouped.update(id(t) for t in cluster)
    for (sid, key), toks in by_vc.items():
        # consonance is local evidence: a coda match ten lines away isn't
        # a rhyme, so clusters break on gaps of more than two lines
        toks = sorted((t for t in toks if id(t) not in grouped),
                      key=lambda t: t["line"])
        cluster = []
        for t in toks:
            if cluster and t["line"] - cluster[-1]["line"] > 2:
                flush_cluster(cluster, key)
                cluster = []
            cluster.append(t)
        flush_cluster(cluster, key)

    # pass 5: weak endings, the LAST resort — infancy rhymes see on its
    # unstressed final syllable, but only after every richer reading
    # (commissioner belongs with militia, not with "her")
    group_by_weak = gmap_for("weak")
    by_weak = defaultdict(list)
    for t in tokens:
        if not t["is_end"] or id(t) in grouped:
            continue
        key = weak_end_key(t["word"])
        if key:
            attach_or_collect(t, key, by_weak, group_by_weak)
    for (sid, key), toks in sorted(by_weak.items(),
                                   key=lambda kv: (-len(kv[1]), kv[0][1])):
        toks = [t for t in toks if id(t) not in grouped]
        if len(toks) >= 2 and len({t["word"].lower() for t in toks}) >= 2:
            raw_groups.append({"toks": toks, "slant": True, "key": key})
            grouped.update(id(t) for t in toks)

    # stopword-anchored phrases ("were up") never compete on their own,
    # but an exact rime match against any grouped token lets them ride
    # along — perfect phone identity carries no transitive-key risk
    rime_map: dict[tuple, int] = {}
    for gi, g in enumerate(raw_groups):
        for t in g["toks"]:
            keys = ("p:" + t["rime"],) if "rime" in t else rime_keys(t["word"])
            for k in keys:
                if k.startswith("p:"):
                    rime_map.setdefault((t["sid"], k), gi)
    for p in phrases:
        if id(p) in grouped or p["word"].split()[0] not in STOPWORDS:
            continue
        gi = rime_map.get((p["sid"], "p:" + p["rime"]))
        if gi is not None:
            raw_groups[gi]["toks"].append(p)
            grouped.add(id(p))

    # fuse groups that carry the same vowel family — a perfect subgroup
    # (shoulder/older/colder) shouldn't split colors with the slant family
    # it lives inside (soldier/holster/coaster). Perfect members keep the
    # strong styling; the slant side keeps per-token slant marks.
    mkeys = [founding_projections(g["key"]).get("multi") for g in raw_groups]
    mparent = list(range(len(raw_groups)))

    def mfind(i):
        while mparent[i] != i:
            mparent[i] = mparent[mparent[i]]
            i = mparent[i]
        return i

    def _tail_of(short, long):
        return len(short) <= len(long) and long[len(long) - len(short):] == short

    def _sig(key):
        vs = key[2:].split("|", 1)[0].split()
        while vs and vs[-1] == "x":
            vs.pop()  # trailing schwas fall off the beat on both sides
        return vs

    def _gtags(gi):
        out = set()
        for t in raw_groups[gi]["toks"]:
            if " " in t["word"]:
                continue
            ph = phones_for(t["word"])
            if ph:
                out.add(_final_coda_tag(ph.split()))
        return out

    def _tags_ok2(ta, tb):
        if ta == tb:
            return True
        if ta == "." or tb == ".":
            return False
        return (ta.startswith(tb) or tb.startswith(ta)
                or ta.endswith(tb) or tb.endswith(ta))

    def _sets_nest(A, B):
        if not A or not B:
            return True  # phrase-only family: no coda evidence to refuse
        return any(_tags_ok2(x, y) for x in A for y in B)

    for ai in range(len(raw_groups)):
        if not mkeys[ai]:
            continue
        va = _sig(mkeys[ai])
        if not va:
            continue
        for bi in range(ai + 1, len(raw_groups)):
            if not mkeys[bi]:
                continue
            vb = _sig(mkeys[bi])
            if not vb:
                continue
            # equal keys fuse; so do END-ALIGNED containments — a family
            # rhyming on AA-x is the tail of one rhyming on AE-AA-x
            # (back pocket / rap profit / office), the longer just
            # carries lead syllables. But when BOTH signatures are a
            # single vowel, the final codas must nest too — forever (.)
            # and sequential (L) share EH-x and still aren't one family
            if va == vb or _tail_of(va, vb) or _tail_of(vb, va):
                if raw_groups[ai]["toks"][0]["sid"] != raw_groups[bi]["toks"][0]["sid"]:
                    continue  # families don't fuse across stanzas
                if (len(va) == 1 and len(vb) == 1
                        and not _sets_nest(_gtags(ai), _gtags(bi))):
                    continue
                if va != vb:
                    # containment (unequal lengths) is weaker evidence
                    # than identity: the families must actually meet —
                    # Baby (l23) never fuses with Daddy (l136)
                    la = {t["line"] for t in raw_groups[ai]["toks"]}
                    lb = {t["line"] for t in raw_groups[bi]["toks"]}
                    if min(abs(x - y) for x in la for y in lb) > 8:
                        continue
                mparent[mfind(ai)] = mfind(bi)

    mclusters = defaultdict(list)
    for gi in range(len(raw_groups)):
        mclusters[mfind(gi)].append(gi)
    fused: list[dict] = []
    for members in mclusters.values():
        if len(members) == 1:
            fused.append(raw_groups[members[0]])
            continue
        hub = max(members, key=lambda gi: (not raw_groups[gi]["slant"],
                                           len(raw_groups[gi]["toks"])))
        core = raw_groups[hub]
        for gi in members:
            if gi == hub:
                continue
            g = raw_groups[gi]
            if g["slant"]:
                for t in g["toks"]:
                    t["slant"] = True
            core["toks"].extend(g["toks"])
        fused.append(core)
    raw_groups = fused

    # fuse single-vowel perfect families whose coda classes NEST — the
    # hook chain: wrist (IH S T) is the hub that pulls in this (IH S, a
    # prefix) and shit (IH T, a suffix). Same-vowel families with nested
    # codas read as one chain in delivery; absorbed members mark slant.
    def _sv_parse(g):
        key = g["key"]
        if not key.startswith("p:"):
            return None
        ph = key[2:].split()
        if not ph or ph[0] not in ARPA_VOWELS:
            return None
        if any(p in ARPA_VOWELS for p in ph[1:]):
            return None
        coda = tuple(_coda_class(c) for c in ph[1:])
        return (ph[0], coda) if coda else None

    sv = [(gi, p) for gi, p in ((gi, _sv_parse(g))
                                for gi, g in enumerate(raw_groups)) if p]
    parent = list(range(len(raw_groups)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def _endy(gi):
        return sum(t["is_end"] for t in raw_groups[gi]["toks"]) >= 2

    for ai in range(len(sv)):
        for bi in range(ai + 1, len(sv)):
            gi, (va, ca) = sv[ai]
            gj, (vb, cb) = sv[bi]
            if va != vb or ca == cb:
                continue
            s, l = (ca, cb) if len(ca) < len(cb) else (cb, ca)
            nested = l[:len(s)] == s or l[len(l) - len(s):] == s
            if raw_groups[gi]["toks"][0]["sid"] != raw_groups[gj]["toks"][0]["sid"]:
                continue  # no coda fusion across stanzas
            # end-dominated families rhyme on their bare vowel, the way
            # lone endings always could (blood/mud + thugs/drugs — the
            # Kanye chorus chain); mid-line families keep their codas
            if nested or (_endy(gi) and _endy(gj)):
                parent[find(gi)] = find(gj)

    clusters = defaultdict(list)
    for gi in range(len(raw_groups)):
        clusters[find(gi)].append(gi)
    if any(len(m) > 1 for m in clusters.values()):
        def _coda_len(gi):
            p = _sv_parse(raw_groups[gi])
            return len(p[1]) if p else 0
        fused2 = []
        for members in clusters.values():
            if len(members) == 1:
                fused2.append(raw_groups[members[0]])
                continue
            hub = max(members,
                      key=lambda gi: (_coda_len(gi),
                                      len(raw_groups[gi]["toks"])))
            core = raw_groups[hub]
            for gi in members:
                if gi == hub:
                    continue
                for t in raw_groups[gi]["toks"]:
                    t["slant"] = True
                core["toks"].extend(raw_groups[gi]["toks"])
            fused2.append(core)
        raw_groups = fused2

    # a word group living ENTIRELY inside one family's phrases is a
    # mirror of that family — four/door inside «four-inch»/«door hinge»:
    # the compound reading wins and the mirror dissolves, so the phrase
    # color paints the words too
    grouped_phrase_spans = []
    for gi, g in enumerate(raw_groups):
        for t in g["toks"]:
            if " " in t["word"]:
                grouped_phrase_spans.append(
                    (t["line"], t["start"], t["end"], gi))
    if grouped_phrase_spans:
        kept_groups = []
        for gi, g in enumerate(raw_groups):
            if any(" " in t["word"] for t in g["toks"]):
                kept_groups.append(g)
                continue
            covers: set[int] = set()
            all_covered = True
            for t in g["toks"]:
                f = {pg for (pl, ps, pe, pg) in grouped_phrase_spans
                     if pl == t["line"] and ps <= t["start"]
                     and t["end"] <= pe and pg != gi}
                if not f:
                    all_covered = False
                    break
                covers |= f
            if all_covered and len(covers) == 1:
                continue
            kept_groups.append(g)
        raw_groups = kept_groups

    # stable colors: order groups by first appearance, then assign hues
    # adjacency-aware — a family avoids colors already worn by families
    # on its own or neighboring lines, so look-alikes never touch
    raw_groups.sort(key=lambda g: min((t["line"], t["start"]) for t in g["toks"]))
    line_sets = [{t["line"] for t in g["toks"]} for g in raw_groups]
    grown = [s | {l - 1 for l in s} | {l + 1 for l in s} for s in line_sets]
    groups_out = []
    chosen: list[int] = []
    usage = [0] * COLORS
    for gid, g in enumerate(raw_groups):
        for t in g["toks"]:
            t["gid"] = gid
        blocked = {chosen[j] for j in range(gid) if grown[j] & line_sets[gid]}
        # among non-adjacent colors, take the globally least-used one, so
        # hues spread evenly instead of piling on the low indices
        avail = [c for c in range(COLORS) if c not in blocked] or list(range(COLORS))
        color = min(avail, key=lambda c: (usage[c], c))
        usage[color] += 1
        chosen.append(color)
        groups_out.append({"id": gid, "color": color, "slant": g["slant"]})

    # stanza rhyme schemes from line-ending groups: the token covering
    # the most of the line's tail owns the slot — Em rhymes "-cock it",
    # not "it"
    end_best: dict[int, tuple[int, int]] = {}
    for t in [*tokens, *phrases]:
        if t["is_end"] and t["gid"] is not None:
            span = t["end"] - t["start"]
            cur = end_best.get(t["line"])
            if cur is None or span > cur[0]:
                end_best[t["line"]] = (span, t["gid"])
    end_gid = {ln: gid for ln, (sp, gid) in end_best.items()}
    last_tok = {}
    for t in tokens:
        if t["is_end"]:
            last_tok[t["line"]] = t
    stanza_lines = defaultdict(list)
    for i, s in enumerate(sids):
        if s is not None:
            stanza_lines[s].append(i)

    stanzas = []
    for s in sorted(stanza_lines):
        letters, order = [], {}
        for i in stanza_lines[s]:
            gid = end_gid.get(i)
            if gid is not None:
                key = gid
            elif i in last_tok:
                # refrains share a letter even though they don't color
                key = "w:" + last_tok[i]["word"].lower()
            else:
                key = f"solo:{i}"
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
         "end": t["is_end"], "ph": "vowels" in t,
         "slant": t["slant"] or groups_out[t["gid"]]["slant"]}
        for t in [*tokens, *phrases] if t["gid"] is not None
    ]
    meter, meter_by_line = [], {}
    for i, line in enumerate(lines):
        if sids[i] is None:
            continue
        m = line_meter(line)
        if m:
            entry = {"l": i, **m, "off": False}
            meter.append(entry)
            meter_by_line[i] = entry

    # meter coaching: when a stanza has a clear syllable pattern, flag
    # the lines that break it
    for s, lns in stanza_lines.items():
        entries = [meter_by_line[i] for i in lns if i in meter_by_line]
        if len(entries) < 3:
            continue
        counts = [e["syl"] for e in entries]
        # mode with +-1 tolerance: the count that covers the most lines
        mode = max(set(counts), key=lambda c: sum(abs(v - c) <= 1 for v in counts))
        covered = sum(abs(v - mode) <= 1 for v in counts)
        if covered / len(entries) >= 0.6:
            for e in entries:
                e["off"] = abs(e["syl"] - mode) > 1
            for e in entries:
                e["target"] = mode

    # per-word stress for the optional dots layer (2+ syllables only —
    # a dot under every monosyllable is noise, not information)
    stress_out = []
    for t in tokens:
        ph = phones_for(t["word"])
        if not ph:
            continue
        st = "".join("1" if p[-1] in "12" else "0"
                     for p in ph.split() if p[-1].isdigit())
        if st:  # one dot even for monosyllables
            stress_out.append({"l": t["line"], "s": t["start"],
                               "e": t["end"], "st": st})

    # alliteration: words sharing an initial consonant SOUND, clustered
    # locally (same or adjacent lines) — head-rhyme to the tails above
    allit_out = []
    onset_map = defaultdict(list)
    for t in tokens:
        w = t["word"].lower()
        if w in STOPWORDS or w in refrain:
            continue
        ph = phones_for(t["word"])
        if not ph:
            continue
        first = ph.split()[0]
        if first[-1].isdigit():
            continue  # vowel-initial: classic alliteration is consonantal
        onset_map[first].append(t)
    allit_gid = 0
    for key in sorted(onset_map):
        toks = sorted(onset_map[key], key=lambda t: (t["line"], t["start"]))
        cluster: list = []

        def flush(cluster):
            nonlocal allit_gid
            distinct = {c["word"].lower() for c in cluster}
            if len(cluster) >= 3 and len(distinct) >= 2:
                for c in cluster:
                    allit_out.append({"l": c["line"], "s": c["start"],
                                      "e": c["end"], "g": allit_gid})
                allit_gid += 1

        for t in toks:
            if cluster and t["line"] - cluster[-1]["line"] > 1:
                flush(cluster)
                cluster = []
            cluster.append(t)
        flush(cluster)

    # unanswered endings: line-ends still waiting for a rhyme partner —
    # the open loops that tell a writer where to strike next
    open_out = []
    end_word_counts = Counter(t["word"].lower() for t in last_tok.values())
    for i, t in last_tok.items():
        if i not in end_gid and end_word_counts[t["word"].lower()] < 2:
            open_out.append({"l": i, "s": t["start"], "e": t["end"]})

    return {"lines": lines, "tokens": toks_out, "groups": groups_out,
            "stanzas": stanzas, "meter": meter, "stress": stress_out,
            "allit": allit_out, "open": open_out}


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


_wordnet = None


def get_wordnet():
    """WordNet via NLTK (already a g2p-en dependency), data on demand."""
    global _wordnet
    if _wordnet is None:
        from nltk.corpus import wordnet as wn
        try:
            wn.synsets("test")
        except LookupError:
            import nltk
            nltk.download("wordnet", quiet=True)
            nltk.download("omw-1.4", quiet=True)
        _wordnet = wn
    return _wordnet


POS_NAMES = {"n": "noun", "v": "verb", "a": "adjective",
             "s": "adjective", "r": "adverb"}


def synonyms_for(w: str, limit: int) -> list[dict]:
    """Word associations from WordNet, in sections: synonyms, opposites,
    broader terms, and related words. Input is lemmatized first so
    'keys' and 'feeling' resolve to 'key' and 'feel'."""
    wn = get_wordnet()
    base = w.replace(" ", "_")
    for pos in ("n", "v", "a", "r"):
        m = wn.morphy(base, pos)
        if m:
            base = m
            break

    sections: dict[str, dict[str, str]] = {
        "synonyms": {}, "opposites": {}, "broader": {}, "related": {}}

    def add(bucket, lemma, pos):
        name = lemma.name().replace("_", " ").lower()
        if name not in (w, base) and re.fullmatch(r"[a-z' -]+", name):
            sections[bucket].setdefault(name, POS_NAMES.get(pos, pos))

    for ss in wn.synsets(base):
        pos = ss.pos()
        for lemma in ss.lemmas():
            add("synonyms", lemma, pos)
            for ant in lemma.antonyms():
                add("opposites", ant, pos)
            for dr in lemma.derivationally_related_forms():
                add("related", dr, dr.synset().pos())
        if pos in ("a", "s"):  # adjectives: the satellite clusters
            for sim in ss.similar_tos():
                for lemma in sim.lemmas():
                    add("related", lemma, pos)
        for hyper in ss.hypernyms():
            for lemma in hyper.lemmas():
                add("broader", lemma, pos)
        for hypo in ss.hyponyms()[:8]:
            for lemma in hypo.lemmas():
                add("related", lemma, pos)
        for other in ss.also_sees() + ss.attributes():
            for lemma in other.lemmas():
                add("related", lemma, other.pos())

    # a word belongs to its strongest section only
    caps = {"synonyms": 30, "opposites": 10, "broader": 12, "related": 20}
    taken: set[str] = set()
    out = []
    for label in ("synonyms", "opposites", "broader", "related"):
        items = {n: p for n, p in sections[label].items() if n not in taken}
        ranked = sorted(items.items(),
                        key=lambda kv: (-zipf_frequency(kv[0], "en"), kv[0]))
        ranked = ranked[:caps[label]]
        taken.update(n for n, _ in ranked)
        if ranked:
            out.append({"label": label,
                        "words": [{"word": n, "pos": p} for n, p in ranked]})
    return out


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
        out.append({"word": w, "z": round(z, 1),
                    "syl": pronouncing.syllable_count(ph) if ph else 0})
    return out


_homophone_index: dict[str, list[str]] | None = None


def get_homophones(w: str, phones: str) -> list[str]:
    global _homophone_index
    if _homophone_index is None:
        pronouncing.init_cmu()
        idx: dict[str, list[str]] = defaultdict(list)
        for word, ph in pronouncing.pronunciations:
            if word.isalpha() and zipf_frequency(word, "en") >= 3.0:
                idx[DIGITS.sub("", _norm_r(ph))].append(word)
        _homophone_index = idx
    return [h for h in _homophone_index.get(DIGITS.sub("", phones), [])
            if h != w][:6]


@app.get("/api/word")
def word_info(word: str):
    """Phonetic anatomy of a word — or a phrase, read straight through."""
    w = " ".join(word.strip().lower().split()[:4])[:64]
    if " " in w:
        parts = [phones_for(p) for p in w.split()]
        phones = " ".join(p for p in parts if p) if all(parts) else None
    else:
        phones = phones_for(w)
    if not phones:
        return {"word": w, "known": False}
    pl = phones.split()
    stress = "".join("1" if p[-1] in "12" else "0"
                     for p in pl if p[-1].isdigit())
    rime = DIGITS.sub("", pronouncing.rhyming_part(phones))
    senses = None
    try:
        wn = get_wordnet()
        base = w.replace(" ", "_")
        for pos in ("n", "v", "a", "r"):
            m = wn.morphy(base, pos)
            if m:
                base = m
                break
        senses = len(wn.synsets(base)) or None
    except Exception:
        pass
    return {"word": w, "known": True,
            "phones": DIGITS.sub("", phones), "syl": len(stress),
            "stress": stress, "rime": rime, "senses": senses,
            "homophones": [] if " " in w else get_homophones(w, phones),
            "zipf": round(zipf_frequency(w, "en"), 1)}


_multi_left: dict[str, list[str]] | None = None
_multi_right: dict[str, list[str]] | None = None


def _squeeze_vs(vs: list[str]) -> str:
    """Vowel skeleton: first vowel exact, later reduced vowels merge to x
    — the same equivalence the multi detection passes use."""
    return " ".join([vs[0]] + ["x" if v in REDUCED else v for v in vs[1:]])


def get_multi_indexes():
    """tail-skeleton -> words (left halves) and full-skeleton -> words
    (right halves / whole-word matches), built once."""
    global _multi_left, _multi_right
    if _multi_left is None:
        pronouncing.init_cmu()
        left: dict[str, list[str]] = defaultdict(list)
        right: dict[str, list[str]] = defaultdict(list)
        seen = set()
        for w, phones in pronouncing.pronunciations:
            if (w in seen or not w.isalpha() or len(w) < 3
                    or zipf_frequency(w, "en") < 3.0):
                continue
            seen.add(w)
            ph = _norm_r(phones)
            tail = _tail_vowels(ph)
            if tail:
                left[_squeeze_vs(tail)].append(w)
            full = _all_vowels(ph)
            stressed = any(p[-1] in "12" for p in ph.split())
            if full and stressed:
                right[_squeeze_vs(full)].append(w)
        _multi_left, _multi_right = left, right
    return _multi_left, _multi_right


def target_skeleton(w: str) -> list[str] | None:
    """Vowel run from the first primary stress — where a rap multi
    anchors (e-LE-va-tor reads from its EH)."""
    if " " in w:
        parts = w.split()
        pa = phones_for(parts[0])
        rest = [phones_for(p) for p in parts[1:]]
        if not pa or not all(rest):
            return None
        vs = _tail_vowels(pa)
        for ph in rest:
            vs += _all_vowels(ph)
        return vs if len(vs) >= 2 else None
    ph = phones_for(w)
    if not ph:
        return None
    pl = ph.split()
    vi = next((i for i, p in enumerate(pl) if p[-1] == "1"),
              next((i for i, p in enumerate(pl) if p[-1].isdigit()), None))
    if vi is None:
        return None
    vs = [DIGITS.sub("", p) for p in pl[vi:] if p[-1].isdigit()]
    return vs if len(vs) >= 2 else None


def multis_for(w: str, exclude: set, limit: int = 14) -> list[str]:
    """Multisyllabic rhymes: single words and two-word combos whose
    vowel skeleton matches the target's (elevator -> hella paper)."""
    vs = target_skeleton(w)
    if not vs:
        return []
    skel = _squeeze_vs(vs)
    left, right = get_multi_indexes()
    avoid = set(exclude) | set(w.split()) | {w}
    scored: list[tuple[float, str]] = []
    for cand in right.get(skel, []):
        if cand not in avoid:
            scored.append((zipf_frequency(cand, "en"), cand))
    parts = skel.split()
    for i in range(1, len(parts)):
        lk, rk = " ".join(parts[:i]), " ".join(parts[i:])
        if not any(v != "x" for v in parts[i:]):
            continue  # the right half must carry a full vowel
        lefts = sorted((w2 for w2 in left.get(lk, [])
                        if w2 not in STOPWORDS and w2 not in avoid),
                       key=lambda w2: -zipf_frequency(w2, "en"))[:8]
        rights = sorted((w2 for w2 in right.get(rk, [])
                         if w2 not in STOPWORDS and w2 not in avoid),
                        key=lambda w2: -zipf_frequency(w2, "en"))[:8]
        for a in lefts:
            za = min(zipf_frequency(a, "en"), 5.0)
            for b in rights:
                if b == a:
                    continue
                scored.append((za + min(zipf_frequency(b, "en"), 5.0) - 4.0,
                               f"{a} {b}"))
    scored.sort(key=lambda t: (-t[0], -len(t[1]), t[1]))
    out, seen = [], set()
    for _, c in scored:
        if c not in seen:
            seen.add(c)
            out.append(c)
        if len(out) >= limit:
            break
    return out


@app.get("/api/lookup")
def lookup(word: str, mode: str = "rhyme", limit: int = 60):
    w = word.strip().lower()[:64]
    limit = min(limit, 200)
    rhyme_on = None
    if " " in w and mode != "syn":
        rhyme_on = w.split()[-1]  # a phrase rhymes on its final word
        w = rhyme_on
    if mode == "syn":
        sections = synonyms_for(w, limit)
        return {"word": w, "mode": mode, "known": bool(sections),
                "sections": sections}
    phones = phones_for(w)
    if not phones:
        return {"word": w, "mode": mode, "known": False, "words": []}
    k = _slant_from_phones(phones)
    perfect = set(pronouncing.rhymes(w))
    near_cands = (get_slant_index().get(k, set()) - perfect) if k else set()
    if mode == "near":
        words = _ranked(near_cands, {w}, limit)
        return {"word": w, "mode": mode, "known": True, "words": words}
    # rhyme mode carries it all: perfect, slant, and multis
    words = _ranked(perfect, {w}, limit)
    near = _ranked(near_cands, {w}, limit // 2)
    target = word.strip().lower()[:64] if rhyme_on else w
    multis = multis_for(target, perfect)
    return {"word": w, "mode": mode, "known": True, "words": words,
            "near": near, "rhyme_on": rhyme_on, "multis": multis}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static",
                           html=True), name="static")
