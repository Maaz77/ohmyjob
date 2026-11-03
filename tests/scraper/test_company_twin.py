"""Company names, filters, cross-board twins, folder names and job.md."""
from datetime import date, timedelta

import pytest

import aa_lib as L
import scrape_core as C


@pytest.mark.parametrize("name,expected", [
    ("Acme Inc.", "acme"), ("Acme, LLC", "acme"), ("Acme Ltd", "acme"), ("Acme Limited", "acme"),
    ("Acme GmbH", "acme"), ("Acme GmbH & Co. KG", "acme"), ("Acme AG", "acme"), ("Acme S.A.", "acme"),
    ("Acme B.V.", "acme"), ("Acme N.V.", "acme"), ("Acme AB", "acme"), ("Acme AS", "acme"), ("Acme Oy", "acme"),
    ("Acme Pty Ltd", "acme"), ("Acme SAS", "acme"), ("Acme Corp.", "acme"), ("The Acme Group", "acme group"),
    ("Café Müller GmbH", "cafe muller"), ("AS", "as"), ("Ltd", "ltd"), ("Zoë  Systems", "zoe systems"),
])
def test_company_key_strips_legal_suffixes(name, expected):
    assert C.company_key(name) == expected


def test_twin_ignores_gender_marks_and_region_detail():
    a = C.twin_key("Acme GmbH", "Software Engineer (m/f/d)", "Berlin, BE, Germany")
    b = C.twin_key("ACME", "Software Engineer (w/m/x)", "Berlin, Germany")
    assert C.is_twin(a, b)
    assert not C.is_twin(a, C.twin_key("Acme", "Software Engineer", "Munich, Germany"))
    assert not C.is_twin(a, C.twin_key("Acme", "Senior Software Engineer", "Berlin"))
    assert not C.is_twin(a, C.twin_key("Other", "Software Engineer", "Berlin"))
    assert C.is_twin(a, C.twin_key("Acme", "Software Engineer", ""))  # no location: the rest decides
    assert not C.is_twin(C.twin_key("", "Engineer", "Berlin"), C.twin_key("", "Engineer", "Berlin"))


def test_twin_index_and_recent_rows():
    index = C.TwinIndex()
    index.add("Acme Inc", "Data Analyst", "Paris, France")
    assert index.find("Acme", "Data Analyst (m/f/d)", "Paris")
    assert not index.find("Acme", "Data Scientist", "Paris")
    today = date(2026, 11, 3)
    assert C.recent_enough("2026-10-20 09:00", 30, today)
    assert not C.recent_enough("2026-08-01 09:00", 30, today)
    assert C.recent_enough(today - timedelta(days=3), 30, today)
    assert C.recent_enough("not a date", 30, today)


def test_filters_are_empty_by_default_and_work():
    lemmas = C.Lemmas(["en"])
    none = C.Filters({}, lemmas)
    assert none.reason({"title": "Staff Nurse", "company": "X", "job_level": "Director"}) is None
    f = C.Filters({"exclude_companies": ["Example Staffing"], "exclude_seniority_level": ["director", "intern"],
                   "filter_title_terms": ["sales"]}, lemmas)
    assert f.reason({"title": "Analyst", "company": "Example Staffing GmbH"}) == "company"
    assert f.reason({"title": "Director of Operations", "company": "A"}) == "seniority"
    assert f.reason({"title": "Analyst", "company": "A", "job_level": "Internship"}) is None  # "intern" is not "internship"
    assert f.reason({"title": "Analyst Interns", "company": "A"}) == "seniority"
    assert f.reason({"title": "Sales Analyst", "company": "A"}) == "title term"
    assert f.reason({"title": "Salesforce Analyst", "company": "A"}) is None


def test_employment_filter():
    assert C.employment_ok("fulltime, contract", ["contract"])
    assert not C.employment_ok("internship", ["fulltime", "contract"])
    assert C.employment_ok("", ["fulltime"])  # a board that names no type
    assert C.employment_ok(float("nan"), ["fulltime"])
    assert C.employment_ok("internship", [])
    assert C.employment_label("fulltime, contract") == "Full-time, Contract"


def test_google_term_follows_recency():
    assert C.google_search_term("Data Analyst", "Berlin", 1) == "Data Analyst jobs near Berlin since yesterday"
    assert C.google_search_term("Data Analyst", "", 3).endswith("jobs in the last 3 days")
    assert C.google_search_term("X", "Y", 7).endswith("in the last week")
    assert C.google_search_term("X", "Y", 20).endswith("in the last month")
    assert C.google_search_term("X", "Y", 0) == "X jobs near Y"
    assert C.hours_old(2) == 48 and C.hours_old(0) is None


def test_folder_name_follows_the_fixture_pattern():
    assert C.folder_name("4399000001", "Lumenfield Logistics", "Operations Analyst") == "4399000001_Lumenfield-Logistics_Operations-Analyst"
    assert C.folder_name("9123", "Café / Ünï", "") == "9123_Cafe-Uni_Job"
    assert len(C.folder_name("9123", "x" * 90, "y" * 90)) <= 6 + 40 + 1 + 60


def test_job_md_is_read_by_the_other_tools():
    text = C.job_md({
        "title": "Data Analyst: Growth", "company": "Acme", "url": "https://x.example/jobs/1?id=2",
        "apply_url": "https://careers.acme.example/42", "source": "indeed", "found": "2026-11-03 09:00",
        "posted": "2026-11-02", "location": "Berlin, Germany", "employment": "Full-time",
        "description": "Intro\n# Not a section\n## Neither\nWhat you do:\n- a thing",
    })
    ov = L.job_overview(text)
    assert ov["Job Title"] == "Data Analyst: Growth" and ov["Job URL"] == "https://x.example/jobs/1?id=2"
    assert ov["Apply URL"] == "https://careers.acme.example/42" and ov["Source"] == "indeed"
    assert ov["Match Score"] == "" and ov["Matched Resume"] == "" and ov["Employment type"] == "Full-time"
    assert "# Not a section" not in [line.strip() for line in text.splitlines()]
    body = L.job_description(text)
    assert "What you do:" in body and "\\# Not a section" in body
    titles = [t for _l, t, _s, _e in L.md_sections(text)]
    assert titles == ["Job Discovery & Scoring", "1. Overview", "2. Job Description",
                      "3. Match Reasoning and Discussions", "Why Matched?", "Gaps?"]
    assert "- Apply URL:" not in C.job_md({"title": "t", "company": "c", "url": "https://u", "source": "s", "found": "f"})
