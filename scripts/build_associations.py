"""Build the server-side replacements for Datamuse's rel_jjb and rel_trg
from three open corpora — Tatoeba (CC BY, 2M sentences), the Gutenberg
Poetry corpus (public domain, 3M lines of verse — the register a rhyme
pad wants), and OpenSubtitles (capped at 30M deduped lines of dialogue):

data/describes.json.gz — noun -> [adjectives], from adjective-noun
    adjacency plus copular "noun is adjective" frames, ranked by
    salience (PMI damped by raw count), so "vast" beats "big".

data/associations.json.gz — word -> [trigger words], from windowed
    co-occurrence PMI: what a word summons, not what it means.

data/continuations.json.gz — word -> [words that commonly come next],
    raw bigram counts: the ghost ranks candidates that read like
    language after what's already on the line ("you will _find_").

data/trigrams.json.gz — "two words" -> [next], a second pass over the
    corpora restricted to frequent two-word contexts: idioms the
    bigram can't see ("from time to _time_", not "to _my_").

Adjective/noun identity comes from Wiktionary POS data (same extract as
the definitions build), not a tagger.

One-time, offline (after the build_definitions.py downloads):

    curl -L -o build/eng_sentences.tsv.bz2 \
        https://downloads.tatoeba.org/exports/per_language/eng/eng_sentences.tsv.bz2
    curl -L -o build/gutenberg-poetry.ndjson.gz \
        http://static.decontextualize.com/gutenberg-poetry-v001.ndjson.gz
    curl -L -o build/opensubs-en.txt.gz \
        https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2018/mono/en.txt.gz
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
SRC_POEM = ROOT / "build" / "gutenberg-poetry.ndjson.gz"
SRC_SUBS = ROOT / "build" / "opensubs-en.txt.gz"
OUT_DESC = ROOT / "data" / "describes.json.gz"
OUT_TRIG = ROOT / "data" / "associations.json.gz"
OUT_NEXT = ROOT / "data" / "continuations.json.gz"
OUT_TRI = ROOT / "data" / "trigrams.json.gz"
NEXT_TOP = 40       # continuations kept per word
NEXT_MIN = 5        # bigram count floor
NEXT_PREV_MIN = 50  # a word this rare can't anchor a phrase table
TRI_CTX_MIN = 300   # two-word contexts must be this common to qualify
TRI_TOP = 12
TRI_MIN = 10

WORD_OK = re.compile(r"[a-z][a-z']*")
TOKEN = re.compile(r"[A-Za-z']+")
WINDOW = 4        # co-occurrence span for associations
MIN_CO = 5        # pair floor before PMI is believable
MIN_DESC = 3
TOP = 25          # per headword
SUBS_CAP = 30_000_000   # subtitle lines to read (the corpus is ~440M)
PRUNE_EVERY = 5_000_000  # drop singleton pairs periodically: RAM bound
COPULA = {"is", "are", "was", "were", "seems", "seemed", "looks",
          "looked", "feels", "felt", "sounds", "smells", "tastes"}
# quantifiers and function words with vestigial adjective senses in
# Wiktionary ("on", "much") — never interesting as describers
DULL_ADJ = {"much", "more", "most", "many", "few", "fewer", "same",
            "such", "other", "own", "all", "whole", "last", "next",
            "several", "certain", "various", "due", "non", "one"}
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
    with gzip.open(SRC_POEM, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)["s"]
    if not SRC_SUBS.exists():
        print("  (no OpenSubtitles file — skipping)")
        return
    seen: set[int] = set()  # dialogue repeats endlessly; dedupe it
    n = 0
    with gzip.open(SRC_SUBS, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            h = hash(line)
            if h in seen:
                continue
            seen.add(h)
            n += 1
            if n > SUBS_CAP:
                return
            yield line


def tokens_of(sent: str) -> list[str]:
    out = []
    sent = sent.replace("’", "'")  # curly apostrophes: let’s -> let's
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
    nxt: dict[str, Counter] = defaultdict(Counter)    # word -> next-word counts
    uni: Counter = Counter()
    prev_tot: Counter = Counter()
    n_sent = 0

    zf = lru_cache(maxsize=None)(lambda t: zipf_frequency(t, "en"))

    def prune(table, label):
        before = sum(len(c) for c in table.values())
        for t in list(table):
            c = table[t]
            for u in [u for u, k in c.items() if k == 1]:
                del c[u]
            if not c:
                del table[t]
        print(f"  pruned {label}: {before:,} -> "
              f"{sum(len(c) for c in table.values()):,} pairs")

    for sent in sentences():
        n_sent += 1
        if n_sent % 500_000 == 0:
            print(f"  …{n_sent:,} sentences")
        if n_sent % PRUNE_EVERY == 0:
            prune(pair, "assoc")
            prune(desc, "desc")
            prune(nxt, "next")
        toks = tokens_of(sent)
        content = [t for t in toks if t not in STOP and len(t) > 1
                   and "'" not in t and zf(t) >= 2.0]
        for t in set(content):
            uni[t] += 1
        # continuations: raw adjacent bigrams, function words included —
        # "will find" is exactly the kind of pair this table is for
        for a, b in zip(toks, toks[1:]):
            if zf(a) >= 2.0 and zf(b) >= 2.0:
                nxt[a][b] += 1
                prev_tot[a] += 1
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

    out_next = {}
    for a, c in nxt.items():
        if prev_tot[a] < NEXT_PREV_MIN:
            continue
        keep = [b for b, k in c.most_common(NEXT_TOP) if k >= NEXT_MIN]
        if keep:
            out_next[a] = keep

    # pass 2: trigrams, but only for two-word contexts common enough to
    # mean something — the bigram counts above are the qualifier
    eligible = {(a, b) for a, c in nxt.items() for b, k in c.items()
                if k >= TRI_CTX_MIN}
    print(f"trigram pass: {len(eligible):,} eligible contexts")
    tri: dict[tuple, Counter] = defaultdict(Counter)
    n_sent = 0
    for sent in sentences():
        n_sent += 1
        if n_sent % 5_000_000 == 0:
            print(f"  …{n_sent:,} sentences (pass 2)")
            prune(tri, "tri")
        toks = tokens_of(sent)
        for a, b, c in zip(toks, toks[1:], toks[2:]):
            if (a, b) in eligible and zf(c) >= 2.0:
                tri[(a, b)][c] += 1
    out_tri = {}
    for (a, b), c in tri.items():
        keep = [w for w, k in c.most_common(TRI_TOP) if k >= TRI_MIN]
        if keep:
            out_tri[f"{a} {b}"] = keep

    OUT_DESC.parent.mkdir(exist_ok=True)
    for path, obj in ((OUT_DESC, out_desc), (OUT_TRIG, out_trig),
                      (OUT_NEXT, out_next), (OUT_TRI, out_tri)):
        blob = json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
        with gzip.open(path, "wt", encoding="utf-8", compresslevel=9) as f:
            f.write(blob)
        print(f"{path.name}: {len(obj):,} words, "
              f"{path.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
