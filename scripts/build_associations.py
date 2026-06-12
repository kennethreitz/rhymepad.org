"""Build the server-side replacements for Datamuse's rel_jjb and rel_trg
from the Tatoeba English corpus (CC BY) — 2M sentences of plain English:

data/describes.json.gz — noun -> [adjectives], from adjective-noun
    adjacency plus copular "noun is adjective" frames, ranked by
    salience (PMI damped by raw count), so "vast" beats "big".

data/associations.json.gz — word -> [trigger words], from windowed
    co-occurrence PMI: what a word summons, not what it means.

Adjective/noun identity comes from Wiktionary POS data (same extract as
the definitions build), not a tagger.

One-time, offline (after the build_definitions.py downloads):

    curl -L -o build/eng_sentences.tsv.bz2 \
        https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2
    uv run python scripts/build_associations.py
"""

import bz2
import gzip
import json
import math
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pronouncing
from wordfreq import zipf_frequency

ROOT = Path(__file__).parent.parent
SRC_WIKT = ROOT / "build" / "kaikki-en.jsonl.gz"
SRC_SENT = ROOT / "build" / "eng_sentences.tsv.bz2"
OUT_DESC = ROOT / "data" / "describes.json.gz"
OUT_TRIG = ROOT / "data" / "associations.json.gz"

WORD_OK = re.compile(r"[a-z][a-z']*")
TOKEN = re.compile(r"[A-Za-z']+")
WINDOW = 4        # co-occurrence span for associations
MIN_CO = 3        # pair floor before PMI is believable
MIN_DESC = 2
TOP = 25          # per headword
COPULA = {"is", "are", "was", "were", "seems", "seemed", "looks",
          "looked", "feels", "felt", "sounds", "smells", "tastes"}
# quantifiers and function words with vestigial adjective senses in
# Wiktionary ("on", "much") — never interesting as describers
DULL_ADJ = {"much", "more", "most", "many", "few", "fewer", "same",
            "such", "other", "own", "all", "whole", "last", "next",
            "several", "certain", "various", "due", "non"}
STOP = set("""a an the and or but if then than so as of in on at to for
from by with about into over after under between out up down off is are
was were be been being am do does did done doing have has had having will
would can could shall should may might must not no nor this that these
those there here it its he she they them his her their my your our me you
i we us him who whom what which when where why how all any both each few
more most other some such only own same very too also just because while
during before against through don't doesn't didn't isn't aren't wasn't
won't can't couldn't shouldn't wouldn't i'm i've i'll you're it's he's
she's we're they're that's there's let's tom mary until till since past
around again always never ever still almost already soon really very
quite too enough lot like likes liked got get gets getting went goes
going come came comes take took taken make made makes put said say says
tell told know knew think thought want wanted wants need needs see saw
seen look looked let""".split())


def headwords() -> set[str]:
    pronouncing.init_cmu()
    return {w for w, _ in pronouncing.pronunciations
            if WORD_OK.fullmatch(w)}


def wikt_pos_sets() -> tuple[set[str], set[str]]:
    """Which words can be adjectives / nouns, per Wiktionary."""
    adj, noun = set(), set()
    with gzip.open(SRC_WIKT, "rt", encoding="utf-8") as f:
        for line in f:
            e = json.loads(line)
            w = e.get("word", "")
            if WORD_OK.fullmatch(w):
                if e.get("pos") == "adj":
                    adj.add(w)
                elif e.get("pos") == "noun":
                    noun.add(w)
    return adj, noun


def sentences():
    with bz2.open(SRC_SENT, "rt", encoding="utf-8") as f:
        for line in f:
            yield line.split("\t", 2)[-1]


def tokens_of(sent: str) -> list[str]:
    out = []
    for i, t in enumerate(TOKEN.findall(sent)):
        if i and t[0].isupper():
            continue  # mid-sentence capital = likely a name
        t = t.lower().strip("'")
        if WORD_OK.fullmatch(t):
            out.append(t)
    return out


def main():
    vocab = headwords()

    @lru_cache(maxsize=None)
    def headword(w: str) -> bool:
        return w in vocab or zipf_frequency(w, "en") >= 3.0

    print("POS sets from Wiktionary…")
    adj, noun = wikt_pos_sets()
    adj -= STOP | DULL_ADJ
    print(f"  {len(adj):,} adjectives, {len(noun):,} nouns")

    desc: dict[str, Counter] = defaultdict(Counter)   # noun -> adj counts
    adj_tot: Counter = Counter()
    noun_tot: Counter = Counter()
    pair: dict[str, Counter] = defaultdict(Counter)   # word -> co counts
    uni: Counter = Counter()
    n_sent = 0

    for sent in sentences():
        n_sent += 1
        if n_sent % 500_000 == 0:
            print(f"  …{n_sent:,} sentences")
        toks = tokens_of(sent)
        content = [t for t in toks if t not in STOP and len(t) > 1
                   and "'" not in t and zipf_frequency(t, "en") >= 2.0]
        for t in set(content):
            uni[t] += 1
        # describes: "vast ocean" and "the ocean is vast"
        for i, t in enumerate(toks):
            if t in adj and i + 1 < len(toks) and toks[i + 1] in noun \
                    and toks[i + 1] not in STOP:
                desc[toks[i + 1]][t] += 1
            if t in COPULA and 0 < i < len(toks) - 1 \
                    and toks[i - 1] in noun and toks[i - 1] not in STOP \
                    and toks[i + 1] in adj:
                desc[toks[i - 1]][toks[i + 1]] += 1
        # associations: windowed co-occurrence
        seen = set()
        for i, t in enumerate(content):
            for u in content[i + 1:i + 1 + WINDOW]:
                if u != t and (t, u) not in seen:
                    seen.add((t, u))
                    pair[t][u] += 1
                    pair[u][t] += 1

    for n, c in desc.items():
        noun_tot[n] = sum(c.values())
        for a, k in c.items():
            adj_tot[a] += k
    grand_desc = sum(noun_tot.values())
    n_uni = sum(uni.values())

    def kin(a, b):  # skip morphological echoes (ocean/oceans, run/running)
        return a[:4] == b[:4]

    out_desc = {}
    for nn, c in desc.items():
        if not headword(nn):
            continue
        scored = []
        for a, k in c.items():
            if k < MIN_DESC or kin(nn, a):
                continue
            pmi = math.log(k * grand_desc / (adj_tot[a] * noun_tot[nn]))
            if pmi > 0:
                scored.append((pmi * math.log1p(k), a))
        scored.sort(reverse=True)
        if scored:
            out_desc[nn] = [a for _, a in scored[:TOP]]

    out_trig = {}
    for t, c in pair.items():
        if not headword(t) or uni[t] < 20:
            continue
        scored = []
        for u, k in c.items():
            if k < MIN_CO or kin(t, u):
                continue
            pmi = math.log(k * n_uni / (uni[t] * uni[u]))
            if pmi > 1.5:
                scored.append((pmi * math.log1p(k), u))
        scored.sort(reverse=True)
        if scored:
            out_trig[t] = [u for _, u in scored[:TOP]]

    OUT_DESC.parent.mkdir(exist_ok=True)
    for path, obj in ((OUT_DESC, out_desc), (OUT_TRIG, out_trig)):
        blob = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(blob)
        print(f"{path.name}: {len(obj):,} words, "
              f"{path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
