"""freehire.me as a job source (search.sources "freehire"), next to the JobSpy boards.

freehire.me collects jobs from employers' own career pages and from job boards. Its public search API needs no key
(600 requests a minute per address, for personal use). search_freehire_jobs() runs one search per search term and
returns rows in the shape scrape.normalize() gives the JobSpy boards, so the filters, the folders and the tracker
treat all sources alike.

A row's address is the employer's own page for a career-site or ATS source, and the job board's page for a board source
(reed, ...).

Choices, and why:
- The country comes from the config's location (the string JobSpy gets), through JobSpy's own country table; when the
  location names none, the config's `country` is used.
- With `uk_sponsor_only` (off by default), a search in the UK only covers employers on the Home Office register of
  licensed visa sponsors (collections=uk-skilled-worker-sponsor).
- WhatJobs, Adzuna and JobLeads rows are left out: their text is cut to a snippet (about 500 characters; JobLeads
  about 1,100, its full text is members-only) and their address is an ad-network, redirect or aggregator page, not
  the employer's.
- No cap per term: every page is read, up to the API's limit of 10,000 results per search.
- The window is the jobs freehire first saw in the last N days; 0 means every open job.
- A job carries FreeHire's structured seniority label as its job_level, so exclude_seniority_level can drop it. A job
  without a label stays.
- The API never refuses a request it does not understand: it answers anyway and lists what it ignored in meta. Those
  lists are reported as warnings, since an ignored filter would otherwise look like a quiet day. The one thing they
  cannot catch is a renamed source in source_exclude (those names are not checked): _wanted_source guards that.
- A search that finds nothing reports one line of cheap probes that say why (a wider window, any employer).
- Nothing here raises: a failure is reported in the result's error, and the other sources carry on.
"""

import html
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple


from job_key import job_key, strip_tracking
from scrape_core import demote_headings

FREEHIRE_SITE = "freehire"
API_URL = "https://freehire.me/api/v1/agent/jobs/search"
PAGE_SIZE = 100  # the API maximum
MAX_RESULTS = 10_000  # the API answers 400 ("pagination too deep") when offset + limit goes above this
DEFAULT_OPEN_WITHIN_DAYS = 1  # 24 hours; jobs already in the tracker are skipped anyway
TIMEOUT_SECONDS = 30
PROBE_TIMEOUT_SECONDS = 10  # a probe only explains: a freehire that cannot answer in this time counts as not answering
PAGE_DELAY_SECONDS = 0.2  # 600 requests a minute are allowed; stay far under
MAX_FREEHIRE_WORKERS = 10
RETRY_WAIT_SECONDS = 2
MAX_RETRY_WAIT_SECONDS = 30
USER_AGENT = "ohmyjob"
UK_SPONSOR_COLLECTION = "uk-skilled-worker-sponsor"
SENIORITY_LABELS = ("intern", "junior", "middle", "senior", "lead", "staff", "principal", "c_level")  # freehire's closed list
EXCLUDED_SOURCES = ("adzuna", "jobleads")
EXCLUDED_SOURCE_PREFIX = "whatjobs"  # one source per country: whatjobs-uk, whatjobs-ie, ... and a global "whatjobs"
_JOB_TYPES = {"full_time": "fulltime", "part_time": "parttime", "contract": "contract"}
_UNWANTED_TYPES = {"internship", "fellowship"}  # a missing type is not unwanted: freehire fills it in later
# freehire's country codes (ISO 3166) for the country names JobSpy 1.4.1 accepts (workspace.COUNTRIES; a test pins the
# match), the config's aliases, and names people put in a location.
COUNTRY_CODES = {
    "argentina": "AR", "australia": "AU", "austria": "AT", "bahrain": "BH", "belgium": "BE", "brazil": "BR",
    "canada": "CA", "chile": "CL", "china": "CN", "colombia": "CO", "costa rica": "CR", "czech republic": "CZ",
    "denmark": "DK", "ecuador": "EC", "egypt": "EG", "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR",
    "hong kong": "HK", "hungary": "HU", "india": "IN", "indonesia": "ID", "ireland": "IE", "israel": "IL", "italy": "IT",
    "japan": "JP", "kuwait": "KW", "luxembourg": "LU", "malaysia": "MY", "mexico": "MX", "morocco": "MA",
    "netherlands": "NL", "new zealand": "NZ", "nigeria": "NG", "norway": "NO", "oman": "OM", "pakistan": "PK",
    "panama": "PA", "peru": "PE", "philippines": "PH", "poland": "PL", "portugal": "PT", "qatar": "QA", "romania": "RO",
    "saudi arabia": "SA", "singapore": "SG", "south africa": "ZA", "south korea": "KR", "spain": "ES", "sweden": "SE",
    "switzerland": "CH", "taiwan": "TW", "thailand": "TH", "türkiye": "TR", "ukraine": "UA",
    "united arab emirates": "AE", "uk": "GB", "usa": "US", "uruguay": "UY", "venezuela": "VE", "vietnam": "VN",
    # other spellings
    "czechia": "CZ", "turkey": "TR", "turkiye": "TR", "uae": "AE", "united kingdom": "GB", "us": "US",
    "united states": "US", "england": "GB", "scotland": "GB", "wales": "GB", "northern ireland": "GB",
    "great britain": "GB", "slovenia": "SI",
}


