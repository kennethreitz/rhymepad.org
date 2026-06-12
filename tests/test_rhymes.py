"""Regression suite for RhymePad's rhyme detection.

Every case here came from a real verse that exposed a gap during
development — Lil Wayne ("6 Foot 7 Foot"), Eminem ("Godzilla"),
Kanye ("Power"), and assorted hand-rolled edge cases.
"""

from collections import defaultdict

import pytest

import rhymes
from app import Draft, analyze, lookup
from rhymes import word_data


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
    assert "people" not in whats
    assert not any("sleep" in w for w in whats)
    # the IY-x rhyme may surface as the word or as the «Sleep is» mosaic
    sleep = group_with(text, "people")
    assert any("sleep" in w for w in sleep)
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
    syrup = group_with(text, "syrup", "stir up")  # perfect ER-AH-P
    assert "burden" not in syrup  # ER-schwa with a mismatched coda — too loose
    cups = group_with(text, "cup", "up")
    assert "stir up" not in cups and "syrup" not in cups


def test_weak_phrase_attaches_but_never_founds():
    # "were up" (stopword tail) joins an existing ER group...
    text = ("Maybe your cup is full of syrup and lean\n"
            "fill the cup up to the brim\n"
            "shake the world up if it were up to me\n"
            "the burden is mine")
    group_with(text, "syrup", "were up")  # perfect ER-AH-P phrase match
    # ...but two weak phrases alone can't create a group
    assert "were up" not in highlighted("it were up to him\nit were up to her")


def test_bracket_and_hash_lines_ignored():
    text = ("[Chorus]\n"
            "the cat\n"
            "a hat\n"
            "# note: tighten this verse\n"
            "so blue\n"
            "so true")
    res = analyze(Draft(text=text))
    assert "chorus" not in highlighted(text)
    assert "note" not in highlighted(text)
    # [ and # lines don't split the stanza or earn scheme letters
    (st,) = res["stanzas"]
    assert st["lines"] == [1, 2, 4, 5]
    assert st["scheme"] == "aabb"


def test_paren_lines_are_lyrics():
    # ( lines are real lyrics now (MF DOOM style), not ignored ad-libs
    text = "the city bright tonight\n(shining all the light)"
    group_with(text, "tonight", "light")


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


def test_lookup_synonyms_wiktionary():
    data = lookup("happy", mode="syn")
    flat = {w["word"] for s in data["sections"] for w in s["words"]}
    assert "glad" in flat
    labels = [s["label"] for s in data["sections"]]
    assert "synonyms" in labels and "opposites" in labels


def test_lookup_synonyms_lemmatized():
    data = lookup("keys", mode="syn")  # inflection pointer: keys -> key
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
    # four/door/george keep their own thread (george is outside any
    # phrase), but inch/hinge are pure mirrors of the grouped phrases —
    # the compound reading wins and paints them orange
    group_with(text, "four", "door", "george")
    assert "hinge" not in highlighted(text)  # only «door hinge» paints


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


def test_near_vowel_neutralized_before_r():
    # CMU: fear = F IH1 R, hear = HH IY1 R — but they rhyme in every
    # dialect of English (the NEAR vowel)
    text = ("i fear the sounds above my head\n"
            "as i sit here calmly in bed\n"
            "i hear a rumble oh so loud\n"
            "sounds like mania and a crowd")
    group_with(text, "fear", "here", "hear")
    group_with(text, "head", "bed")
    group_with(text, "loud", "crowd")
    assert scheme(text) == "aabb"


def test_nasal_codas_merge():
    # Em delivers damn/hand/plans as one sound; M/N/NG share a coda
    # class — and the engine finds the even fuller mosaic
    text = ("If you never gave a damn, raise your hand\n"
            "'Cause I'm about to set trip, vacation plans")
    group_with(text, "gave a damn", "vacation plans")
    group_with(text, "hand", "plans")


def test_analyze_rejects_oversized_drafts():
    import pytest as _pytest
    from fastapi import HTTPException
    with _pytest.raises(HTTPException):
        analyze(Draft(text="a" * 200_000))


