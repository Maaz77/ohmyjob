"""The title-filter vectors of the first scraper, run against templates/presets/software.json.

The search terms, the seniority words and the title-term denylist below are the test's own config (the plugin ships
none of them: its lists are empty by default). The preset supplies the abbreviations and the bridges.
"""
import json
from pathlib import Path

import pytest

import scrape_core as C

PRESET = json.loads((Path(__file__).parents[2] / "templates" / "presets" / "software.json").read_text(encoding="utf-8"))

TERMS = ["AI Engineer", "Machine Learning Engineer", "Backend Engineer", "Computer Vision Engineer", "Data Scientist",
         "Software Engineer"]
LEVELS = ["staff", "principal", "manager", "lead", "intern", "internship", "apprenticeship"]
DENY = [
    ".net", "dotnet", "dot net", "c#", "csharp", "c sharp", "java", "spring boot", "springboot", "golang", "go lang",
    "rust", "go", "php", "laravel", "aws", "amazon web services", "gcp", "google cloud", "azure", "devops", "dev ops",
    "site reliability", "sre", "kafka", "kubernetes", "k8s", "databricks", "salesforce", "sfcc", "business intelligence",
    "bi", "performance", "localisation", "localization", "professional services", "network", "networking", "compiler",
    "data engineer", "product analytics", "product analyst", "product design", "product designer", "product owner",
    "product manager", "product management", "mobile", "android", "ios",
]
SEARCH = {"exclude_seniority_level": LEVELS, "filter_title_terms": DENY, **PRESET}


def reason(title, terms=TERMS, search=SEARCH):
    """Why a title is dropped ("level", "term", "title"), else None: the order scrape.run applies the rules in."""
    lemmas = C.Lemmas(["en"])
    why = C.Filters(search, lemmas).reason({"title": title})
    if why:
        return why
    matcher = C.TitleMatcher(terms, ["en"], "strict", search["title_aliases"], search["title_only_aliases"])
    return None if matcher.matches(title) else "title"


KEPT = [
    "Backend Engineer", "Engineer, Backend", "Senior Backend Engineer II", "Backend Developer", "BACKEND ENGINEER",
    "Senior Data Scientists", "ML Engineer", "Machine Learning Engineer", "Software Engineer",
    "Machine Learning Researcher", "Senior Applied Scientist, Search - NLP/GenAI",
    "Senior Machine Learning Scientist (Personalisation)", "Computer Science Expert (Remote)",
    "Senior Machine Learning Expert", "Research Scientist, Computer Vision MGenAI", "Python Developer",
    "Algorithms Engineer - Imaging Systems", "Foundation Model Engineer", "Forward Deployed Engineer",
    "Web Developer", "Artificial Intelligence (AI) Engineering Senior Consultant", "AI Enablement Builder (AI)",
    "AI Architect", "Back-End Developer", "Full Stack Developer", "Senior Full Stack Engineer",
    "Senior Fullstack Engineer", "Computer Engineering", "AI Solution Designer", "Senior AI Prototyping Officer",
    "LLM Engineer", "Engineer, LLMs", "Agentic Systems Engineer", "Neural Rendering Engineer",
    "Deep Learning Engineer", "Inference Engineer", "Senior MLOps Engineer - Edge", "Vision Systems Engineer",
    "Backend Engineer - Leadership Track",  # "Leadership" is not "lead"
    "Backend Engineer at Google",  # "Google" is not "go"
    "Software Engineer - JavaScript",  # "JavaScript" is not "java"
    "Backend Engineer Stage", "Frontend Engineer",  # "frontend" is a software word, as in the first scraper
]
DROPPED = [
    ("Staff Backend Engineer", "level"), ("principal backend engineer", "level"),
    ("Backend Engineering Manager", "level"), ("Backend Engineer Intern", "level"),
    ("Backend Engineer Internship", "level"), ("Backend Engineer Apprenticeship", "level"),
    ("Lead AI Engineer", "level"), ("Senior Lead Software Engineer - Java", "level"),
    ("Lead Software Engineer", "level"), ("Senior / Lead Data Scientist", "level"),
    ("Group Product Manager, Machine Learning", "level"),
    ("Software Engineer (Java)", "term"), ("Senior Software Engineer C# .Net", "term"),
    ("Backend Engineer (Rust)", "term"), ("Backend Engineer - Golang", "term"), ("Backend Engineer - Go", "term"),
    ("Software Engineer - Spring Boot", "term"), ("PHP Laravel Developer", "term"), ("Cloud Engineer (AWS)", "term"),
    ("AWS Professional Services", "term"), ("Senior Localisation Engineer", "term"),
    ("Senior DevOps Engineer – AWS", "term"), ("Kubernetes & AI Automation", "term"), ("Data Engineer", "term"),
    ("Kafka Senior Developer - Contract - Melbourne", "term"), ("Performance Engineer", "term"),
    ("Salesforce Commerce Cloud (SFCC) Architect", "term"), ("Senior BI Engineer", "term"),
    ("Professional Services Engineer", "term"), ("Senior Software Engineer (compiler focused)", "term"),
    ("System Development Engineer, Core Networking (Relocation support available)", "term"),
    ("Senior Data Engineer – Databricks", "term"), ("GCP Data Engineer", "term"),
    ("Senior Software Engineer - Azure", "term"), ("Site Reliability Engineer", "term"),
    ("Localization Engineer", "term"), ("Business Intelligence Engineer", "term"), ("Product Analytics Engineer", "term"),
    ("Senior Product Designer", "term"), ("Product Owner - AI Platform", "term"),
    ("Software Engineer, Product Management", "term"), ("Mobile Developer", "term"), ("Android Developer", "term"),
    ("Senior Mobile Software Engineer", "term"), ("iOS Engineer", "term"),
    ("Senior Software Engineer (PHP with Laravel Framework)", "term"),
    ("Backend Analyst", "title"), ("Penetration Tester", "title"),
    ("Senior Analytics Engineer", "title"), ("Actuarial Expert", "title"),
    ("Anaplan Hyperion Forecasting Data Consultant", "title"), ("Marketing Solutions Engineer", "title"),
    ("Defect/Integration Engineer", "title"), ("Senior Scientist", "title"), ("Senior Production Engineer", "title"),
    ("Sr. Cloud Operations Engineer", "title"), ("Senior Engineer", "title"),
    ("Agent Support Engineer", "title"),  # "agent" alone is no software word: it also names sales and support roles
    ("Transformer Design Engineer", "title"),  # electrical power far more often than ML
    ("Data Quality Engineer", "title"),
]


@pytest.mark.parametrize("title", KEPT)
def test_kept(title):
    assert reason(title) is None, title


@pytest.mark.parametrize("title,why", DROPPED)
def test_dropped(title, why):
    assert reason(title) == why, title


def test_staffing_is_not_staff_and_a_reordered_term_matches():
    assert reason("Staffing Engineer", ["Staffing Engineer"]) is None
    assert reason("Engineer, Backend", ["Backend Engineer"], NONE) is None


NONE = {"title_aliases": {}, "title_only_aliases": {}}


def test_the_lists_are_empty_by_default_so_nothing_is_dropped_by_level_or_term():
    for title in ("Staff Nurse", "Lead Software Engineer", "Software Engineer (Java)"):
        assert reason(title, ["Nurse", "Software Engineer"], {**PRESET}) in (None, "title")
    assert reason("Staff Nurse", ["Nurse"], NONE) is None
