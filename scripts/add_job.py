#!/usr/bin/env python3
"""Put postings the candidate found by hand into the pipeline (/ohmyjob:add-job): `ohmyjob add-job <url>... [--paste]`.

Each posting becomes what the scraper makes: Applications/<key>_<Company>_<Title>/job.md and a tracker row at "Found".
No search filter applies (the candidate chose the job); a company in exclude_companies only gets a WARNING line.
A posting is given as a web address (this script fetches the page: standard library only, no Chrome), as pasted text
(`--paste`: the postings are read from the inbox file that `--inbox` names, a JSON list of objects with the keys url,
text, company, title, location, apply_url; the file is deleted after reading), or both (the text is used and the page
is not fetched). A text without an address gets one from job_key.pasted_url, so its id is stable and reconcile pairs
it with its row like any other.

Nothing is created for a posting that is already known (id, or the same company, title and city as a row), whose page
cannot be read, whose description is empty or only boilerplate, or whose company or title is not known. Output: one
line per posting (`added`, `already known`, `unreadable`, `needs input`), `WARNING ...` lines, a `total:` line, and
`FATAL: ...` (exit 1). Exit 2: the run finished but some posting was unreadable or needs input.
"""
from __future__ import annotations

import argparse
import html
import http.client
import json
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

import scrape_core as C
import workspace as W
from job_key import job_key, pasted_url, strip_tracking
from scrape import Stop, known_keys, open_tracker, say
from tracker import TrackerError

EXIT_OK, EXIT_FATAL, EXIT_SOME_LEFT = 0, 1, 2
INBOX = "add-job.json"  # in .ohmyjob/: the postings of a --paste run, written by the skill
FETCH_TIMEOUT = 20
MAX_PAGE_BYTES = 3_000_000
USER_AGENT = "Mozilla/5.0 (compatible; ohmyjob add-job)"
# ponytail: a word count and a few English wall phrases decide "boilerplate only"; a smarter test when a real page slips through.
MIN_WORDS = 30  # fewer words than this is no posting
WALL_WORDS = 200  # a fetched page with fewer words than this and a wall phrase is a wall, not a posting
WALL_RE = re.compile(r"sign in|log in|enable javascript|javascript is (disabled|required)|captcha|verify you are human|"
                     r"access denied|are you a robot|join now|accept (all )?cookies", re.I)
LOGIN_URL_RE = re.compile(r"/(login|log-in|signin|sign-in|authwall|uas/login|checkpoint)\b", re.I)


class Unreadable(Exception):
    """The page cannot be read: the message says why."""


# --------------------------------------------------------------------------- reading a page

_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside"}
_BLOCK_TAGS = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}


