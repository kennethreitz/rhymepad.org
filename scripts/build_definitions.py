"""Distill the English Wiktionary extract (kaikki.org, CC BY-SA) and the
Moby Thesaurus II (public domain) into RhymePad's lexicon. Headwords are
the CMUdict vocabulary, any non-rare word (zipf >= 2), and anything
Wiktionary tags as slang however rare — a rhyme pad wants "guap" and
"rizz" more than it wants frequency thresholds.

data/definitions.json.gz — word -> {"d": [[pos, gloss], ...] (max 3),
    "n": uncapped sense count, "of": base} ("of" marks inflections, so
    "hums" can lead with "hum"'s senses without a lemmatizer).

data/thesaurus.json.gz — word -> {"syn"/"opp"/"broad"/"rel": [words]}
    from Wiktionary's curated links, symmetrized (if A lists B as a
    synonym, B gets A too; hypernym/hyponym links invert likewise),
    plus "mob": the Moby exhaustive-synonym layer, zipf-ranked.

One-time, offline:

    curl -L -o build/kaikki-en.jsonl.gz \
        https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz
    curl -L -o build/mthesaur.txt \
        https://www.gutenberg.org/files/3202/files/mthesaur.txt
    uv run python scripts/build_definitions.py
"""

import gzip
import json
import re
from functools import lru_cache
from pathlib import Path

import pronouncing
from wordfreq import zipf_frequency

ROOT = Path(__file__).parent.parent
SRC = ROOT / "build" / "kaikki-en.jsonl.gz"
SRC_MOBY = ROOT / "build" / "mthesaur.txt"
OUT_DEFS = ROOT / "data" / "definitions.json.gz"
OUT_THES = ROOT / "data" / "thesaurus.json.gz"

WORD_OK = re.compile(r"[a-z][a-z'-]*")
LINK_OK = re.compile(r"[a-z][a-z' -]*")   # linked words may be phrases
MAX_GLOSSES = 3      # per word, across all parts of speech
MAX_PER_POS = 2      # a noun-heavy word still shows its verb sense
MAX_LEN = 140        # chars per gloss
SKIP_TAGS = {"obsolete", "archaic", "dated", "misspelling", "rare",
             "no-gloss", "nonstandard", "pejorative", "offensive"}
SKIP_POS = {"name", "proverb", "phrase", "punct", "character", "symbol"}
# wiktextract link field -> (bucket it lands in, bucket the reverse edge
# lands in on the linked word) — synonymy/antonymy are symmetric, the
# hypernym/hyponym pair inverts, the rest is mutual relatedness
LINKS = [("synonyms", "syn", "syn"), ("antonyms", "opp", "opp"),
         ("hypernyms", "broad", "rel"), ("hyponyms", "rel", "broad"),
         ("coordinate_terms", "rel", "rel"), ("related", "rel", "rel"),
         ("derived", "rel", "rel")]
CAPS = {"syn": 120, "opp": 40, "broad": 60, "rel": 120, "mob": 150}
MOBY_MIN_ZIPF = 2.0  # drop Moby's archaic dregs


def cmu_words() -> set[str]:
    pronouncing.init_cmu()
    return {w for w, _ in pronouncing.pronunciations
            if WORD_OK.fullmatch(w)}


def trim(gloss: str) -> str:
    gloss = " ".join(gloss.split())
    if len(gloss) > MAX_LEN:
        gloss = gloss[:MAX_LEN].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return gloss


def main():
    vocab = cmu_words()

    @lru_cache(maxsize=None)
    def headword(w: str) -> bool:
        return (w in vocab or zipf_frequency(w, "en") >= 2.0) \
            and WORD_OK.fullmatch(w) is not None

    defs: dict[str, dict] = {}   # word -> {"d": pairs, "n": count}
    bases: dict[str, str] = {}   # inflection -> base word
    thes: dict[str, dict] = {}   # word -> {bucket: [words]}
    n_lines = 0

    def put(w, bucket, t):
        got = thes.setdefault(w, {}).setdefault(bucket, [])
        if t not in got and len(got) < CAPS[bucket]:
            got.append(t)

    def harvest_links(w, obj, head):
        for field, bucket, rev in LINKS:
            for item in obj.get(field, []):
                t = item.get("word", "").strip()
                if t == w or not LINK_OK.fullmatch(t):
                    continue
                if head:
                    put(w, bucket, t)
                if headword(t):
                    put(t, rev, w)

    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            n_lines += 1
            if n_lines % 200_000 == 0:
                print(f"  …{n_lines:,} entries, {len(defs):,} words")
            e = json.loads(line)
            w = e.get("word", "")
            pos = e.get("pos", "")
            if pos in SKIP_POS or not WORD_OK.fullmatch(w):
                continue
            head = headword(w) or any(
                "slang" in (s.get("tags") or []) for s in e.get("senses", []))
            harvest_links(w, e, head)
            for s in e.get("senses", []):
                if set(s.get("tags", [])) & SKIP_TAGS:
                    continue
                harvest_links(w, s, head)
            if not head:
                continue
            ent = defs.setdefault(w, {"d": [], "n": 0})
            for s in e.get("senses", []):
                if set(s.get("tags", [])) & SKIP_TAGS:
                    continue
                if s.get("form_of") or s.get("alt_of"):
                    src = (s.get("form_of") or s.get("alt_of"))[0]
                    base = src.get("word", "")
                    if base and base != w:
                        bases.setdefault(w, base)
                    continue
                glosses = s.get("glosses") or []
                if not glosses:
                    continue
                ent["n"] += 1
                gloss = trim(glosses[0])
                pairs = ent["d"]
                if (len(pairs) >= MAX_GLOSSES
                        or sum(1 for p, _ in pairs if p == pos) >= MAX_PER_POS
                        or any(g == gloss for _, g in pairs)):
                    continue
                pairs.append([pos, gloss])

    # the Moby layer: huge loose-synonym lists, symmetric, kept apart
    # from the curated buckets so the runtime can rank them second
    print("  …Moby Thesaurus")
    for line in SRC_MOBY.read_text().splitlines():
        root, *members = line.strip().split(",")
        members = [m for m in members if LINK_OK.fullmatch(m)
                   and zipf_frequency(m, "en") >= MOBY_MIN_ZIPF]
        if headword(root):
            for m in sorted(members,
                            key=lambda m: -zipf_frequency(m, "en")):
                put(root, "mob", m)
        for m in members:
            if headword(m):
                put(m, "mob", root)

    # inflections carry a pointer to their base, ahead of any senses of
    # their own ("ran" is mostly "run", despite its own yarn-winch noun)
    out = {}
    for w, ent in defs.items():
        keep = {k: v for k, v in ent.items() if v}
        base = bases.get(w)
        if base and defs.get(base, {}).get("d"):
            keep["of"] = base
        if keep:
            out[w] = keep

    OUT_DEFS.parent.mkdir(exist_ok=True)
    for path, obj in ((OUT_DEFS, out), (OUT_THES, thes)):
        blob = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(blob)
        print(f"{path.name}: {len(obj):,} words, "
              f"{path.stat().st_size / 1e6:.1f} MB "
              f"({len(blob) / 1e6:.1f} MB raw)")
    print(f"({sum(1 for v in out.values() if 'of' in v):,} inflection pointers)")


if __name__ == "__main__":
    main()
