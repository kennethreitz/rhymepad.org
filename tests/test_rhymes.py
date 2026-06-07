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


def test_trailing_schwa_trimmed_militia_commissioner():
    # commissioner has one extra reduced syllable (IH-AH-ER vs IH-AH)
    text = ("Swagger down pat, call my shit Patricia\n"
            "Young Money militia, and I am the commissioner")
    group_with(text, "patricia", "militia", "commissioner")


def test_meter_coaching_flags_outlier_line():
    text = ("I walk the lonely road tonight\n"
            "I hold the heavy stone of light\n"
            "I call the fading stars to fight\n"
            "and everybody wonders where the time has gone")
    res = analyze(Draft(text=text))
    flagged = [m["l"] for m in res["meter"] if m["off"]]
    assert flagged == [3]
    assert res["meter"][3]["target"] == 8


def test_meter_coaching_needs_a_pattern_to_break():
    # two lines of wildly different lengths: no dominant pattern, no flags
    res = analyze(Draft(text="short line here\na very much longer line that runs on and on"))
    assert not any(m["off"] for m in res["meter"])


# ------------------------------------------------------------------ layers

def test_layered_phrase_rides_second_group():
    # Em's syrup knot: "up" fills with the cup chain, while "stir up"
    # rides the syrup/burden group as a second layer
    text = ("Maybe your cup is full of syrup and lean\n"
            "fill the cup up to the brim\n"
            "Maybe I need to stir up shit\n"
            "the burden is mine")
    syrup = group_with(text, "syrup", "burden", "stir up")
    cups = group_with(text, "cup", "up")
    assert "stir up" not in cups and "syrup" not in cups


def test_weak_phrase_attaches_but_never_founds():
    # "were up" (stopword tail) joins an existing ER group...
    text = ("Maybe your cup is full of syrup and lean\n"
            "fill the cup up to the brim\n"
            "shake the world up if it were up to me\n"
            "the burden is mine")
    group_with(text, "syrup", "burden", "were up")
    # ...but two weak phrases alone can't create a group
    assert "were up" not in highlighted("it were up to him\nit were up to her")


def test_annotation_lines_ignored():
    text = ("[Chorus]\n"
            "the cat\n"
            "a hat\n"
            "# note: tighten this verse\n"
            "(yeah)\n"
            "so blue\n"
            "so true")
    res = analyze(Draft(text=text))
    assert "chorus" not in highlighted(text)
    assert "note" not in highlighted(text)
    # annotations don't split the stanza or earn scheme letters
    (st,) = res["stanzas"]
    assert st["lines"] == [1, 2, 5, 6]
    assert st["scheme"] == "aabb"


def test_perfect_subgroup_fuses_with_slant_family():
    # shoulder/older/colder (perfect, OW L D ER) live inside the bigger
    # OW-schwa family — one color for the whole column
    text = ("Skunk, bug, soldier\n"
            "Tongue, shrub, shoulder\n"
            "One month older\n"
            "Sponge, mob, colder\n"
            "Nun, rug, holster\n"
            "Lug nut, coaster\n"
            "Lung, jug, roaster\n"
            "Young Thug poster\n"
            "Unplugged toaster")
    group_with(text, "soldier", "shoulder", "older", "colder", "holster",
               "coaster", "roaster", "poster", "toaster")


def test_lookup_synonyms_wordnet():
    data = lookup("happy", mode="syn")
    flat = {w["word"] for s in data["sections"] for w in s["words"]}
    assert "glad" in flat
    labels = [s["label"] for s in data["sections"]]
    assert "synonyms" in labels and "opposites" in labels


def test_lookup_synonyms_lemmatized():
    data = lookup("keys", mode="syn")  # morphy: keys -> key
    assert data["known"] is True


def test_lookup_synonyms_unknown_word():
    assert lookup("xqzzqx", mode="syn")["known"] is False


def test_ine_ending_gets_een_candidate():
    # g2p reads OOV "-ine" like valentine (AY N); Em says ketamine with
    # an -een. Both candidates compete; the perfect pass picks the match.
    text = ("Even ketamine or methamphetamine with the minithin\n"
            "It better be at least seventy or three-hundred milligram")
    group_with(text, "ketamine", "methamphetamine")


def test_rhyme_mode_includes_near():
    data = lookup("hold", mode="rhyme")
    assert "gold" in {w["word"] for w in data["words"]}
    assert "home" in {w["word"] for w in data["near"]}


def test_schwa_phrase_needs_consonant_support():
    # «sloth hugs» (OW-TH + schwa) must not ride the over/shoulder family
    # just because the vowels rhyme — door hinge gets in on orange's R
    text = ("Looming over your shoulder, like a sloth hugs a tree,\n"
            "Thinking it won't fall, yet there it goes. Damn, it's free.")
    assert "sloth hugs" not in highlighted(text)


def test_phrase_with_one_new_half_is_not_suppressed():
    # though/mode already rhyme as words, but beast/sleep/seats only
    # rhyme through the phrase pairs — those must survive
    text = ("Look, I woke up in beast mode\n"
            "With my girl, that's beauty and the beast though\n"
            "Been top 5, these niggas sleep though\n"
            "Only thing that sold out is the seats though")
    group_with(text, "beast mode", "beast though", "sleep though",
               "seats though")


def test_phrase_joins_slant_group_via_coda_consensus():
    # orange + pourage found their group on vowels alone (end slant);
    # door hinge joins because 2+ members agree on the AO-R coda
    text = ("Pick up an orange\n"
            "It's by the door hinge\n"
            "eating pourage")
    group_with(text, "orange", "door hinge", "pourage")


def test_the_full_orange_verse():
    # the Eminem demonstration: every line ties back to orange
    text = ("I put my orange\nfour-inch\ndoor hinge\nin storage,\n"
            "and ate porridge with George")
    group_with(text, "orange", "storage", "porridge",
               "four-inch", "door hinge")
    group_with(text, "four", "door", "george")
    group_with(text, "inch", "hinge")


def test_mosaic_triples_kanye_power():
    text = ("I'm living in that 21st century\n"
            "Doing something mean to it\n"
            "Do it better than anybody you ever seen do it\n"
            "Screams from the haters, got a nice ring to it\n"
            "I guess every superhero need his theme music")
    group_with(text, "mean to it", "seen do it", "theme music")


def test_mosaic_finds_perfect_members_other_anchor():
    # Gambino: mastermind founds a perfect group with rind (AY N D), but
    # its first-stress run (AE..AY) is what "pass the time" rhymes with
    text = ("Gambino is a mastermind, fuck a bitch to pass the time\n"
            "Mass appeal, orange rind, smoke your green, I'm spendin' mine")
    group_with(text, "mastermind", "rind", "pass the time")


def test_eliot_is_it_visit():
    # Prufrock's signature mosaic: phone-for-phone identical rimes
    text = ('Oh, do not ask, "What is it?"\n'
            "Let us go and make our visit.")
    group_with(text, "is it", "visit")
    assert scheme(text) == "aa"
