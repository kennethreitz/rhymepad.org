"""Regression suite for RhymePad's rhyme detection.

Every case here came from a real verse that exposed a gap during
development — Lil Wayne ("6 Foot 7 Foot"), Eminem ("Godzilla"),
Kanye ("Power"), and assorted hand-rolled edge cases.
"""

from collections import defaultdict

import pytest

from app import Draft, analyze, lookup


def groups(text: str) -> list[set[str]]:
    """Rhyme groups as sets of (lowercased) highlighted spans."""
    res = analyze(Draft(text=text))
    by_gid = defaultdict(set)
    for t in res["tokens"]:
        by_gid[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    return list(by_gid.values())


def group_with(text: str, *words: str) -> set[str]:
    """The group containing words[0]; asserts all words share it."""
    for g in groups(text):
        if words[0].lower() in g:
            missing = {w.lower() for w in words} - g
            assert not missing, f"{missing} not grouped with {words[0]!r} in {g}"
            return g
    pytest.fail(f"{words[0]!r} not highlighted at all")


def highlighted(text: str) -> set[str]:
    gs = groups(text)
    return set().union(*gs) if gs else set()


def scheme(text: str) -> str:
    return analyze(Draft(text=text))["stanzas"][0]["scheme"]


# ---------------------------------------------------------------- perfect

def test_perfect_end_rhymes():
    text = ("I keep the cadence tucked beneath my tongue tonight\n"
            "the city hums in amber under fading light\n"
            "I trace a melody that never quite takes flight\n"
            "and let the silence answer everything I write")
    group_with(text, "tonight", "light", "flight", "write", "quite")
    assert scheme(text) == "aaaa"


def test_internal_perfect_rhymes():
    text = ("no time to waste, I climb in haste and find my place\n"
            "the rhythm shows, the river flows, wherever it goes")
    group_with(text, "time", "climb")
    group_with(text, "waste", "haste")
    group_with(text, "shows", "flows", "goes")


def test_say_weigh():
    group_with("say this weigh that", "say", "weigh")


def test_abab_scheme():
    assert scheme("the cat\nso blue\na hat\nso true") == "abab"


# ------------------------------------------------------------------ slant

def test_end_slant_joins_existing_perfect_group():
    # "time" (AY M) slant-joins the mind/find (AY N D) perfect group
    text = ("the placement of creation\n"
            "eternal precipice of mind\n"
            "where it goes\n"
            "i can't find the time")
    group_with(text, "mind", "find", "time")
    group_with(text, "placement", "creation")
    assert scheme(text) == "abcb"


def test_kanye_hours_power():
    text = ("The clock's ticking, I just count the hours\n"
            "Stop tripping, I'm tripping off the power")
    group_with(text, "hours", "power")
    group_with(text, "ticking", "tripping")
    # the multi-word pass catches the full phrase pair too
    group_with(text, "clock's ticking", "stop tripping")
    assert scheme(text) == "aa"


def test_reduced_vowel_merge_vodka_lasagna():
    # AA + schwa, with different reduced vowels — the Wayne chain
    text = ("vodka with a spritzer\n"
            "real Gs move in silence like lasagna")
    group_with(text, "vodka", "lasagna")


def test_consonance_bliss_exist():
    # vowel + first coda consonant (IH S), full codas differ;
    # "whisps" is OOV and needs the g2p model
    group_with("bliss whisps in darker nights exist",
               "bliss", "whisps", "exist")


# ------------------------------------------------- unknown words / repairs

def test_compound_split_heresay_neigh():
    text = ("the rhythm and the gotcha of the heresay\n"
            "neigh")
    group_with(text, "heresay", "neigh")


def test_multiword_orange_door_hinge_porage():
    text = ("an orange door hinge\n"
            "porage")
    group_with(text, "orange", "door hinge", "porage")


def test_cmu_variant_get_rhymes_with_hit():
    # CMU's secondary pronunciation of "get" is "git"
    group_with("take the hit\nthat's what you get", "hit", "get")


# ------------------------------------------------------------------ noise

def test_no_transitive_chaining():
    # peanuts rhymes with what's via one pronunciation (AH T S) and with
    # people via another (IY + schwa) — the two groups must stay separate
    text = ("So misunderstood, but what's a world without enigma?\n"
            "You niggas are gelatin, peanuts to an elephant\n"
            "Sleep is the cousin, what a fuckin' family picture\n"
            "People say I'm borderline crazy, sorta, kinda\n"
            "Woman of my dreams, I don't sleep, so I can't find her")
    whats = group_with(text, "what's", "peanuts")
    assert "sleep" not in whats and "people" not in whats
    sleep = group_with(text, "sleep", "people")
    assert "what's" not in sleep


def test_filler_words_not_highlighted_midline():
    text = ("I'm a monster and I'm heartless, I'm the king\n"
            "and I'm the sting")
    assert "i'm" not in highlighted(text)
    group_with(text, "king", "sting")


def test_em_counts_at_line_end_only():
    text = ("Fill 'em with the venom and eliminate 'em\n"
            "I told 'em owls fly, I Minute Maid 'em")
    assert scheme(text) == "aa"  # ...ate 'em / Maid 'em
    # but the mid-line 'em's stay dark
    res = analyze(Draft(text=text))
    em_tokens = [t for t in res["tokens"]
                 if res["lines"][t["l"]][t["s"]:t["e"]].lower() == "em"]
    assert all(t["end"] for t in em_tokens)


def test_repeated_word_midline_is_not_a_rhyme():
    assert highlighted("the fire burns the fire down") == set()


# ----------------------------------------------------------------- lookup

def test_lookup_rhymes():
    words = {w["word"] for w in lookup("light", mode="rhyme")["words"]}
    assert "night" in words and "tonight" in words
    assert "light" not in words


def test_lookup_near_rhymes_exclude_perfect():
    words = {w["word"] for w in lookup("hold", mode="near")["words"]}
    assert "home" in words      # shared vowel, different coda
    assert "gold" not in words  # perfect rhymes live in the other mode


# ------------------------------------------------------------------ shape

def test_stanzas_and_legend():
    res = analyze(Draft(text="the cat\nso blue\na hat\nso true"))
    (st,) = res["stanzas"]
    assert st["lines"] == [0, 1, 2, 3]
    a, b = st["legend"][0], st["legend"][1]
    assert a["ch"] == "a" and b["ch"] == "b"
    assert a["color"] is not None and b["color"] is not None
    assert a["color"] != b["color"]


def test_blank_lines_split_stanzas():
    res = analyze(Draft(text="the cat\na hat\n\nso blue\nso true"))
    assert [s["scheme"] for s in res["stanzas"]] == ["aa", "aa"]


# ------------------------------------------------------------------ meter

def meter_of(line: str) -> dict:
    res = analyze(Draft(text=line))
    return res["meter"][0]


def test_iambic_pentameter():
    m = meter_of("Shall I compare thee to a summer's day")
    assert m["syl"] == 10
    assert m["label"] == "iambic pentameter"


def test_trochaic_tetrameter():
    m = meter_of("Tyger Tyger burning bright")
    assert m["syl"] == 7  # catalectic — final unstressed syllable dropped
    assert m["label"] == "trochaic tetrameter"


def test_syllables_always_reported():
    m = meter_of("the city hums in amber under fading light")
    assert m["syl"] == 12
    assert m["stress"]