class _Reader(HTMLParser):
    """The visible text of a page (no navigation, no scripts), its <title> and its ld+json blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.ld: list[str] = []
        self._skip = 0
        self._in_title = self._in_ld = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self.ld.append("")
            self._in_ld = True
        self._in_title = self._in_title or tag == "title"
        if tag in _BLOCK_TAGS:
            self.parts.append("\n- " if tag == "li" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "script":
            self._in_ld = False
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.ld[-1] += data
        elif self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        lines = [" ".join(line.split()) for line in "".join(self.parts).splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _read(markup: str) -> _Reader:
    reader = _Reader()
    reader.feed(markup)
    reader.close()
    return reader


def _s(value: Any) -> str:
    return C.one_line(value) if isinstance(value, str) else ""


def _job_postings(node: Any):
    if isinstance(node, list):
        for item in node:
            yield from _job_postings(item)
    elif isinstance(node, dict):
        kind = node.get("@type")
        if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
            yield node
        yield from _job_postings(node.get("@graph", []))


def _location(posting: dict) -> str:
    places = posting.get("jobLocation")
    for place in places if isinstance(places, list) else [places]:
        address = place.get("address") if isinstance(place, dict) else None
        if isinstance(address, dict):
            country = address.get("addressCountry")
            country = country.get("name") if isinstance(country, dict) else country
            found = ", ".join(t for t in (_s(address.get("addressLocality")), _s(address.get("addressRegion")), _s(country)) if t)
            if found:
                return found
    return "Remote" if _s(posting.get("jobLocationType")).upper() == "TELECOMMUTE" else ""


def parse_page(markup: str) -> dict:
    """What a page says about its job: company, title, location, description, posted, employment, page_title.

    Company and title come from the page's schema.org JobPosting only: a guess from the <title> would name a folder
    wrongly. The description is the posting's own, else the visible text of the whole page.
    """
    reader = _read(markup)
    found = {"company": "", "title": "", "location": "", "description": "", "posted": "", "employment": "",
             "page_title": C.one_line(html.unescape(reader.title)), "from_posting": False}
    for block in reader.ld:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for posting in _job_postings(data):
            org = posting.get("hiringOrganization")
            raw = posting.get("description")
            raw = html.unescape(raw) if isinstance(raw, str) and "&lt;" in raw else raw
            kinds = posting.get("employmentType")
            found.update(
                company=_s(org.get("name") if isinstance(org, dict) else org), title=_s(posting.get("title")),
                location=_location(posting), posted=_s(posting.get("datePosted"))[:10],
                description=_read(raw).text() if isinstance(raw, str) else "",
                employment=C.employment_label(",".join(t.lower().replace("_", "") for t in (kinds if isinstance(kinds, list) else [kinds]) if isinstance(t, str))),
                from_posting=True)
            break
        if found["from_posting"]:
            break
    if not found["description"]:
        found["description"] = reader.text()
    return found


def fetch(url: str) -> str:
    """The HTML of a page. Raises Unreadable (with the reason) for a network error, an error status, a redirect to a
    sign-in page or an answer that is not a web page."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml",
                                                   "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            final, kind = response.geturl(), response.headers.get_content_type()
            charset = response.headers.get_content_charset() or "utf-8"
            raw = response.read(MAX_PAGE_BYTES)
    except urllib.error.HTTPError as exc:
        raise Unreadable(f"the site answered with an error (HTTP {exc.code})") from None
    except (urllib.error.URLError, OSError, http.client.HTTPException, ValueError) as exc:
        raise Unreadable(f"the page could not be loaded ({getattr(exc, 'reason', None) or exc})") from None
    if LOGIN_URL_RE.search(urlsplit(final).path):
        raise Unreadable("the site sent the request to a sign-in page")
    if kind not in ("text/html", "application/xhtml+xml"):
        raise Unreadable(f"the address is not a web page ({kind})")
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


def thin_reason(text: str, fetched: bool) -> Optional[str]:
    """Why a description is no posting (empty, too short, or a sign-in / script / cookie wall), else None."""
    words = len(text.split())
    if words < MIN_WORDS:
        return "the text is empty or too short to be a posting" if words else "the text is empty"
    if fetched and words < WALL_WORDS and WALL_RE.search(text):
        return "the page shows a sign-in, script or cookie wall, not the posting"
    return None


# --------------------------------------------------------------------------- the books

def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").removeprefix("www.")


def known_line(rows: list[dict], folders: set[str], key: str) -> Optional[str]:
    """Says which row (or folder) already has this id, else None."""
    for row in rows:
        if row["_key"] == key:
            return f"{row['Company'] or '?'} - {row['Job Title'] or '?'}, sheet row {row['_index'] + 1}, status {row['Status']}"
    if key in folders:
        return "a job folder with this id exists, without a tracker row (/ohmyjob:prep-jobs adds the row)"
    return None


def twin_line(rows: list[dict], company: str, title: str, location: str) -> Optional[str]:
    """The row that has the same company, title and city. Every row counts, whatever its age or status. A posting with
    no location is a twin of any city; one with a location is not a twin of a row without a city (as in scrape)."""
    new = C.twin_key(company, title, location)
    for row in rows:
        old = C.twin_key(row["Company"], row["Job Title"], row["Location"])
        if C.is_twin(new, old) and (not new[2] or new[2] == old[2]):
            return (f"same company, title and city as {row['Company'] or '?'} - {row['Job Title'] or '?'}, "
                    f"sheet row {row['_index'] + 1}, status {row['Status']}")
    return None


