"""Early liveness check: is a posting still open? A script only: no model, no Chrome.

find_closed([(key, url), ...]) returns {key: code} for the postings that are POSITIVELY closed, and nothing for the
rest. A job is dropped on evidence only. Every doubt (network error, timeout, rate limit, sign-in wall, a redirect to
another site, a page that cannot be read, a host we stopped asking, the time budget used up) is "unknown", and the job
goes on as if it were open. The page text is data: only the fixed codes of CLOSED_TEXT ever leave this module.

Positive evidence of "closed", per host, and what each rule rests on:
  LinkedIn      HTTP 200 on the /jobs/view/ page itself and a `closed-job__flavor` element (the banner of a closed
                posting) whose own text starts with "No longer accepting applications". The phrase elsewhere on the page
                (a description that says "no longer accepting applications from agencies") is not evidence, and an HTTP
                status alone never is. Observed on real closed and open postings (tests/fixtures/liveness/linkedin_*_44*).
  Lever         HTTP 404 on a /<company>/<id> posting address. Observed: a made-up id on a public board answers 404 with
                "The job posting you're looking for might have closed, or it has been removed". No known closed posting.
  Greenhouse    a /jobs/<number> posting address that ends on a Greenhouse page with `?error=true`. Observed: a made-up id
                on a public board redirects to its board with `?error=true`. No known closed posting. (A board that
                redirects a missing id to the company's own site is "redirected away": unknown.)
  any host      HTTP 410 Gone (RFC 9110: the resource was removed for good). Documentation only, not observed.
Nothing else is evidence: no page text on any other host, no 404 elsewhere.

Politeness: one request at a time per host, a pause between two calls to one host, a stop for the host after
STOP_AFTER refusals in a row, at most MAX_HOSTS_AT_ONCE hosts in parallel, a hard time budget for the whole check.
The session and the headers are the scraper's own (JobSpy's LinkedIn session). A check is reused within a call (the same
address is fetched once); nothing is kept between runs.

Set OHMYJOB_NO_LIVENESS=1 to turn the check off (the tests do, so that no test asks a real site).
"""
from __future__ import annotations

import ipaddress
import os
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, NamedTuple
from urllib.parse import parse_qsl, urlsplit

from job_key import is_linkedin_url

KILL_SWITCH = "OHMYJOB_NO_LIVENESS"
TIMEOUT_SECONDS = 8          # one request
DEADLINE_SECONDS = 40        # the whole check; the masters run `start` through a Bash call that times out at 2 minutes
PAUSE_SECONDS = 1.0          # between two calls to one host
PAUSE_LINKEDIN_SECONDS = 1.5  # LinkedIn rate-limits
STOP_AFTER = 3               # refusals in a row, then the host is left alone for the rest of the call
MAX_HOSTS_AT_ONCE = 6
MAX_BODY_BYTES = 400_000

# The reason of every closed verdict, in words (the Notes entry and the run summary). Fixed text: nothing from the page.
CLOSED_TEXT = {
    "linkedin-closed-banner": "LinkedIn says it is no longer accepting applications",
    "http-410": "the posting address answers 410 Gone",
    "http-404-lever": "the Lever posting address answers 404",
    "greenhouse-closed-redirect": "Greenhouse sends the posting address to its board with an error",
}


class Reply(NamedTuple):
    status: int
    url: str   # the address after redirects
    body: str


class Verdict(NamedTuple):
    outcome: str  # "closed" | "open" | "unknown"
    code: str


def note(code: str, today: str) -> str:
    """The Notes entry of a job dropped as closed: says it was closed and the date of the check."""
    return f"Deleted {today}: posting closed (liveness check {today}: {CLOSED_TEXT[code]})"


# --------------------------------------------------------------------------- classify (pure)

_WALL_PATH_RE = re.compile(r"/(auth-?wall|log-?in|sign-?in|sign-?up|uas/login|checkpoint|sso)(/|$)")
_LINKEDIN_BANNER_RE = re.compile(
    r"<[a-z0-9]+\b[^>]*\bclass=\"[^\"]*\bclosed-job__flavor[\w-]*[^\"]*\"[^>]*>\s*([^<]{0,200})", re.I)
_LINKEDIN_PHRASE_RE = re.compile(r"no longer accepting applications", re.I)  # matched at the START of the banner's text
_LEVER_POSTING_RE = re.compile(r"^/[^/]+/[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}(/|$)", re.I)
_GREENHOUSE_POSTING_RE = re.compile(r"/jobs/\d+(/|$)")
_REFUSED = {"network-error", "sign-in-wall", "http-401", "http-403", "http-429", "http-999"}


def _registrable(host: str) -> str:
    return ".".join(host.lower().rstrip(".").split(".")[-2:])


def _refusal(verdict: Verdict) -> bool:
    return verdict.outcome == "unknown" and (verdict.code in _REFUSED or verdict.code.startswith("http-5"))