def freehire_country(location, country_name="") -> str | None:
    """The country code freehire uses for the country named in the config's location ("United Kingdom" -> "GB").

    "Paris, France" works too: the last comma-separated part that names a country wins. When the location names none,
    the config's `country` (a JobSpy country name) is tried.
    """
    found = _country_in(location)
    return found if found or not country_name else _country_in(country_name)


def _country_in(text) -> str | None:
    parts = [part.strip() for part in str(text or "").split(",") if part.strip()]
    for part in reversed(parts):
        code = COUNTRY_CODES.get(part.casefold())
        if code:
            return code
    return None


def _excluded_sources(country: str) -> str:
    # freehire names the UK's WhatJobs feed "whatjobs-uk", not "-gb". Sending only the searched country's feed keeps
    # the request short; _wanted_source drops any other WhatJobs row that still arrives.
    feed = "uk" if country == "GB" else country.lower()
    return ",".join((*EXCLUDED_SOURCES, EXCLUDED_SOURCE_PREFIX, f"{EXCLUDED_SOURCE_PREFIX}-{feed}"))


def _wanted_source(source) -> bool:
    source = source or ""
    return source not in EXCLUDED_SOURCES and not source.startswith(EXCLUDED_SOURCE_PREFIX)


def _params(
    term: str,
    country: str,
    open_within_days: int,
    offset: int,
    sponsors_only: bool = False,
) -> dict:
    params = {
        "q": 'title:"{}"'.format(term.replace('"', " ").strip()),
        "countries": country,
        "source_exclude": _excluded_sources(country),
        "description_format": "markdown",
        "sort": "created_at",
        "order": "desc",
        "limit": PAGE_SIZE,
        "offset": offset,
    }
    if open_within_days:
        params["open_within_days"] = open_within_days
    if country == "GB" and sponsors_only:
        params["collections"] = UK_SPONSOR_COLLECTION
    return params


def _retry_after(error: urllib.error.HTTPError) -> float:
    try:
        wait = float(error.headers.get("Retry-After", ""))
    except (TypeError, ValueError):
        return RETRY_WAIT_SECONDS
    return wait if wait >= 0 else RETRY_WAIT_SECONDS  # a negative or NaN value falls back to the default


