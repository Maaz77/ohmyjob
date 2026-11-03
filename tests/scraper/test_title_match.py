"""Title matching (plan 7.7): Unicode tokens, lemmas per language, aliases, the software preset."""
import json
from pathlib import Path

import pytest

import scrape_core as C

PRESET = json.loads((Path(__file__).parents[2] / "templates" / "presets" / "software.json").read_text(encoding="utf-8"))


def matcher(terms, languages=("en",), mode="strict", preset=True, **extra):
    aliases = dict(PRESET["title_aliases"]) if preset else {}
    only = dict(PRESET["title_only_aliases"]) if preset else {}
    aliases.update(extra.get("title_aliases", {}))
    only.update(extra.get("title_only_aliases", {}))
    return C.TitleMatcher(terms, languages, mode, aliases, only)


def test_tokens_fold_accents_and_keep_any_script():
    assert C.tokens("Ärztin für Straße (m/w/d)") == ("arztin", "fur", "strasse", "m", "w", "d")
    assert C.tokens("C++ / C# Entwickler") == ("c++", "c#", "entwickler")
    assert C.tokens("工程师 Engineer") == ("工程师", "engineer")


def test_every_term_word_must_be_in_the_title():
    m = matcher(["Operations Analyst"], preset=False)
    assert m.matches("Senior Operations Analyst (m/f/d)")
    assert m.matches("Analyst, Operations")
    assert not m.matches("Operations Manager")
    assert not m.matches("Financial Analyst")


def test_lemmas_follow_languages():
    assert matcher(["Software Engineer"], preset=False).matches("Software Engineers")
    german = matcher(["Ingenieur"], ("de",), preset=False)
    assert german.matches("Ingenieure Maschinenbau")
    assert not matcher(["Ingenieur"], ("en",), preset=False).matches("Ingenieure Maschinenbau")


def test_title_filter_off_accepts_everything():
    assert matcher(["Software Engineer"], mode="off").matches("Head Chef")


def test_aliases_work_on_both_sides_and_title_only_aliases_one_way():
    m = matcher(["Operations Analyst"], preset=False, title_aliases={"ops": "operations"})
    assert m.matches("Ops Analyst")
    assert matcher(["Ops Analyst"], preset=False, title_aliases={"ops": "operations"}).matches("Operations Analyst")
    one_way = matcher(["Project Coordinator"], preset=False, title_only_aliases={"manager": "coordinator"})
    assert one_way.matches("Project Manager")
    assert not matcher(["Project Manager"], preset=False, title_only_aliases={"manager": "coordinator"}).matches("Project Coordinator")


def test_alias_table_cannot_explode():
    loop = {chr(97 + i) * 2: [chr(97 + (i + 1) % 20) * 2, chr(97 + (i + 2) % 20) * 2] for i in range(20)}
    m = C.TitleMatcher(["aa"], ("en",), "strict", loop, {})
    m.matches("aa bb cc dd ee ff gg hh")  # returns, whatever the answer


# (term, title, expected) run against templates/presets/software.json
SOFTWARE_VECTORS = [
    ("Software Engineer", "Senior Software Engineer (m/f/d)", True),
    ("Software Engineer", "SWE II, Payments", True),
    ("Software Engineer", "SDE 2", True),
    ("Software Developer", "Software Engineer", True),
    ("Software Engineer", "Software Developer", True),
    ("Software Engineer", "Programmer", False),
    ("Software Engineer", "Sw Engineer", True),
    ("Software Engineer", "Sw Eng", True),
    ("Software Engineer", "Mechanical Engineer", False),
    ("Frontend Developer", "Front-End Engineer", True),
    ("Frontend Developer", "Front End Developer (React)", True),
    ("Front End Developer", "Frontend Dev", True),
    ("Backend Engineer", "Back-end Developer", True),
    ("Full Stack Developer", "Fullstack Engineer", True),
    ("Fullstack Developer", "Full-Stack Software Engineer", True),
    ("Machine Learning Operations Analyst", "ML Operations Analyst", True),
    ("ML Operations Analyst", "Machine Learning Operations Analyst", True),
    ("Machine Learning Operations Analyst", "Marketing Operations Analyst", False),
    ("AI Policy Analyst", "Artificial Intelligence Policy Analyst", True),
    ("Quality Assurance Engineer", "QA Engineer", True),
    ("QA Engineer", "Quality Assurance Engineer", True),
    ("Site Reliability Engineer", "SRE", True),
    ("DevOps Engineer", "Dev Ops Engineer", True),
    ("DevOps Engineer", "DevOps Developer", True),
    ("Database Administrator", "DBA", True),
    ("JavaScript Developer", "JS Developer", True),
    ("Typescript Engineer", "TS Engineer", True),
    ("Kubernetes Engineer", "K8s Engineer", True),
    ("Senior Software Engineer", "Sr. Software Engineer", True),
    ("Senior Software Engineer", "Junior Software Engineer", False),
    ("C++ Developer", "C Developer", False),
    ("C++ Developer", "C++ Engineer", True),
    ("Data Engineer", "Software Engineer", False),
]


@pytest.mark.parametrize("term,title,expected", SOFTWARE_VECTORS)
def test_software_preset_vectors(term, title, expected):
    assert matcher([term]).matches(title) is expected


def test_preset_is_valid_search_config(workspace):
    import workspace as W

    raw = json.loads((workspace / "ohmyjob.json").read_text(encoding="utf-8"))
    raw["search"].update(PRESET)
    assert W.validate_config(raw)["search"]["title_aliases"]["swe"] == "software engineer"
    assert set(PRESET) == {"title_aliases", "title_only_aliases"}