def classify(url: str, reply: Reply) -> Verdict:
    final = urlsplit(reply.url or url)
    host = (urlsplit(url).hostname or "").lower()
    final_host = (final.hostname or "").lower()
    if _registrable(final_host) != _registrable(host):
        return Verdict("unknown", "redirected-away")
    if _WALL_PATH_RE.search(final.path.lower()):
        return Verdict("unknown", "sign-in-wall")
    status = reply.status
    if is_linkedin_url(url):
        if status != 200:
            return Verdict("unknown", f"http-{status}")
        if not final.path.startswith("/jobs/view/"):
            return Verdict("unknown", "redirected-away")
        banner = (m.group(1) for m in _LINKEDIN_BANNER_RE.finditer(reply.body))
        if any(_LINKEDIN_PHRASE_RE.match(text.strip()) for text in banner):
            return Verdict("closed", "linkedin-closed-banner")
        return Verdict("open", "ok")
    if status == 410:
        return Verdict("closed", "http-410")
    if status == 404 and _registrable(host) == "lever.co" and _LEVER_POSTING_RE.match(urlsplit(url).path):
        return Verdict("closed", "http-404-lever")
    if status != 200:
        return Verdict("unknown", f"http-{status}")
    if (_registrable(host) == "greenhouse.io" and _GREENHOUSE_POSTING_RE.search(urlsplit(url).path)
            and not _GREENHOUSE_POSTING_RE.search(final.path) and ("error", "true") in parse_qsl(final.query)):
        return Verdict("closed", "greenhouse-closed-redirect")
    return Verdict("open", "ok")


# --------------------------------------------------------------------------- fetch

def fetch(url: str) -> Reply:
    """GET with the scraper's own session and headers (JobSpy's LinkedIn ones). Raises on a network error."""
    from jobspy.linkedin.constant import headers  # lazy: importing JobSpy takes a moment, a run without a check skips it
    from jobspy.util import RequestsRotating

    with RequestsRotating(clear_cookies=True) as session:
        session.headers.update(headers)
        response = session.get(url, timeout=TIMEOUT_SECONDS, stream=True)
        try:
            raw = response.raw.read(MAX_BODY_BYTES, decode_content=True)
        finally:
            response.close()
        return Reply(response.status_code, response.url, raw.decode(response.encoding or "utf-8", errors="replace"))


def _checkable(url: str) -> bool:
    """An http(s) address on a named site: never a file, an IP address or a local name (the address is job-page data)."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or "." not in host or parts.username or parts.password:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        return True


def _one(url: str) -> Verdict:
    try:
        return classify(url, fetch(url))
    except Exception:  # noqa: BLE001  (a timeout, a TLS error, a page that breaks the parser: all are doubt)
        return Verdict("unknown", "network-error")


def _run_host(host_linkedin: bool, items: list[tuple[str, str]], stop_at: float) -> dict[str, Verdict]:
    pause = PAUSE_LINKEDIN_SECONDS if host_linkedin else PAUSE_SECONDS
    seen: dict[str, Verdict] = {}
    out: dict[str, Verdict] = {}
    strikes, last = 0, None
    for key, url in items:
        if url not in seen:
            if strikes >= STOP_AFTER:
                seen[url] = Verdict("unknown", "host-stopped")
            else:
                if last is not None:
                    time.sleep(max(0.0, min(last + pause - time.monotonic(), stop_at - time.monotonic())))
                if time.monotonic() >= stop_at:
                    seen[url] = Verdict("unknown", "time-budget")
                else:
                    seen[url] = _one(url)
                    last = time.monotonic()
                    strikes = strikes + 1 if _refusal(seen[url]) else 0
        out[key] = seen[url]
    return out


def check(jobs: Iterable[tuple[str, str]]) -> dict[str, Verdict]:
    """A verdict for every (key, url). Never raises; a key it did not check is "unknown"."""
    jobs = [(key, url) for key, url in jobs]
    out = {key: Verdict("unknown", "not-checked") for key, _ in jobs}
    if os.environ.get(KILL_SWITCH):
        return out
    try:
        hosts: dict[str, list[tuple[str, str]]] = {}
        for key, url in jobs:
            if _checkable(url or ""):
                hosts.setdefault(urlsplit(url.strip()).hostname.lower(), []).append((key, url.strip()))
            else:
                out[key] = Verdict("unknown", "bad-url")
        stop_at = time.monotonic() + DEADLINE_SECONDS
        if hosts:
            with ThreadPoolExecutor(max_workers=min(MAX_HOSTS_AT_ONCE, len(hosts))) as pool:
                for done in pool.map(lambda h: _run_host(is_linkedin_url(f"https://{h[0]}/"), h[1], stop_at), hosts.items()):
                    out.update(done)
    except Exception:  # noqa: BLE001  (a check that cannot finish must not stop the run)
        pass
    return out


def find_closed(jobs: Iterable[tuple[str, str]]) -> dict[str, str]:
    """{key: code} of the postings that are positively closed (the code is a key of CLOSED_TEXT)."""
    return {key: v.code for key, v in check(jobs).items() if v.outcome == "closed"}


def delete_closed(tracker: Any, items: list[tuple[int, Any, str]], today: str) -> None:
    """Rows first (one save), folders second, never the reverse. items: (row _index, folder path, code).
    Raises when the tracker cannot be saved; nothing is deleted then."""
    for index, _folder, code in items:
        tracker.set_status(index, "Deleted", note(code, today))
    tracker.save()
    for _index, folder, _code in items:
        shutil.rmtree(folder, ignore_errors=True)