def _get_json(url: str, timeout: float = TIMEOUT_SECONDS, attempts: int = 2):
    """One GET of the API, with one retry (attempts=2) after a 429, a 5xx or a network error."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            if attempt == attempts or error.code not in (429, 500, 502, 503, 504):
                raise
            wait = _retry_after(error)
        except (OSError, http.client.HTTPException):
            # a refused or timed-out connection (URLError, TimeoutError) and one dropped half way
            # (RemoteDisconnected, ConnectionResetError, IncompleteRead), which urllib does not wrap
            if attempt == attempts:
                raise
            wait = RETRY_WAIT_SECONDS
        time.sleep(min(wait, MAX_RETRY_WAIT_SECONDS))


def _fetch(params: dict, probe: bool = False) -> dict:
    """One GET of the search API. A probe (a request that only explains) waits less and is not retried. Tests replace this."""
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    return _get_json(url, PROBE_TIMEOUT_SECONDS, 1) if probe else _get_json(url)


def _term_pages(term: str, country: str, open_within_days: int, sponsors_only: bool, warn):
    """Every page of one search, newest first."""
    offset = 0
    while True:
        page = _fetch(_params(term, country, open_within_days, offset, sponsors_only))
        if "data" not in page:
            # raised, so that search_freehire_jobs logs it as SOURCE_FAILED and counts it: a changed API must not
            # look like a quiet day
            raise ValueError(f"no 'data' in the answer at offset {offset}: did the API change?")
        yield page
        items = page["data"] or []
        total = (page.get("meta") or {}).get("total", 0)
        offset += PAGE_SIZE
        if not items or offset >= total:
            return
        if offset + PAGE_SIZE > MAX_RESULTS:
            warn(f"term={term!r}: read {offset} of {total} results; the API stops at {MAX_RESULTS}")
            return
        time.sleep(PAGE_DELAY_SECONDS)


def _ignored_names(meta) -> list[str]:
    """What the API says it did not use from the request: "reality", "seniority=wizard"."""
    if not isinstance(meta, dict):
        return []
    names = []
    params = meta.get("ignored_params")
    for entry in (params if isinstance(params, list) else []):
        if isinstance(entry, dict) and entry.get("param"):
            hint = f" (did you mean {entry['did_you_mean']}?)" if entry.get("did_you_mean") else ""
            names.append(f"{entry['param']}{hint}")
    values = meta.get("ignored_values")
    for param, ignored in (values.items() if isinstance(values, dict) else ()):
        names.extend(f"{param}={value}" for value in (ignored if isinstance(ignored, list) else [ignored]))
    return names


def _report_ignored(meta, reported: set, warn) -> None:
    """Report (once per run and name) what the API ignored."""
    new = [name for name in _ignored_names(meta) if name not in reported]
    if not new:
        return
    reported.update(new)
    warn(f"the API did not use: {'; '.join(new)} (a filter we send was renamed or retired, so results are wider than asked)")


def _span(days: int) -> str:
    return f"{days}d" if days else "all"


def _total(term: str, country: str, open_within_days: int, sponsors_only: bool):
    """How many jobs a search matches (one request for one result), or None when freehire does not say. A failed
    request raises."""
    params = _params(term, country, open_within_days, 0, sponsors_only)
    params["limit"] = 1
    total = (_fetch(params, probe=True).get("meta") or {}).get("total")
    return total if isinstance(total, int) else None


def _explain_zero(term: str, country: str, open_within_days: int, sponsors_only: bool = False) -> tuple[str, bool]:
    """Why a search found nothing: what a wider window and, in the UK with uk_sponsor_only, any employer would have found. Returns
    (text, probes_worked). After the first failed request no more are sent: freehire is slow or down, and each
    request can take a minute."""
    working = True

    def count(window, only=sponsors_only):
        nonlocal working
        if not working:
            return "?"
        try:
            total = _total(term, country, window, only)
        except Exception:  # noqa: BLE001 - a probe only explains; it must never fail the search
            working = False
            return "?"
        return "?" if total is None else total

    wider = [window for window in (7, 30, 0) if open_within_days and (window == 0 or window > open_within_days)]

    def counts():
        return " ".join(f"{_span(window)}={count(window)}" for window in wider) or "nothing wider to try"

    head = f"window={_span(open_within_days)}"
    if country != "GB" or not sponsors_only:
        return f"{head} | {counts()}", working
    text = f"{head} | sponsors_only: {counts()}"
    anyone = count(open_within_days, only=False)
    return f"{text} | any_employer: {_span(open_within_days)}={anyone}", working


def _row(item: dict, country: str = "") -> dict | None:
    """One API result as a row for scrape.py, or None when it is not a usable job."""
    source = item.get("source") or ""
    if not _wanted_source(source):
        return None
    url = strip_tracking((item.get("url") or "").strip())
    key = job_key(url)
    title = html.unescape(item.get("title") or "").strip()
    company = html.unescape(item.get("company") or "").strip()
    enrichment = item.get("enrichment") or {}
    employment = enrichment.get("employment_type")
    # key.isdigit(): the folder prefix and tracker pairing need digits. An address that gives none (no address, or
    # a LinkedIn page without a job id) cannot be tracked.
    if not (title and company and key.isdigit()) or employment in _UNWANTED_TYPES:
        return None
    sponsor = country == "GB" and UK_SPONSOR_COLLECTION in (item.get("collections") or [])
    seniority = enrichment.get("seniority")
    return {
        "title": title,
        "company": company,
        "url": url,
        "apply_url": "",
        "source": FREEHIRE_SITE,
        "location": html.unescape(item.get("location") or "").strip(),
        "posted": (item.get("posted_at") or "")[:10],
        "job_type": _JOB_TYPES.get(employment) or "",
        "job_level": seniority.strip().lower() if isinstance(seniority, str) else "",
        "description": demote_headings(item.get("description") or ""),
        "listed_via": source,
        "note": f"Source: freehire/{source}" + ("; UK licensed sponsor" if sponsor else ""),
    }


class Result(NamedTuple):
    rows: list
    fetched: int  # results read, usable or not
    unusable: int  # results that cannot be tracked or read
    repeated: int  # jobs already met under another term
    error: str | None  # terms that failed (the rows read before the failure are kept)
    warnings: list


def search_freehire_jobs(
    search_terms: list[str],
    location: str,
    country_name: str = "",
    open_within_days: int = DEFAULT_OPEN_WITHIN_DAYS,
    uk_sponsor_only: bool = False,
) -> Result:
    """freehire jobs for every search term in the config's country (never raises)."""
    warnings: list[str] = []

    def warn(text: str) -> None:
        warnings.append(text)

    country = freehire_country(location, country_name)
    if country is None:
        warn(f"no country found in location {location!r} or country {country_name!r}; freehire skipped")
        return Result([], 0, 0, 0, None, warnings)
    rows, seen, reported = [], set(), set()

    def fetch_term(term):
        fetched = item_errors = unusable = 0
        failed = None
        term_rows = []
        term_metas = []
        try:
            for page in _term_pages(term, country, open_within_days, uk_sponsor_only, warn):
                term_metas.append(page.get("meta"))
                for item in page["data"] or []:
                    fetched += 1
                    try:
                        row = _row(item, country)
                    except Exception as error:  # noqa: BLE001 - one odd result must not cost the rest of the term
                        row = None
                        item_errors += 1
                        warn(f"term={term!r}: a result ({item.get('public_slug') if isinstance(item, dict) else None}) "
                             f"could not be read: {type(error).__name__}: {error}")
                    if row is None:
                        unusable += 1
                    else:
                        term_rows.append(row)
        except Exception as error:  # noqa: BLE001 - whatever goes wrong here must leave the other sources intact
            failed = f"term={term!r}: freehire failed after {fetched} results: {type(error).__name__}: {error}"
        return term, fetched, unusable, failed, term_rows, term_metas

    # Terms are independent; pagination for each term remains sequential so the
    # per-page delay and retry behavior continue to apply.
    with ThreadPoolExecutor(max_workers=max(1, min(len(search_terms), MAX_FREEHIRE_WORKERS))) as pool:
        term_results = list(pool.map(fetch_term, search_terms)) if search_terms else []

    errors, total_fetched, total_unusable, repeated = [], 0, 0, 0
    probing = True  # False once freehire has failed on us: no probes after that
    for term, fetched, unusable, failed, term_rows, term_metas in term_results:
        total_fetched += fetched
        total_unusable += unusable
        for meta in term_metas:
            _report_ignored(meta, reported, warn)
        for row in term_rows:
            key = job_key(row["url"])
            if key in seen:
                repeated += 1
            else:
                seen.add(key)
                rows.append(row)
        if failed:
            errors.append(failed)
            probing = False
        if not fetched and not failed and probing:
            line, probing = _explain_zero(term, country, open_within_days, uk_sponsor_only)
            warn(f"term={term!r}: nothing found | {line}")
    return Result(rows, total_fetched, total_unusable, repeated, "; ".join(errors) or None, warnings)