def test_weak_ending_rhymes_at_line_end():
    # Lateralus: stress sits on IN-fancy, but the weak final syllable
    # carries the rhyme at the line end
    text = ("All I see\n"
            "In my infancy\n"
            "Red and yellow then came to be\n"
            "Reaching out to me")
    group_with(text, "see", "infancy", "be", "me")


def test_cmu_override_stasis():
    # CMU transcribes stasis as "STAH-seez"; everyone says STAY-sis
    group_with("Oasis of stasis", "oasis", "stasis")


def test_weak_ending_requires_matching_coda():
    # divinity ends open (..tee); screams ends IY M Z — same vowel,
    # different coda, not the same family
    text = ("Roots surrounding our entirety\n"
            "Tangled in divinity\n"
            "Weaving heaven among hellish screams")
    fam = group_with(text, "entirety", "divinity")
    assert "screams" not in fam


def test_cot_caught_merger():
    # thought (AO T) and lot (AA T) are identical in merged dialects
    group_with("I thought about it a lot\nI gave it everything I got",
               "thought", "lot", "got")


def test_end_phrase_pure_vowel_rhyme():
    # forgotten / "off of": AA-schwa at both line ends — assonance is
    # allowed at the line boundary, like it is for single words
    text = ("I realize shit and write it down to be forgotten\n"
            "Building a database to reference my life off of")
    group_with(text, "forgotten", "off of")


def test_weak_endings_never_split_rich_chains():
    # the weak -er ending must not steal commissioner from the militia
    # family it rhymes with multisyllabically
    text = ("Ahem, excuse my charisma, vodka with a spritzer\n"
            "Swagger down pat; call my shit Patricia\n"
            "Young Money militia and I am the commissioner\n"
            "You no wan' start Weezy 'cause the F is for finisher")
    group_with(text, "charisma", "patricia", "militia",
               "commissioner", "finisher", "spritzer")


def test_schwa_heavy_phrase_cannot_join_word_family():
    # "Two bitches" (UW + schwas) must not ride the Buddha/scuba chain
    text = ("doin' it like Buddha, scuba, Cuba\n"
            "Two bitches at the same time")
    assert "two bitches" not in highlighted(text)


def test_swimmers_twist_her_finisher_one_family():
    text = ("Young Money militia and I am the commissioner\n"
            "You no wan' start Weezy cause the F is for finisher\n"
            "Two bitches at the same time, synchronized swimmers\n"
            "Got the girl twisted cause she open when you twist her\n"
            "Never met the bitch, but I fuck her like I missed her")
    group_with(text, "swimmers", "finisher", "commissioner",
               "twist her", "missed her")


def test_refrain_words_go_dark_midline():
    text = ("the bitch is cold\n"
            "the bitch is old\n"
            "a bitch like this\n"
            "a bitch like that\n"
            "bitch got gold")
    group_with(text, "cold", "old", "gold")
    assert "bitch" not in highlighted(text)


def test_consonance_is_local():
    # speech (line 1) and keep (line 9): a coda match eight lines apart
    # is not a rhyme
    filler = "la la la\n" * 7
    text = "I gave a dumber speech\n" + filler + "I always keep my word"
    assert "keep" not in highlighted(text)


def test_secondary_pronunciation_stays_local():
    # predicate's verb reading (pred-i-KATE) must not hand it to the
    # hook's "eight" far away when the noun rhymes with its neighbor
    filler = "nothing on this line\n" * 8
    text = ("Six-foot, seven-foot, eight-foot bunch\n" + filler +
            "You niggas are gelatin, peanuts to an elephant\n"
            "I got through that sentence like a subject and a predicate")
    group_with(text, "gelatin", "elephant", "predicate")


# -------------------------------------------------------------- new tools

def test_word_info():
    from app import word_info
    info = word_info(word="tonight")
    assert info["syl"] == 2 and info["stress"] == "01"
    assert info["rime"] == "AY T"


def test_word_inside_grouped_phrase_still_rhymes():
    # «all this time» rides a mosaic with «lost in my», but the word
    # time must still pair with mind at word level
    text = ("lost in my mind for a while\n"
            "wastin' all this time with style")
    group_with(text, "mind", "time")
    group_with(text, "while", "style")


