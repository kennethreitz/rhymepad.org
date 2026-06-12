"""Distill the English Wiktionary extract (kaikki.org, CC BY-SA) into
RhymePad's lexicon, restricted to the CMUdict vocabulary:

data/definitions.json.gz — word -> {"d": [[pos, gloss], ...] (max 3),
    "n": uncapped sense count, "of": base} ("of" marks inflections, so
    "hums" can lead with "hum"'s senses without a lemmatizer).

data/thesaurus.json.gz — word -> {"syn"/"opp"/"broad"/"rel": [words]}
    from Wiktionary's curated links: synonyms, antonyms, hypernyms,
    and the hyponym/coordinate/derived/related cluster.

One-time, offline:

    curl -L -o build/kaikki-en.jsonl.gz \
        https://kaikki.org/dictionary/English/kaikki.org-dictionary-English.jsonl.gz
    uv run python scripts/build_definitions.py
"""

import gzip
import json
import re
from pathlib import Path

import pronouncing

ROOT = Path(__file__).parent.parent
SRC = ROOT / "build" / "kaikki-en.jsonl.gz"
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
# wiktextract link field -> thesaurus bucket, harvest cap per word
LINK_BUCKETS = [("synonyms", "syn", 60), ("antonyms", "opp", 20),
                ("hypernyms", "broad", 30), ("hyponyms", "rel", 60),
                ("coordinate_terms", "rel", 60), ("related", "rel", 60),
                ("derived", "rel", 60)]


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
    defs: dict[str, dict] = {}   # word -> {"d": pairs, "n": count}
    bases: dict[str, str] = {}   # inflection -> base word
    thes: dict[str, dict] = {}   # word -> {bucket: [words]}
    n_lines = 0

    def harvest_links(w, obj):
        for field, bucket, cap in LINK_BUCKETS:
            for item in obj.get(field, []):
                t = item.get("word", "").strip()
                if t == w or not LINK_OK.fullmatch(t):
                    continue
                got = thes.setdefault(w, {}).setdefault(bucket, [])
                if t not in got and len(got) < cap:
                    got.append(t)

    with gzip.open(SRC, "rt", encoding="utf-8") as f:
        for line in f:
            n_lines += 1
            if n_lines % 200_000 == 0:
                print(f"  …{n_lines:,} entries, {len(defs):,} words")
            e = json.loads(line)
            w = e.get("word", "")
            pos = e.get("pos", "")
            if (w not in vocab or pos in SKIP_POS
                    or not WORD_OK.fullmatch(w)):
                continue
            ent = defs.setdefault(w, {"d": [], "n": 0})
            harvest_links(w, e)
            for s in e.get("senses", []):
                tags = set(s.get("tags", []))
                if tags & SKIP_TAGS:
                    continue
                harvest_links(w, s)
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
