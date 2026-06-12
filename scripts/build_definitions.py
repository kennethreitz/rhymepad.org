"""Distill the English Wiktionary extract (kaikki.org, CC BY-SA) into a
compact gloss table for RhymePad: data/definitions.json.gz, mapping each
CMUdict word to up to three short [pos, gloss] pairs. Words that are pure
inflections ("hums", "ran") get a single ["of", base] pointer instead, so
the runtime can show the base word's senses without a lemmatizer.

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
OUT = ROOT / "data" / "definitions.json.gz"

WORD_OK = re.compile(r"[a-z][a-z'-]*")
MAX_GLOSSES = 3      # per word, across all parts of speech
MAX_PER_POS = 2      # a noun-heavy word still shows its verb sense
MAX_LEN = 140        # chars per gloss
SKIP_TAGS = {"obsolete", "archaic", "dated", "misspelling", "rare",
             "no-gloss", "nonstandard", "pejorative", "offensive"}
SKIP_POS = {"name", "proverb", "phrase", "punct", "character", "symbol"}


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
    defs: dict[str, list] = {}     # word -> [[pos, gloss], ...]
    bases: dict[str, str] = {}     # pure-inflection word -> base word
    n_lines = 0

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
            pairs = defs.setdefault(w, [])
            for s in e.get("senses", []):
                tags = set(s.get("tags", []))
                if tags & SKIP_TAGS:
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
                gloss = trim(glosses[0])
                if (len(pairs) >= MAX_GLOSSES
                        or sum(1 for p, _ in pairs if p == pos) >= MAX_PER_POS
                        or any(g == gloss for _, g in pairs)):
                    continue
                pairs.append([pos, gloss])

    # inflections carry a pointer to their base, ahead of any senses of
    # their own ("ran" is mostly "run", despite its own yarn-winch noun)
    out = {}
    for w, pairs in defs.items():
        base = bases.get(w)
        if base and defs.get(base):
            out[w] = [["of", base]] + pairs
        elif pairs:
            out[w] = pairs

    OUT.parent.mkdir(exist_ok=True)
    blob = json.dumps(out, separators=(",", ":"), ensure_ascii=False)
    with gzip.open(OUT, "wt", encoding="utf-8", compresslevel=9) as f:
        f.write(blob)
    print(f"{len(out):,} words ({sum(1 for v in out.values() if v[0][0] == 'of'):,} inflection pointers)")
    print(f"{OUT} — {OUT.stat().st_size / 1e6:.1f} MB "
          f"({len(blob) / 1e6:.1f} MB raw)")


if __name__ == "__main__":
    main()