def test_inline_adlibs_excluded():
    # (yeah) is delivery, not text — bunch keeps the line-ending slot
    text = ("Six-foot, seven-foot, eight-foot bunch (yeah)\n"
            "I roll with the gang, throw a punch (what)")
    assert scheme(text) == "aa"
    assert "yeah" not in highlighted(text)
    group_with(text, "bunch", "punch")


def test_alliteration_detected():
    res = analyze(Draft(text="Peter Piper picked a peck of pickled peppers"))
    words = {res["lines"][t["l"]][t["s"]:t["e"]].lower() for t in res["allit"]}
    assert {"peter", "piper", "picked", "peck", "pickled", "peppers"} <= words
    assert len({t["g"] for t in res["allit"]}) == 1


def test_alliteration_needs_three_and_locality():
    # two same-onset words far apart are coincidence, not craft
    res = analyze(Draft(text="big dog barks\nnothing here\nnothing there\nbright day"))
    assert res["allit"] == []


def test_word_senses():
    from app import word_info
    assert word_info(word="light")["senses"] >= 10


def test_definitions_from_wiktionary():
    # no WordNet, no network — glosses come from data/definitions.json.gz
    d = rhymes.definitions_for("light")
    assert d["word"] == "light" and len(d["defs"]) >= 2
    assert all(d2["pos"] and d2["gloss"] for d2 in d["defs"])


def test_definitions_inflection_resolves_to_base():
    # "ran" is mostly "run", despite its own obscure yarn-winch noun
    d = rhymes.definitions_for("ran")
    assert d["word"] == "run" and d["defs"]
    info = word_data("hums")
    assert info["def_of"] == "hum" and info["defs"]


def test_definitions_unknown_word_is_quiet():
    assert rhymes.definitions_for("asdfgh") == {"word": "asdfgh", "defs": []}


def test_definitions_cover_slang():
    # headwords admit anything Wiktionary tags as slang, however rare
    assert rhymes.definitions_for("yeet")["defs"]
    assert rhymes.definitions_for("rizz")["defs"]


def test_thesaurus_moby_layer_is_exhaustive():
    data = lookup("money", mode="syn")
    syn = next(s for s in data["sections"] if s["label"] == "synonyms")
    flat = {w["word"] for w in syn["words"]}
    assert len(flat) >= 40           # Moby bulk behind the curated links
    assert "bread" in flat           # and the slang survives up front


def test_lookup_describes_offline():
    data = lookup("night", mode="desc")
    flat = {w["word"] for w in data["words"]}
    assert data["known"] and {"sleepless", "starry"} & flat


def test_lookup_associations_offline():
    data = lookup("heart", mode="trig")
    flat = {w["word"] for w in data["words"]}
    assert data["known"] and {"attack", "beating", "chest"} & flat


def test_lookup_desc_unknown_word_is_quiet():
    assert lookup("xqzzqx", mode="desc")["known"] is False


def test_suggest_rhymes_know_the_draft():
    # in a draft about fire and smoke, "higher" candidates that echo the
    # scene (fire/pyre via "lit") lead the list, tagged with the link
    draft = "i lit a match to see her face\nand lost it in the fire"
    r = rhymes.suggest_data("higher", draft)
    fits = {d["word"]: d["fit"] for d in r["words"] if "fit" in d}
    assert "pyre" in fits
    assert r["words"][0].get("fit")  # fitting rhymes float first


def test_suggest_without_draft_is_plain_lookup():
    r = rhymes.suggest_data("higher", "")
    assert r["known"] and not any("fit" in d for d in r["words"])


def test_suggest_grades_by_echo_count():
    draft = ("the moon hung low above the harbor\nsmoke curled off the water\n"
             "i lit a match to see her face\nand lost it in the fire")
    r = rhymes.suggest_data("higher", draft)
    fitns = [d.get("fitn", 0) for d in r["words"]]
    assert fitns == sorted(fitns, reverse=True)  # more echoes lead
    assert fitns[0] >= 2


def test_suggest_context_reads_newest_first():
    ctx = rhymes.draft_context("dawn breaks early\nthe ember glows at midnight")
    assert ctx.index("midnight") < ctx.index("dawn")


