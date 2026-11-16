import languages as L

PROFILE = """# Resume
- German: not a language line here.

# Languages

Guidance text: with a colon.

- **Deutsch:** native. Mother tongue.
- English: fluent, C1.
- fr: some
- Klingon: fluent.

# Work Authorization
- Spanish: no
"""


def test_profile_languages_reads_names_codes_and_ignores_unknown():
    assert L.profile_languages(PROFILE) == ({"de", "en", "fr"}, ["Klingon"])
    assert L.profile_languages("# Resume\n- English: fluent\n") == (set(), [])


def test_ok_keeps_unknown_empty_and_spoken():
    assert L.ok("", {"en"}) and L.ok(None, {"en"}) and L.ok("fr", set())
    assert L.ok(" PT-br ", {"pt"}) and L.ok("en", {"en"})
    assert not L.ok("de", {"en"})


def langs(*lines):
    return L.profile_languages("# Languages\n" + "\n".join(f"- {x}: fluent." for x in lines))


def test_names_match_as_whole_words_and_codes_only_when_alone():
    assert langs("Mandarin Chinese", "Brazilian Portuguese", "English (C1)") == ({"zh", "pt", "en"}, [])
    assert langs("de", "Indonesian") == ({"de", "id"}, [])
    assert langs("Norwegian")[0] == langs("no")[0] == langs("nn")[0] == {"nb"}
    assert langs("Farsi", "Tagalog") == ({"fa", "tl"}, [])
    assert langs("Italics") == (set(), ["Italics"])
