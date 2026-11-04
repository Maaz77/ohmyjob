"""The pure logic of the scraper: text normalization, title matching, company names, cross-board twins, filters and job.md.

No network, no workspace, no tracker here (scrape.py does those), so every rule is testable on plain values.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from functools import lru_cache
from typing import Any, Iterable, Optional

from rapidfuzz import fuzz

# --------------------------------------------------------------------------- text

_TOKEN_RE = re.compile(r"\w+[+#]*")  # "c++" and "c#" stay whole; any script, not only [a-z0-9]


def fold(text: Any) -> str:
    """Casefolded text without accents ("Ärztin" -> "arztin", "Straße" -> "strasse")."""
    text = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(c for c in text if not unicodedata.combining(c))


def tokens(text: Any) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(fold(text).replace("_", " ")))


def words(text: Any) -> tuple[str, ...]:
    """Like tokens, but accents kept (casefolded): simplemma needs "Fachkräfte" to find "Fachkraft"."""
    return tuple(_TOKEN_RE.findall(unicodedata.normalize("NFC", str(text or "").casefold()).replace("_", " ")))


def one_line(value: Any) -> str:
    return " ".join(str(value or "").split())


# --------------------------------------------------------------------------- title matching (plan 7.7)

ALIAS_LIMIT = 64  # variants of one title: a bad alias table must not explode


def _alias_rules(table: dict) -> list[tuple[tuple[str, ...], tuple[str, ...]]]:
    rules = []
    for word, target in (table or {}).items():
        for text in target if isinstance(target, list) else [target]:
            a, b = words(word), words(text)
            if a and b and tuple(map(fold, a)) != tuple(map(fold, b)):
                rules.append((a, b))
    return rules


# The built-in aliases: always on, no config key. The shipped software preset (templates/presets/software.json) plus the
# common abbreviations of any field. title aliases rewrite the search term and the title in both directions;
# title-only aliases rewrite the title only, one way.
PRESET_FILE = Path(__file__).resolve().parents[1] / "templates" / "presets" / "software.json"
COMMON_ABBREVIATIONS = {
    "ops": "operations", "mgr": "manager", "sr": "senior", "jr": "junior", "eng": "engineer", "dev": "developer",
    "assoc": "associate", "admin": "administrator", "asst": "assistant", "dir": "director", "exec": "executive",
    "rep": "representative", "hr": "human resources", "acct": "accountant", "mktg": "marketing",
}


@lru_cache(maxsize=1)
def builtin_aliases() -> tuple[dict, dict]:
    """(title_aliases, title_only_aliases): the preset file plus COMMON_ABBREVIATIONS."""
    preset = json.loads(PRESET_FILE.read_text(encoding="utf-8"))
    return {**COMMON_ABBREVIATIONS, **preset["title_aliases"]}, dict(preset["title_only_aliases"])


def _replace_all(seq: tuple[str, ...], old: tuple[str, ...], new: tuple[str, ...]) -> Iterable[tuple[str, ...]]:
    folded_old = tuple(map(fold, old))
    for i in range(len(seq) - len(old) + 1):
        if tuple(map(fold, seq[i:i + len(old)])) == folded_old:
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

            forms = {fold(word)}
            for lang in self.languages:  # the accented word first ("Fachkräfte"), then without accents (typed without them)
                forms.add(fold(simplemma.lemmatize(word, lang=lang)))
                forms.add(fold(simplemma.lemmatize(fold(word), lang=lang)))
            found = self._cache[word] = frozenset(forms)
        return found


class TitleMatcher:
    """Does a posting's title belong to one of the search terms?

    A term matches when every word of it is in the title, in any order, comparing lemmas (config `languages`). Before
    that, the built-in aliases (builtin_aliases) rewrite both the term and the title in both directions ({"ops":
    "operations"}: "Ops Analyst" meets the term "Operations Analyst" and the reverse), and the built-in title-only
    aliases rewrite the title only, one way ({"developer": ["engineer"]}). mode "off" accepts every title.
    """

    def __init__(self, terms: Iterable[str], languages: Iterable[str] = ("en",), mode: str = "strict"):
        self.mode = mode
        self.lemmas = Lemmas(languages)
        both = _alias_rules(builtin_aliases()[0])
        both += [(b, a) for a, b in both]
        self._both = both
        self._title_only = _alias_rules(builtin_aliases()[1])
        self._queries: list[tuple[str, ...]] = []
        for term in terms:
            self._queries += [q for q in _variants(words(term), both) if q]

    def matches(self, title: str) -> bool:
        if self.mode == "off":
            return True
        for variant in _variants(words(title), self._both + self._title_only):
            hay = [self.lemmas.forms(w) for w in variant]
            for query in self._queries:
                if all(any(self.lemmas.forms(q) & h for h in hay) for q in query):
                    return True
        return False


# --------------------------------------------------------------------------- company names

LEGAL_SUFFIXES = frozenset({
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "plc", "corp", "corporation", "co", "company",
    "group", "holding", "holdings", "gmbh", "mbh", "ag", "kg", "ug", "se", "sa", "sarl", "sas", "srl", "spa", "bv", "nv",
    "ab", "as", "asa", "aps", "oy", "oyj", "pty", "pte", "pvt", "sdn", "bhd", "kk",
})
COMPANY_TOKEN_FUZZ_THRESHOLD = 88


def company_tokens(name: Any) -> list[str]:
    """The words of a company name without accents, punctuation, HTML entities and legal forms ("Acme Pty. Ltd." -> acme).

    A name made of nothing but legal forms keeps them.
    """
    words = list(tokens(html.unescape(str(name or "")).replace(".", "")))
    kept = [w for w in words if w not in LEGAL_SUFFIXES]
    while kept and kept[0] == "the" and len(kept) > 1:
        kept.pop(0)
    return kept or words


def company_key(name: Any) -> str:
    return " ".join(company_tokens(name))


def company_matches_excluded(company: Any, excluded: Any) -> bool:
    """True if `company` is the excluded company: legal forms, a trailing country or division word ("Acme" matches
    "Acme Deutschland") and small spelling differences are tolerated, but the excluded name must be the START of the
    company name ("Apple" is not "Big Apple Recruiting"; a plain fuzzy score gets this wrong)."""
    if not isinstance(company, str) or not company or not isinstance(excluded, str) or not excluded:
        return False
    have, want = company_tokens(company), company_tokens(excluded)
    if not want or len(want) > len(have):
        return False
    return all(fuzz.ratio(c, e) >= COMPANY_TOKEN_FUZZ_THRESHOLD for c, e in zip(have, want))


def find_excluded_company_match(company: Any, excluded_companies: Optional[Iterable[str]]) -> Optional[str]:
    """The first entry of exclude_companies that company matches, else None."""
    for entry in excluded_companies or []:
        if company_matches_excluded(company, entry):
            return entry
    return None


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
    """exclude_seniority_level and filter_title_terms (both empty by default); exclude_companies is `excluded_company`.

    A seniority word is looked for in the title (whole words, after the built-in aliases rewrote it: "Sr." is "senior") and
    must equal the board's job_level as a whole value: "senior" does not drop LinkedIn's "Mid-Senior level".
    """

    def __init__(self, search: dict, lemmas: Lemmas):
        self.lemmas = lemmas
        self.exclude_companies = list(search.get("exclude_companies", []))
        self.seniority = [words(s) for s in search.get("exclude_seniority_level", []) if words(s)]
        self.title_terms = [words(s) for s in search.get("filter_title_terms", []) if words(s)]
        rules = _alias_rules(builtin_aliases()[0])
        self._rules = rules + [(b, a) for a, b in rules]

    @staticmethod
    def _has_phrase(hay: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
        """Whole words only, no lemmas: "Staffing" must not meet "staff", as the old \\b(staff)\\b pattern did not."""
        hay, phrase = tuple(map(fold, hay)), tuple(map(fold, phrase))
        return any(hay[i:i + len(phrase)] == phrase for i in range(len(hay) - len(phrase) + 1))

    def _in_title(self, title: tuple[str, ...], phrases: list) -> bool:
        return any(self._has_phrase(variant, p) for variant in _variants(title, self._rules) for p in phrases)

    def reason(self, job: dict) -> Optional[str]:
        """"level" or "term" when a posting is dropped, else None."""
        title, level = words(job.get("title")), words(job.get("job_level"))
        if self._in_title(title, self.seniority) or any(tuple(map(fold, level)) == tuple(map(fold, s)) for s in self.seniority):
            return "level"
        if self._in_title(title, self.title_terms):
            return "term"
        return None

    def excluded_company(self, company: Any) -> Optional[str]:
        return find_excluded_company_match(company, self.exclude_companies)


# --------------------------------------------------------------------------- cross-board twins

_GENDER_RE = re.compile(r"[\(\[]\s*[mwfdxihv]\s*(?:[/|*]\s*[mwfdxihv]\s*)+[\)\]]|\b[mwfdhv](?:/[mwfdxv]){1,2}\b", re.I)


def twin_title(title: Any) -> str:
    """The title for comparing: no "(m/f/d)" marks, accents, HTML entities or punctuation."""
    return " ".join(tokens(_GENDER_RE.sub(" ", html.unescape(str(title or "")))))


def twin_city(location: Any) -> str:
    """The first part of a location ("Berlin, BE, Germany" and "Berlin, Germany" are both "berlin")."""
    return " ".join(tokens(str(location or "").split(",")[0]))


def twin_key(company: Any, title: Any, location: Any) -> tuple[str, str, str]:
    return company_key(company), twin_title(title), twin_city(location)


def is_twin(a: tuple[str, str, str], b: tuple[str, str, str]) -> bool:
    """Same company and title, and the same city (an empty location on either side counts as the same)."""
    return bool(a[0]) and bool(a[1]) and a[0] == b[0] and a[1] == b[1] and (not a[2] or not b[2] or a[2] == b[2])


class TwinIndex:
    """Postings seen so far (earlier boards, tracker rows): `find` says whether a new one is a twin of one.

    The empty-location wildcard of is_twin holds between postings of this run only. An `exact` entry (a tracker row)
    needs the same city, so one row without a Location does not hide that company and title in every city.
    """

    def __init__(self) -> None:
        self._by_company: dict[str, list[tuple[tuple[str, str, str], bool]]] = {}

    def add(self, company: Any, title: Any, location: Any, exact: bool = False) -> None:
        key = twin_key(company, title, location)
        self._by_company.setdefault(key[0], []).append((key, exact))

    def find(self, company: Any, title: Any, location: Any) -> bool:
        key = twin_key(company, title, location)
        return any(is_twin(key, other) and (not exact or key[2] == other[2])
                   for other, exact in self._by_company.get(key[0], []))


TWIN_WINDOW_DAYS = 30  # a tracker row found this long ago (or as far back as recency_days looks, if longer) can have twins
# Rows the user acted on or decided never expire from the twin check: an employer's repost is not a new job.
KEPT_STATUSES = frozenset({"Submitted", "Pending Review", "Needs Attention", "Deleted", "Skipped-LowFit"})


KEPT_STATUSES_FOLDED = frozenset(x.casefold() for x in KEPT_STATUSES)  # a hand-typed "submitted" counts too


def twin_window(recency_days: int) -> Optional[int]:
    """Days a tracker row counts for the twin check: as far as the scrape looks, 30 at least; None (every row) for 0."""
    return None if recency_days == 0 else max(TWIN_WINDOW_DAYS, recency_days)


def recent_enough(found: Any, days: Optional[int], today: Optional[date] = None) -> bool:
    """A tracker row's Date Found is within `days`; a date that cannot be read counts as recent."""
    if days is None:
        return True
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