def test_continuations_read_like_language():
    # "you will _find_" — the bigram table knows what comes next
    nx = rhymes.get_continuations()
    assert "find" in nx["will"]
    assert "life" in nx["my"]
    from app import follows
    assert follows(prev="WILL ")["words"] == nx["will"]


def test_suggest_multis_are_ghost_ready():
    r = rhymes.suggest_data("paper", "a draft about nothing much")
    assert r["multis"] and all(
        d["word"] and d["syl"] >= 1 for d in r["multis"])


def test_meaning_chips_chime_when_they_rhyme():
    # a synonym that also rhymes is the jackpot — it floats first, gold
    data = lookup("light", mode="syn")
    syn = next(s for s in data["sections"] if s["label"] == "synonyms")
    chimes = {w["word"] for w in syn["words"] if w.get("chime") == "perfect"}
    assert {"bright", "white"} & chimes
    assert syn["words"][0].get("chime")  # chiming words lead the section


def test_unanswered_endings_reported():
    res = analyze(Draft(text="the cat\nso blue\na hat\nthe end"))
    opens = {res["lines"][o["l"]][o["s"]:o["e"]] for o in res["open"]}
    assert "blue" in opens and "end" in opens
    assert "hat" not in opens and "cat" not in opens


def test_hook_coda_chain_fuses():
    # Logic's hook: wrist (IH S T) is the hub; this (IH S) and shit
    # (IH T) ride it as slant satellites — one color, kinda-rhymes kept
    text = ("Yeah I've been killin' this shit\n"
            "Yeah I've been hard in the paint, not a single assist\n"
            "Yeah I've been flickin' that wrist\n"
            "Yeah I've been cookin' that shit, now they fuckin' with this")
    group_with(text, "shit", "assist", "wrist", "this")


def test_vamonos_dominoes():
    # final s/z voicing neutralizes: -nos rhymes -noes
    text = ("my members go quicker than vamonos\n"
            "He dead, she dead, he in jail\n"
            "Everyone fallin' like dominoes")
    group_with(text, "vamonos", "dominoes")


def test_repeated_this_joins_the_hook_chain():
    # the unstressed CMU variant (DH IH0 S) used to found "this" on a
    # consonant-led key that the coda-nest fuse couldn't parse
    text = ("Yeah I've been killin' this shit\n"
            "not a single assist\n"
            "flickin' that wrist\n"
            "now they fuckin' with this\n"
            "killin' this shit\n"
            "not a single assist\n"
            "flickin' that wrist\n"
            "now they fuckin' with this")
    group_with(text, "shit", "assist", "wrist", "this")


def test_repetition_alone_does_not_color():
    text = ("Six-foot, seven-foot, eight-foot bunch\n"
            "Six-foot, seven-foot, eight-foot bunch")
    assert highlighted(text) == set()
    assert scheme(text) == "aa"  # ...but refrains share a scheme letter
    # and a repeated unrhymed ending isn't flagged "unanswered" either
    res = analyze(Draft(text=text))
    assert res["open"] == []


def test_repetition_colors_once_a_differing_word_joins():
    text = "it was Tammy\npure whammy\nstill Tammy"
    group_with(text, "tammy", "whammy")


def test_paren_words_highlight_but_never_end():
    # (justify greed) rhymes internally with need/breed, but the line's
    # ending slot belongs to "tragedies", outside the parens
    text = ("How can we still succeed taking what we don't need?\n"
            "Telling lies, alibis, selling all the hate that we breed,\n"
            "Super-size our tragedies (you can't define me, or justify greed),")
    group_with(text, "need", "breed", "greed", "succeed")
    res = analyze(Draft(text=text))
    ends = {res["lines"][t["l"]][t["s"]:t["e"]].lower()
            for t in res["tokens"] if t["end"] and not t["ph"]}
    assert "greed" not in ends


def test_end_dominated_vowel_families_fuse():
    # the Kanye chorus chain: blood/mud (AH D) and thugs/drugs (AH G Z)
    # are one AH family when both live at line ends
    text = ("Shower us with your love\n"
            "Wash us in the blood\n"
            "Drop this for the thugs\n"
            "Know I grew up in the mud\n"
            "The top is not enough\n"
            "No choice, sellin' drugs")
    group_with(text, "love", "blood", "thugs", "mud", "enough", "drugs")