def label_of(item: dict) -> str:
    return item["url"] or "pasted text: " + C.one_line(item["text"])[:50]


def normalize(raw: Any) -> dict:
    """One posting of the input as {url, text, company, title, location, apply_url}, all strings."""
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise Stop("a posting must be an object whose values are all strings (url, text, company, title, location, apply_url)")
    item = {k: C.one_line(raw.get(k)) for k in ("url", "company", "title", "location", "apply_url")}
    item["text"] = str(raw.get("text") or "").strip()
    if item["url"]:
        item["url"] = C.canonical_linkedin(strip_tracking(item["url"]))
    if item["apply_url"]:
        item["apply_url"] = strip_tracking(item["apply_url"])
    if not item["url"] and not item["text"]:
        raise Stop("a posting needs a url or a text")
    return item


def resolve(item: dict, rows: list[dict], folders: set[str]) -> tuple[str, str, Optional[dict]]:
    """(kind, message, job): kind is "known", "unreadable" or "needs input" with a message, or "ready" with the job."""
    url = item["url"]
    if url and not url.lower().startswith(("http://", "https://")):
        return "unreadable", "not a web address (http or https)", None
    key = job_key(url or pasted_url(item["text"]))
    if (line := known_line(rows, folders, key)) is not None:
        return "already known", line, None
    page = {"company": "", "title": "", "location": "", "description": "", "posted": "", "employment": "", "page_title": ""}
    if item["text"]:
        page["description"] = item["text"]
    else:
        try:
            page = parse_page(fetch(url))
        except Unreadable as exc:
            return "unreadable", str(exc), None
    if (why := thin_reason(page["description"], fetched=not item["text"] and not page.get("from_posting"))) is not None:
        return "unreadable", why, None
    company, title = item["company"] or page["company"], item["title"] or page["title"]
    if not company or not title:
        missing = " and ".join(n for n, v in (("company", company), ("title", title)) if not v)
        hint = f' (the page is titled "{page["page_title"]}")' if page["page_title"] else ""
        return "needs input", f"the {missing} could not be told with confidence{hint}; ask the candidate", None
    apply_url = item["apply_url"] if item["apply_url"].lower().startswith(("http://", "https://")) else ""
    job_url = url or pasted_url(item["text"])
    return "ready", "", {
        "key": key, "url": job_url, "apply_url": "" if apply_url == job_url else apply_url, "title": title, "company": company,
        "location": item["location"] or page["location"], "posted": page["posted"], "employment": page["employment"],
        "description": page["description"],
        "source": f"Added by hand ({host_of(url) if url else 'pasted text'})",
    }