_MAX_FOLDER_NAME_BYTES = 200  # a path component is at most 255 bytes on macOS and Linux; the key prefix is short


def _path_part(text: Any) -> str:
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "", str(text or "").strip())
    return re.sub(r"\s+", "-", text) or "Unknown"


def folder_name(key: str, company: Any, title: Any) -> str:
    """<job_key>_<Company>_<Title>, like the folders the other tools read ("4399000001_Lumenfield-Logistics_Operations-Analyst")."""
    prefix = f"{key}_"
    rest = f"{_path_part(company)}_{_path_part(title)}".encode("utf-8")[: _MAX_FOLDER_NAME_BYTES - len(prefix)]
    return prefix + rest.decode("utf-8", errors="ignore")


_HEADING_RE = re.compile(r"(?m)^(#{1,4})(?=\s)")


def demote_headings(markdown: str) -> str:
    """Push every markdown heading two levels down.

    The pipeline reads job.md's "## 2. Job Description" up to the next heading of level 1 or 2 (aa_lib.md_section_body),
    so a description with its own "## Requirements" would reach the screening and resume workers cut off there. Lines
    are first split the way that reader splits them (str.splitlines: also \\r, \\x0b, \\x0c, \\x85, \\u2028 ...).
    """
    return _HEADING_RE.sub(r"##\1", "\n".join(markdown.splitlines()))


