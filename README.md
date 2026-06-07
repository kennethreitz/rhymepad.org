# RhymePad

A scratchpad for poets & rappers. Write in the pad; rhymes are detected
phonetically (CMU Pronouncing Dictionary) and color-coded as you type —
end rhymes, **internal rhymes**, and slant rhymes included. Comes with a
rhyme/synonym lookup, draggable stanza reordering, and synthesized beats
with adjustable tempo.

## Run

```console
$ uv run uvicorn app:app --reload
```

Then open <http://127.0.0.1:8000>.

## How rhyme detection works

- Every word is mapped to its CMU phonemes (`pronouncing`), with
  lyric-friendly fallbacks (`runnin'` → `running`, possessives, and a
  spelling heuristic for made-up words).
- **Perfect rhymes** share everything from the last stressed vowel on
  (`tonight` / `light` / `flight` → `AY T`). These are matched anywhere
  in a line — that's the internal rhyme detection.
- **Slant rhymes** share just the vowel sounds from the last stressed
  vowel (`hold` / `coal`). Applied to line endings that didn't find a
  perfect match.
- Same color = same sound. Underline = line-ending rhyme (the stanza's
  a/b/a/b scheme); soft glow only = internal rhyme.

## API

- `POST /api/analyze` `{"text": "..."}` → token spans, rhyme groups,
  per-stanza schemes
- `GET /api/lookup?word=light&mode=rhyme|near` → frequency-ranked
  rhymes / near rhymes, grouped by syllable count

Synonyms come from the free [Datamuse](https://www.datamuse.com/api/)
API, client-side.
