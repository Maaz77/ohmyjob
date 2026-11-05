"""The title-filter vectors of the first scraper, run against templates/presets/software.json.

The search terms, the seniority words and the title-term denylist below are the test's own config (the plugin ships
none of them: its lists are empty by default). The preset supplies the abbreviations and the bridges.
"""
import contextlib
import json
from unittest import mock
from pathlib import Path

import pytest

import scrape_core as C

PRESET = json.loads((Path(__file__).parents[2] / "templates" / "presets" / "software.json").read_text(encoding="utf-8"))

TERMS = ["Robotics Engineer", "Graphics Engineer", "Platform Engineer", "Quality Assurance Engineer", "Backend Engineer",
         "Software Engineer"]
LEVELS = ["director", "manager", "head", "chief", "lead", "trainee", "traineeship", "cadet"]
DENY = [
    ".vb", "f#", "fsharp", "f sharp", "perl", "ruby", "ruby on rails", "rails", "scala", "haskell", "erlang", "elm", "qt",
    "sap", "oracle", "tableau", "mainframe", "cobol", "fortran", "wordpress", "drupal", "embedded", "firmware", "game",
    "unity", "unreal", "network", "networking", "technical writer", "technical writing", "product marketing",
    "support engineer",
]
SEARCH = {"exclude_seniority_level": LEVELS, "filter_title_terms": DENY}


def reason(title, terms=TERMS, search=SEARCH):
    """Why a title is dropped ("level", "term", "title"), else None: the order scrape.run applies the rules in."""
    lemmas = C.Lemmas(["en"])
    patch = mock.patch.object(C, "builtin_aliases", lambda: ({}, {})) if search is NONE else contextlib.nullcontext()
    with patch:  # NONE: no aliases at all; else the built-in ones (the software preset and common abbreviations)
        why = C.Filters(search, lemmas).reason({"title": title})
        if why:
            return why
        matcher = C.TitleMatcher(terms, ["en"], "strict")
    return None if matcher.matches(title) else "title"


KEPT = [
    "Backend Engineer", "Engineer, Backend", "Senior Backend Engineer II", "Backend Developer", "BACKEND ENGINEER",
    "Senior Robotics Engineers", "QA Engineer", "Quality Assurance Engineer", "Software Engineer",
    "Robotics Researcher", "Senior Applied Scientist, Perception - Graphics", "Senior Robotics Scientist (Motion Planning)",
    "Computer Science Expert (Remote)", "Senior Robotics Expert", "Research Scientist, Graphics Rendering",
    "Python Developer", "Algorithms Engineer - Imaging Systems", "Foundation Model Engineer", "Forward Deployed Engineer",
    "Web Developer", "Platform Engineering Senior Consultant", "Platform Enablement Builder", "Platform Architect",
    "Back-End Developer", "Full Stack Developer", "Senior Full Stack Engineer", "Senior Fullstack Engineer",
    "Computer Engineering", "Platform Solution Designer", "Senior Platform Prototyping Officer",
    "LLM Engineer", "Engineer, LLMs", "Agentic Systems Engineer", "Neural Rendering Engineer",
    "Deep Learning Engineer", "Inference Engineer", "Senior MLOps Engineer - Edge", "Vision Systems Engineer",
    "Backend Engineer - Leadership Track",  # "Leadership" is not "lead"
    "Platform Engineer, Headquarters",  # "Headquarters" is not "head"
    "Backend Engineer at Elmwood",  # "Elmwood" is not "elm"
    "Software Engineer - Scalability",  # "Scalability" is not "scala"
    "Frontend Engineer",  # "frontend" is a software word, as in the first scraper
]
DROPPED = [
    ("Director Backend Engineer", "level"), ("head backend engineer", "level"), ("Backend Engineering Manager", "level"),
    ("Backend Engineer Trainee", "level"), ("Backend Engineer Traineeship", "level"), ("Backend Engineer Cadet", "level"),
    ("Lead Robotics Engineer", "level"), ("Senior Lead Software Engineer - Perl", "level"),
    ("Lead Software Engineer", "level"), ("Senior / Lead Quality Assurance Engineer", "level"),
    ("Chief Platform Engineer", "level"), ("Group Platform Manager, Robotics", "level"),
    ("Software Engineer (Perl)", "term"), ("Senior Software Engineer F# .VB", "term"), ("Backend Engineer (Haskell)", "term"),
    ("Backend Engineer - Erlang", "term"), ("Backend Engineer - Elm", "term"), ("Software Engineer - Ruby on Rails", "term"),
    ("Rails Developer", "term"), ("Mainframe Platform Engineer", "term"), ("SAP Integration Platform Engineer", "term"),
    ("Oracle Database Platform Engineer", "term"), ("Tableau Platform Engineer", "term"), ("Qt Developer", "term"),
    ("Senior Software Engineer (Fortran)", "term"), ("Game Software Engineer", "term"), ("Unity Developer", "term"),
    ("Embedded Software Engineer", "term"), ("Senior Firmware Platform Engineer", "term"),
    ("Network Platform Engineer", "term"), ("System Platform Engineer, Core Networking (Hybrid)", "term"),
    ("Technical Writer, Platform", "term"), ("Product Marketing Engineer", "term"), ("WordPress Developer", "term"),
    ("Drupal Software Engineer", "term"), ("Platform Support Engineer", "term"), ("Software Engineer, Technical Writing", "term"),
    ("Backend Analyst", "title"), ("Penetration Tester", "title"), ("Senior Analytics Engineer", "title"),
    ("Rocketry Expert", "title"), ("Forecasting Data Consultant", "title"), ("Marketing Solutions Engineer", "title"),
    ("Defect/Integration Engineer", "title"), ("Senior Scientist", "title"), ("Senior Production Engineer", "title"),
    ("Sr. Cloud Operations Engineer", "title"), ("Senior Engineer", "title"),
    ("Agent Relations Engineer", "title"),  # "agent" alone is no software word: it also names sales and support roles
    ("Transformer Design Engineer", "title"),  # electrical power far more often than software
    ("Data Quality Engineer", "title"),
]


@pytest.mark.parametrize("title", KEPT)
def test_kept(title):
    assert reason(title) is None, title


@pytest.mark.parametrize("title,why", DROPPED)
def test_dropped(title, why):
    assert reason(title) == why, title


def test_exclusions_are_whole_words_and_a_reordered_term_matches():
    assert reason("Headless CMS Engineer", ["Headless CMS Engineer"]) is None
    assert reason("Engineer, Backend", ["Backend Engineer"], NONE) is None


NONE = {"no_aliases": True}


def test_the_lists_are_empty_by_default_so_nothing_is_dropped_by_level_or_term():
    for title in ("Head Nurse", "Lead Software Engineer", "Software Engineer (Perl)"):
        assert reason(title, ["Nurse", "Software Engineer"], {}) in (None, "title")
    assert reason("Head Nurse", ["Nurse"], NONE) is None