def _description(text: Any) -> str:
    return demote_headings(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(text or "")).strip())


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
        *([f"- Listed via: {one_line(job['listed_via'])}"] if job.get("listed_via") else []),
        f"- Date Found: {job['found']}",
        f"- Date Posted: {job.get('posted') or 'Not available'}",
        f"- Location: {one_line(job.get('location')) or 'Not specified'}",
        f"- Employment type: {job.get('employment') or 'Not specified'}",
        "- Match Score:", "- Matched Resume:", "",
        "## 2. Job Description", "", _description(job.get("description")) or "No description available.", "",
        "## 3. Match Reasoning and Discussions", "", "### Why Matched?", "", "### Gaps?", "",
    ]
    return "\n".join(lines)


_LINKEDIN_VIEW_RE = re.compile(r"^(https?://[^/?#]+)/jobs/view/(?:[^/?#]*-)?(\d+)(?=[/?#]|$)", re.I)


def canonical_linkedin(url: str) -> str:
    """Google's LinkedIn links end in a slug ("/jobs/view/data-analyst-at-acme-4399000001"): back to /jobs/view/<id>."""
    match = _LINKEDIN_VIEW_RE.match(url)
    return f"{match.group(1)}/jobs/view/{match.group(2)}" if match and "linkedin." in match.group(1).lower() else url