def test_em_back_pocket_quintet_is_one_family():
    # AE-AA mosaics, plain AA endings, and IH-x-AA triples all share the
    # vowel tail — suffix fusion reads them as Em wrote them: one chain
    text = ("got a laptop in my back pocket\n"
            "My pen'll go off when I half-cock it\n"
            "Got a fat knot from that rap profit\n"
            "Made a livin' and a killin' off it\n"
            "Ever since Bill Clinton was still in office")
    group_with(text, "back pocket", "rap profit", "off it",
               "still in office", "laptop", "pocket", "profit", "office")
    assert scheme(text) == "aaaaa"


# ------------------------------------------------------------------ multis

def test_multis_generator():
    from app import multis_for
    ch = multis_for("charisma", set())
    assert "little" in ch  # IH-x skeleton, not a perfect rhyme
    el = multis_for("elevator", set())
    assert any(" " in m for m in el)  # two-word combos exist


def test_multis_in_lookup_response():
    data = lookup("placement", mode="rhyme")
    assert data["multis"] and "basement" not in data["multis"]  # perfects excluded


def test_forever_does_not_glue_to_sequential():
    # both carry EH-x, but forever ends open (.) and sequential closes
    # on L — and «forever (forever» phrase carpets obey refrain muting
    text = ("Diamonds are forever (forever, forever)\n"
            "Diamonds are forever (forever, forever, forever)\n"
            "Forever ever? Forever ever? Ever, ever?\n"
            "this is a question of writing style\n"
            "going the extra mile\n"
            "quintessential\n"
            "sequential")
    seq = group_with(text, "quintessential", "sequential")
    assert "forever" not in seq and "ever" not in seq
    assert "question" not in seq and "extra" not in seq
    group_with(text, "style", "mile")


def test_vowel_families_are_local():
    # a 100-line gap can't chain a vowel family — Baby/Daddy/loan/being
    # were one teal blob across all of "All Me"
    text = "Baby girl I see you\n" + ("filler line here\n" * 100) + "my old daddy\n"
    from collections import defaultdict
    res = analyze(Draft(text=text))
    bg = defaultdict(set)
    for tok in res["tokens"]:
        bg[tok["g"]].add(res["lines"][tok["l"]][tok["s"]:tok["e"]].lower())
    assert not any({"baby", "daddy"} <= s for s in bg.values())


