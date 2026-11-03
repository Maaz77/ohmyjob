"""The pure logic of the scraper: text normalization, title matching, company names, cross-board twins, filters and job.md.

No network, no workspace, no tracker here (scrape.py does those), so every rule is testable on plain values.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- text

_TOKEN_RE = re.compile(r"\w+[+#]*")  # "c++" and "c#" stay whole; any script, not only [a-z0-9]


def fold(text: Any) -> str:
    """Casefolded text without accents ("Ärztin" -> "arztin", "Straße" -> "strasse")."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def tokens(text: Any) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(fold(text).replace("_", " ")))


def one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


# --------------------------------------------------------------------------- title matching (plan 7.7)

ALIAS_LIMIT = 64  # variants of one title: a bad alias table must not explode


def _alias_rules(table: dict) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    rules = []
    for word, target in (table or {}).items():
        for text in target if isinstance(target, list) else [target]:
            a, b = tokens(word), tokens(text)
            if a and b and a != b:
                rules.append((a, b))
    return rules


def _replace_all(seq: tuple[str, ...], old: tuple[str, ...], new: tuple[str, ...]) -> Iterable[tuple[str, ...]]:
    for i in range(len(seq) - len(old) + 1):
        if seq[i:i + len(old)] == old:
            yield seq[:i] + new + seq[i + len(old):]


def _variants(seq: tuple[str, ...], rules: list) -> set[tuple[str, ...]]:
    seen = {seq}
    todo = [seq]
    while todo and len(seen) < ALIAS_LIMIT:
        current = todo.pop()
        for old, new in rules:
            for out in _replace_all(current, old, new):
                if out not in seen and len(seen) < ALIAS_LIMIT:
                    seen.add(out)
                    todo.append(out)
    return seen


class Lemmas:
    """The forms of a word: itself plus its simplemma lemma in each configured language."""

    def __init__(self, languages: Iterable[str]):
        self.languages = tuple(languages) or ("en",)
        self._cache: dict[str, frozenset] = {}

    def forms(self, word: str) -> frozenset:
        found = self._cache.get(word)
        if found is None:
            import simplemma

            forms = {word}
            for lang in self.languages:
                forms.add(fold(simplemma.lemmatize(word, lang=lang)))
            found = self._cache[word] = frozenset(forms)
        return found

    def contains(self, haystack: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
        """The phrase's words stand next to each other in haystack, each in any of its forms."""
        if not phrase:
            return False
        hay = [self.forms(w) for w in haystack]
        need = [self.forms(w) for w in phrase]
        return any(
            all(hay[i + j] & need[j] for j in range(len(need)))
            for i in range(len(hay) - len(need) + 1)
        )


class TitleMatcher:
    """Does a posting's title belong to one of the search terms?

    A term matches when every word of it is in the title, in any order, comparing lemmas (config `languages`). Before
    that, `title_aliases` rewrite both the term and the title in both directions ({"ops": "operations"}: "Ops Analyst"
    meets the term "Operations Analyst" and the reverse), and `title_only_aliases` rewrite the title only, one way
    ({"manager": "coordinator"}: "Project Manager" meets "Project Coordinator"). mode "off" accepts every title.
    """

    def __init__(self, terms: Iterable[str], languages: Iterable[str] = ("en",), mode: str = "strict",
                 title_aliases: Optional[dict] = None, title_only_aliases: Optional[dict] = None):
        self.mode = mode
        self.lemmas = Lemmas(languages)
        both = _alias_rules(title_aliases or {})
        both += [(b, a) for a, b in both]
        self._both = both
        self._title_only = _alias_rules(title_only_aliases or {})
        self._queries: list[tuple[str, ...]] = []
        for term in terms:
            self._queries += [q for q in _variants(tokens(term), both) if q]

    def matches(self, title: str) -> bool:
        if self.mode == "off":
            return True
        for variant in _variants(tokens(title), self._both + self._title_only):
            hay = [self.lemmas.forms(w) for w in variant]
            for query in self._queries:
                if all(any(self.lemmas.forms(q) & h for h in hay) for q in query):
                    return True
        return False


# --------------------------------------------------------------------------- company names

LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "plc", "corp", "corporation", "co",
    "gmbh", "mbh", "ag", "kg", "ug", "se", "sa", "sarl", "sas", "srl", "spa", "bv", "nv", "ab", "as", "asa", "aps",
    "oy", "oyj", "pty", "pte", "pvt", "sdn", "bhd", "kk",
})