def load_postings(root: Path, urls: list[str], paste: bool) -> list[dict]:
    raws: list[Any] = [{"url": u} for u in urls]
    if paste:
        inbox = root / ".ohmyjob" / INBOX
        try:
            data = json.loads(inbox.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise Stop(f"{inbox} not found: write the postings there first (ohmyjob add-job --inbox names it)") from None
        except (OSError, ValueError) as exc:
            raise Stop(f"{inbox} is not a JSON list of postings ({exc})") from None
        finally:
            inbox.unlink(missing_ok=True)  # pasted page text is not kept
        if not isinstance(data, list):
            raise Stop(f"{inbox} must hold a JSON list of postings")
        raws += data
    if not raws:
        raise Stop("give at least one web address, or --paste for postings written to the inbox file (--inbox names it)")
    return [normalize(raw) for raw in raws]


def run(root: Path, postings: list[dict], search: dict) -> int:
    rows = open_tracker(root).rows()  # read-only: the pages are fetched before the tracker is loaded for the change
    folders = known_keys(root)
    results: list[tuple[str, str, Optional[dict]]] = []
    for item in postings:
        results.append(resolve(item, rows, folders))  # a page can take seconds; known ids skip the fetch

    tracker = open_tracker(root)  # a short load, change, save
    rows = tracker.rows()
    folders = known_keys(root)
    found = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    warnings: list[str] = []
    counts = {"added": 0, "already known": 0, "unreadable": 0, "needs input": 0}
    for item, (kind, message, job) in zip(postings, results):
        label = label_of(item)
        if job is not None:
            message = known_line(rows, folders, job["key"]) or twin_line(rows, job["company"], job["title"], job["location"])
            kind = "already known" if message else "ready"
        if kind == "ready":
            folder = root / "Applications" / C.folder_name(job["key"], job["company"], job["title"])
            try:
                folder.mkdir(parents=True)  # never into an existing folder
            except FileExistsError:
                kind, message = "already known", f"folder {folder.name} exists"
        if kind == "ready":
            job["found"] = found
            try:
                (folder / "job.md").write_text(C.job_md(job), encoding="utf-8")  # job.md first, then the tracker
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)  # an empty folder would make the id known for good
                raise
            index = tracker.add_row({"Job URL": job["url"], "Company": job["company"], "Job Title": job["title"],
                                     "Date Found": found, "Location": job["location"], "Source": job["source"],
                                     "Status": "Found", "Notes": "Added by hand with /ohmyjob:add-job"})
            rows.append({"_key": job["key"], "_index": index, "Company": job["company"], "Job Title": job["title"],
                         "Location": job["location"], "Status": "Found"})
            if (entry := C.find_excluded_company_match(job["company"], search["exclude_companies"])) is not None:
                warnings.append(f"WARNING {job['company']} matches exclude_companies entry '{entry}'; added anyway, you chose this job")
            lines.append(f"added: {job['company']} - {job['title']} ({folder.name})")
            counts["added"] += 1
            continue
        lines.append(f"{kind}: {label}: {message}" + ("; paste the posting text instead" if kind == "unreadable" else ""))
        counts[kind] += 1
    try:
        if counts["added"]:
            tracker.save()
    except TrackerError as exc:
        raise Stop(f"{exc} The {counts['added']} new folders are in Applications/; /ohmyjob:prep-jobs adds their rows at Found.") from exc
    for text in warnings + lines:
        say(text)
    say("total: " + ", ".join(f"{n} {name.replace('needs', 'need')}" for name, n in counts.items())
        + ("; run /ohmyjob:prep-jobs" if counts["added"] else ""))
    return EXIT_SOME_LEFT if counts["unreadable"] or counts["needs input"] else EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ohmyjob add-job", description=__doc__.split("\n")[0])
    parser.add_argument("urls", nargs="*", help="web addresses of postings")
    parser.add_argument("--paste", action="store_true", help="also take the postings in the inbox file (deleted after reading)")
    parser.add_argument("--inbox", action="store_true", help="print the path of the inbox file and stop")
    args = parser.parse_args(argv)
    try:
        root = W.find_root()
        if args.inbox:
            (root / ".ohmyjob").mkdir(exist_ok=True)
            say(str(root / ".ohmyjob" / INBOX))
            return EXIT_OK
        template = W.template_files(root)
        if template:  # mock values must not become tracked jobs
            raise Stop(f"{template[0].name} still has the template marker ({W.TEMPLATE_MARKER}): replace the mock data with "
                       "yours and delete the marker line (the `_template` line in ohmyjob.json), then run again")
        return run(root, load_postings(root, args.urls, args.paste), W.load_config(root)["search"])
    except (W.WorkspaceError, Stop) as exc:
        say(f"FATAL: {exc}")
        return EXIT_FATAL
    except Exception as exc:  # noqa: BLE001  (a full disk, a permission error: one line, not a traceback)
        say(f"FATAL: unexpected {type(exc).__name__}: {exc}")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