def test_garbage_javascript_dont_attach():
    # both AA-x, but garbage ends JH and javascript ends PT — neither
    # nests with dollar/scholar's open coda, so they don't join
    text = ("Garbage collected greatness, a dollar-sign disease\n"
            "your JavaScript lost at sea, the notebook of the scholar")
    group_with(text, "dollar", "scholar")
    res = analyze(Draft(text=text))
    from collections import defaultdict
    bg = defaultdict(set)
    for t in res["tokens"]:
        bg[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    dfam = next(s for s in bg.values() if "dollar" in s)
    assert "garbage" not in dfam and "javascript" not in dfam


def test_weak_ending_needs_a_full_vowel():
    # middle and unavoidable share only a schwa+L tail — too weak to rhyme
    text = ("Wednesday has given up and admitted it's the middle\n"
            "slightly apologetic and entirely unavoidable")
    assert "middle" not in highlighted(text)
    # but a full-vowel feminine ending still pairs (infancy/see on IY)
    group_with("all I can see\nin my infancy", "see", "infancy")


def test_no_matching_across_stanzas():
    # each stanza is its own rhyme world: light/night in stanza 1 must
    # not group with bright/sight in stanza 2
    text = ("the city light\nshines every night\n\n"
            "the future's bright\nit's in my sight")
    res = analyze(Draft(text=text))
    from collections import defaultdict
    bg = defaultdict(set)
    for t in res["tokens"]:
        bg[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    s1 = next(s for s in bg.values() if "light" in s)
    assert "bright" not in s1 and "sight" not in s1
    # but within each stanza they still rhyme
    assert "night" in s1
    assert any({"bright", "sight"} <= s for s in bg.values())


def test_rhyme_char_start():
    from app import rhyme_char_start
    assert rhyme_char_start("tonight") == 3   # "ight"
    assert rhyme_char_start("write") == 2     # 'ite' (silent-e skipped)


def test_near_miss_radar():
    # two dead endings one vowel apart get flagged; true rhymes don't
    text = ("I raise my hand\nlook at the bond\nthe city sleeps\nthe ocean wakes")
    res = analyze(Draft(text=text))
    near = {res["lines"][t["l"]][t["s"]:t["e"]].lower() for t in res["near"]}
    assert near == {"hand", "bond"}


def test_consonance_only_on_final_syllable():
    # me-TIC-ulous's mid-word IH-K must not consonance-rhyme quick
    text = "a maverick imagine that I travel quick\nso meticulous and ridiculous"
    res = analyze(Draft(text=text))
    from collections import defaultdict
    bg = defaultdict(set)
    for t in res["tokens"]:
        bg[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    assert not any({"meticulous", "quick"} <= s for s in bg.values())
    # bliss/exist (final-syllable consonance) still works
    group_with("bliss whisps in the nights exist", "bliss", "exist")


def test_triple_not_dangling_on_stopword():
    # "smell like a" trails into an article; it shouldn't rhyme "myself why"
    text = ("should I listen to myself why\n"
            "it just don't smell like a")
    assert "smell like a" not in highlighted(text)


def test_repeated_word_at_line_ends_rhymes():
    # "again / again / again" at line ends is a monorhyme scheme — color it
    text = ("everything spins round again\n"
            "the messages flood back again\n"
            "my inner voice again")
    group_with(text, "again", "again")  # all three share a colored family
    # but mid-line repetition stays refrain, not a rhyme group
    assert "round" not in highlighted("round and round and round we go\nnothing else")


AUDIT_SHOULD = [
    ("orange", "door hinge"), ("benadryl", "many a pill"),
    ("antidote", "anecdote"), ("oblivious", "ridiculous"),
    ("shawty", "naughty"), ("finna", "dinner"), ("tryna", "designer"),
    ("skrrt", "hurt"), ("bruh", "duh"), ("thang", "bang"),
    ("Babolats", "cats"), ("Maybach", "way back"), ("Patek", "check"),
    ("Balmain", "ballgame"), ("guap", "drop"),
    ("confidence", "nonsense"),
    # the rhyme-type canon (poetrysoup taxonomy)
    ("immeasurable", "pleasurable"),   # perfect dactylic
    ("poet", "know it"),               # mosaic — the classic example
    ("spirit", "merit"),               # syllabic, front-vowel ladder
    ("trail", "railing"),              # trailing
    ("sun", "cunning"),                # semi-rhyme
    ("atone", "telephone"),            # light/imperfect
]
AUDIT_NOT = [
    ("garbage", "javascript"), ("middle", "unavoidable"),
    ("orange", "purple"), ("table", "people"), ("quick", "meticulous"),
    # forms the philosophy rejects: spelling, suffix-only, frame-only
    ("love", "move"),          # eye-rhyme
    ("nation", "fusion"),      # wrench (-tion alone)
    ("slaughter", "murder"),   # weak -er alone
    ("tell", "tall"),          # para-rhyme
]


def _pair_rhymes(a, b):
    res = analyze(Draft(text=f"I got the {a}\nyou know the {b}"))
    bg = defaultdict(set)
    for t in res["tokens"]:
        bg[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    return any(any(a.lower() in w for w in s) and any(b.lower() in w for w in s)
               for s in bg.values())


def test_audit_famous_rhymes():
    misses = [(a, b) for a, b in AUDIT_SHOULD if not _pair_rhymes(a, b)]
    assert not misses, f"audit misses: {misses}"


def test_audit_non_rhymes_stay_apart():
    fps = [(a, b) for a, b in AUDIT_NOT if _pair_rhymes(a, b)]
    assert not fps, f"audit false positives: {fps}"


def test_schwa_front_syllable_is_no_anchor():
    # a-FECT and e-SPE-cially share an unstressed schwa + EH — that's
    # not a rhyme a listener hears
    assert not _pair_rhymes("affect", "especially")


def test_unanswered_ends_bridge_nearby_stanzas():
    # evening -> dreaming -> meaning thread across three stanzas: each
    # is unanswered at home, so it may reach out
    text = ("especially on this evening\nI can't quite seem to think\n\n"
            "that I circle around\nwhat my mind is dreaming\n\n"
            "down on myself\nI don't find much meaning")
    group_with(text, "evening", "dreaming", "meaning")


def test_dactylic_endings_rhyme():
    # conSIDering / GATHering rhyme on their unstressed '-ering' tail
    text = "worth considering\nat any old dinner gathering"
    group_with(text, "considering", "gathering")


def test_same_sound_same_color_across_stanzas():
    # stanza-scoped families with the same founding sound share a color
    text = ("the city light\nshines every night\n\n"
            "the future's bright\nit's in my sight")
    res = analyze(Draft(text=text))
    gcolor = {g["id"]: g["color"] for g in res["groups"]}
    fams = defaultdict(set)
    for t in res["tokens"]:
        fams[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    gids = [g for g, s in fams.items() if s & {"light", "bright"}]
    assert len(gids) == 2          # still two separate families
    a, b = gids
    assert gcolor[a] == gcolor[b]  # ...wearing the same color


def test_neighbor_vowel_slant_thinking_dreaming():
    # IH and IY sit a hair apart; a shared unstressed tail carries them
    group_with("about thinking\nwhat my mind is dreaming",
               "thinking", "dreaming")
    # but single-vowel slants don't get the merger: bit/beat stay apart
    res = analyze(Draft(text="the bit\nthe beat"))
    assert not res["tokens"]


def test_ings_does_not_glue_to_ence():
    # offerings shares AA-x with confidence/nonsense, but its velar
    # '-ings' close is not their '-ence' close
    text = ("In what my brain offerings up\n"
            "It shouts with confidence\n"
            "all this is, is nonsense")
    fam = group_with(text, "confidence", "nonsense")
    assert "offerings" not in fam


def test_alliteration_tight_pair():
    res = analyze(Draft(text="sordid solutions on my mind"))
    al = {res["lines"][a["l"]][a["s"]:a["e"]].lower() for a in res["allit"]}
    assert {"sordid", "solutions"} <= al
    # but two small words side by side stay quiet
    res2 = analyze(Draft(text="big boy moves on my mind"))
    al2 = {res2["lines"][a["l"]][a["s"]:a["e"]].lower() for a in res2["allit"]}
    assert "big" not in al2


def test_stoptail_phrase_needs_coda_agreement():
    # «affect and» (ND tail) must not ride especially's open EH-x — but
    # poet / know it (T ~ T) still pairs
    res = analyze(Draft(text="Seem flat in affect and simpler\n"
                              "Especially on this evening"))
    assert not res["tokens"]


def test_accented_words_tokenize_whole():
    # Blasé must not become "Blas", naïve must not split into na + ve
    res = analyze(Draft(text="so blasé on this day\nnaïve in every way"))
    spans = {res["lines"][t["l"]][t["s"]:t["e"]].lower() for t in res["tokens"]}
    assert "blas" not in spans and "ve" not in spans
    group_with("so blasé on this day\nnaïve in every way", "day", "way")
    group_with("feeling blasé\nevery day", "blasé", "day")


def test_unstressed_schwa_is_no_assonance_key():
    # enjambment endings "the" / "and" must not group on bare schwa
    text = ("Compliment and compound like the\n"
            "middle of the poem goes here\n"
            "mingle with the long lost and")
    res = analyze(Draft(text=text))
    bg = defaultdict(set)
    for t in res["tokens"]:
        bg[t["g"]].add(res["lines"][t["l"]][t["s"]:t["e"]].lower())
    assert not any({"the", "and"} <= s for s in bg.values())
    # stressed AH keeps its assonance: blood / cup
    group_with("in my blood\nraise the cup", "blood", "cup")


def test_bare_vowel_slant_needs_matching_closure():
    # detail (liquid L) and brain (nasal N) share only EY — not a rhyme
    res = analyze(Draft(text="every detail\nin my brain"))
    assert not res["tokens"]
    # but time/mind (both nasal closures) still found their slant group
    group_with("i lost the time\nin my mind", "time", "mind")