def company_key(name: Any) -> str:
    """The company name for comparing: no accents, punctuation or legal form ("Acme Pty. Ltd." -> "acme")."""
    words = list(tokens(str(name or "").replace(".", "")))
    while words and words[0] == "the" and len(words) > 1:
        words.pop(0)
    while len(words) > 1 and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


# --------------------------------------------------------------------------- filters

_JOB_TYPE_LABELS = {
    "fulltime": "Full-time", "parttime": "Part-time", "contract": "Contract", "temporary": "Temporary",
    "internship": "Internship", "perdiem": "Per diem", "other": "Other", "volunteer": "Volunteer",
}


def job_types(value: Any) -> list[str]:
    """JobSpy's job_type cell ("fulltime, contract") as a list of its codes."""
    return [t.strip().lower() for t in str(value or "").split(",") if t.strip() and str(value) != "nan"]


def employment_label(value: Any) -> str:
    return ", ".join(_JOB_TYPE_LABELS.get(t, t.title()) for t in job_types(value))


def employment_ok(value: Any, wanted: Iterable[str]) -> bool:
    """True when `wanted` is empty (every type), the posting names no type (boards differ), or one of its types is wanted."""
    wanted = set(wanted)
    kinds = job_types(value)
    return not wanted or not kinds or bool(wanted & set(kinds))


class Filters:
    """exclude_companies, exclude_seniority_level and filter_title_terms (all empty by default)."""

    def __init__(self, search: dict, lemmas: Lemmas):
        self.lemmas = lemmas
        self.companies = {company_key(c) for c in search.get("exclude_companies", []) if company_key(c)}
        self.seniority = [tokens(s) for s in search.get("exclude_seniority_level", []) if tokens(s)]
        self.title_terms = [tokens(s) for s in search.get("filter_title_terms", []) if tokens(s)]

    def reason(self, job: dict) -> Optional[str]:
        """Why a posting is dropped, else None."""
        if company_key(job.get("company")) in self.companies:
            return "company"
        title, level = tokens(job.get("title")), tokens(job.get("job_level"))
        if any(self.lemmas.contains(title, s) or self.lemmas.contains(level, s) for s in self.seniority):
            return "seniority"
        if any(self.lemmas.contains(title, s) for s in self.title_terms):
            return "title term"
        return None


# --------------------------------------------------------------------------- cross-board twins

_GENDER_RE = re.compile(r"[\(\[]\s*[mwfdxi]\s*(?:[/|*]\s*[mwfdxi]\s*)+[\)\]]|\b[mwfdx](?:/[mwfdx]){1,2}\b", re.I)


def twin_title(title: Any) -> str:
    """The title for comparing: no "(m/f/d)" marks, accents or punctuation."""
    return " ".join(tokens(_GENDER_RE.sub(" ", str(title or ""))))


def twin_city(location: Any) -> str:
    """The first part of a location ("Berlin, BE, Germany" and "Berlin, Germany" are both "berlin")."""
    return " ".join(tokens(str(location or "").split(",")[0]))


def twin_key(company: Any, title: Any, location: Any) -> tuple[str, str, str]:
    return company_key(company), twin_title(title), twin_city(location)


def is_twin(a: tuple[str, str, str], b: tuple[str, str, str]) -> bool:
    """Same company and title, and the same city (an empty location on either side counts as the same)."""
    return bool(a[0]) and bool(a[1]) and a[0] == b[0] and a[1] == b[1] and (not a[2] or not b[2] or a[2] == b[2])


class TwinIndex:
    """Postings seen so far (earlier boards, recent tracker rows): `find` says whether a new one is a twin of one."""

    def __init__(self) -> None:
        self._by_company: dict[str, list[tuple[str, str, str]]] = {}

    def add(self, company: Any, title: Any, location: Any) -> None:
        key = twin_key(company, title, location)
        self._by_company.setdefault(key[0], []).append(key)

    def find(self, company: Any, title: Any, location: Any) -> bool:
        key = twin_key(company, title, location)
        return any(is_twin(key, other) for other in self._by_company.get(key[0], []))


def recent_enough(found: Any, days: int, today: Optional[date] = None) -> bool:
    """A tracker row's Date Found is within `days`; a date that cannot be read counts as recent."""
    today = today or date.today()
    if isinstance(found, datetime):
        found = found.date()
    if not isinstance(found, date):
        try:
            found = date.fromisoformat(str(found).strip()[:10])
        except ValueError:
            return True
    return today - found <= timedelta(days=days)


# --------------------------------------------------------------------------- search words for the boards

def google_search_term(term: str, location: str, recency_days: int) -> str:
    """JobSpy sends this text to Google as it is (and then adds nothing), so the time words are ours."""
    text = f"{term} jobs"
    if location:
        text += f" near {location}"
    if 0 < recency_days <= 1:
        text += " since yesterday"
    elif 0 < recency_days <= 3:
        text += " in the last 3 days"
    elif 0 < recency_days <= 7:
        text += " in the last week"
    elif recency_days > 7:
        text += " in the last month"
    return text


def hours_old(recency_days: int) -> Optional[int]:
    """recency_days as JobSpy's hours_old; 0 means no limit."""
    return recency_days * 24 if recency_days > 0 else None


# --------------------------------------------------------------------------- folder and job.md

def slug(text: Any, limit: int, fallback: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_text).strip("-")[:limit].strip("-") or fallback


def folder_name(key: str, company: Any, title: Any) -> str:
    """<job_key>_<Company>_<Title>, like the folders the other tools read ("4399000001_Lumenfield-Logistics_Operations-Analyst")."""
    return f"{key}_{slug(company, 40, 'Company')}_{slug(title, 60, 'Job')}"


def _description(text: Any) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).strip()
    # a "# ..." line inside the posting would start a new section of job.md
    return "\n".join(re.sub(r"^(\s{0,3})(#{1,6})(\s)", r"\1\\\2\3", line) for line in text.splitlines())


def job_md(job: dict) -> str:
    """The job.md of a new posting, in the shape the prep and apply tools read (plan 5.7).

    job keys: title company url apply_url source found posted location employment description (strings).
    The Match Score and Matched Resume lines stay empty: the screening fills them.
    """
    lines = [
        "# Job Discovery & Scoring", "", "## 1. Overview",
        f"- Job Title: {one_line(job['title'])}",
        f"- Company: {one_line(job['company'])}",
        f"- Job URL: {job['url']}",
    ]
    if job.get("apply_url"):
        lines.append(f"- Apply URL: {job['apply_url']}")
    lines += [
        f"- Source: {job['source']}",
        f"- Date Found: {job['found']}",
        f"- Date Posted: {job.get('posted', '')}",
        f"- Location: {one_line(job.get('location'))}",
        f"- Employment type: {job.get('employment', '')}",
        "- Match Score:", "- Matched Resume:", "",
        "## 2. Job Description", "", _description(job.get("description")) or "(the board gave no description)", "",
        "## 3. Match Reasoning and Discussions", "", "### Why Matched?", "", "### Gaps?", "",
    ]
    return "\n".join(lines)
