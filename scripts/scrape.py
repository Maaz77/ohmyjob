#!/usr/bin/env python3
"""Find new jobs on the configured boards (/ohmyjob:find-jobs): `ohmyjob scrape [--dry-run]`.

For each board in search.sources (in order), JobSpy is asked for every search_query term. A posting is kept when its
title matches a term, its employment type and company pass the filters, it does not read as spam (scrape_core.junk_reason), it is not known yet (tracker row or folder with
the same job key) and it is not a twin of a posting from an earlier board or of a recent tracker row. Each new job
gets Applications/<key>_<Company>_<Title>/job.md and a tracker row at "Found". The next step is /ohmyjob:prep-jobs.

Output: a multi-section summary (Search Results, Job Finder Summary, Filtered, Sources), `WARNING ...` lines, and
`FATAL: ...` when the run cannot continue (exit 1; exit 2: the run finished but a board failed). A posting whose company
matches exclude_companies is not dropped: it is recorded as a Deleted tracker row (no folder), so it is not met again.
"""
from __future__ import annotations

import argparse
import logging
import math
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

import freehire_source
import languages as LANG
import scrape_core as C
import workspace as W
from job_key import job_key, strip_tracking
from tracker import Tracker, TrackerError

FOLDER_ROOTS = ("Applications", "Pending-Review", "Needs-Attention", "Submitted")
KEY_RE = re.compile(r"^(\d+)_")
SOURCE_NAMES = {"linkedin": "LinkedIn", "freehire_source": "FreeHire"}  # anything else: .title()
COLORS = {"FreeHire": "\033[36m", None: "\033[35m"}  # cyan; filter-stage lines (no source) magenta; the boards blue
_RESET = "\033[0m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_BLUE = "\033[34m"
log = logging.getLogger(__name__)  # only INFO from here on: with no handler installed (no --verbose) nothing prints


def _color(text: str, color: str) -> str:
    return f"{color}{text}{_RESET}" if sys.stdout.isatty() else text


def _log_filter(drop: str, board: str, job: dict) -> None:
    title, company = job["title"], job["company"]
    if drop in ("level", "term", "title", "duplicate", "twin_tracker", "twin_board"):
        reason = {"level": "title_contains_excluded_level", "term": "title_contains_excluded_term",
                  "title": "title_does_not_match_search_term", "duplicate": "duplicate_title_and_company",
                  "twin_tracker": "already_in_tracker", "twin_board": "twin_of_earlier_source"}[drop]
        log.info("TITLE_FILTERED | title=%s | company=%s | reason=%s", title, company, reason)
    elif drop == "type":
        log.info("JOB_TYPE_FILTERED | title=%s | company=%s | reason=job_type=%s", title, company, job.get("job_type", ""))
    else:
        log.info("DROPPED | reason=%s | board=%s | title=%s | company=%s", drop, board, title, company)


class LogFormat(logging.Formatter):
    """`LEVEL | Source | message`; the Source comes from extra={"source": ...} or the logger name (jobspy.<site>)."""

    def __init__(self, color: bool = False):
        super().__init__()
        self.color = color

    def format(self, record: logging.LogRecord) -> str:
        source = getattr(record, "source", None)
        if source is None and record.name.startswith(("jobspy.", "freehire_source")):
            site = record.name.rsplit(".", 1)[-1]
            source = SOURCE_NAMES.get(site, site.title())
        line = f"{record.levelname} | " + (f"{source} | " if source else "") + record.getMessage()
        return COLORS.get(source, "\033[34m") + line + "\033[0m" if self.color else line


def start_logging() -> None:
    """--verbose: progress lines (and JobSpy's own) on stderr, which a background run's output file captures."""
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(LogFormat(color=stream.stream.isatty()))
    logging.root.addHandler(stream)
    logging.root.setLevel(logging.INFO)


EXIT_OK, EXIT_FATAL, EXIT_BOARD_FAILED = 0, 1, 2  # FATAL line / the run finished but a board failed


def open_tracker(root: Path) -> Tracker:
    try:
        return Tracker(root / "Job_Tracker.xlsx")
    except TrackerError as exc:
        raise Stop(f"{exc}. Run /ohmyjob:setup.") from exc


class Stop(Exception):
    """The run cannot go on: the message becomes a FATAL line."""


def say(text: str) -> None:
    print(text, flush=True)


def cell(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def normalize(record: dict, board: str) -> dict:
    """One JobSpy row (its documented columns) as the fields this script uses."""
    url = C.canonical_linkedin(strip_tracking(cell(record.get("job_url"))))
    apply_url = strip_tracking(cell(record.get("job_url_direct")))
    if not apply_url.lower().startswith(("http://", "https://")) or apply_url == url:
        apply_url = ""
    return {
        "title": C.one_line(cell(record.get("title"))), "company": C.one_line(cell(record.get("company"))),
        "url": url, "apply_url": apply_url, "source": board, "location": cell(record.get("location")),
        "posted": cell(record.get("date_posted")), "job_type": cell(record.get("job_type")),
        "job_level": cell(record.get("job_level")), "description": cell(record.get("description")), "note": "",
        "posting_language": "",  # JobSpy has no language label: always unknown
    }


# Terms of one board that run at once. JobSpy spaces LinkedIn's search pages but not the job detail pages (one request
# per job, no pause), so terms are not run wide: every board takes 2 unless it has an entry here.
BOARD_WORKERS: dict[str, int] = {}
DEFAULT_BOARD_WORKERS = 2

# seconds slept before each LinkedIn detail page: JobSpy fires them back to back, which gets the run rate limited
DETAIL_PAUSE = (1.0, 2.5)


def space_linkedin_details() -> None:
    """Make every LinkedIn detail-page request in this process wait a short random time first (once, however often called)."""
    try:
        from jobspy.linkedin import LinkedIn
    except ImportError:
        return
    original = LinkedIn._fetch_details
    if getattr(original, "spaced", False):
        return

    def spaced(self, *args, **kwargs):
        time.sleep(random.uniform(*DETAIL_PAUSE))
        return original(self, *args, **kwargs)

    spaced.spaced = True
    LinkedIn._fetch_details = spaced


def workers_for(board: str) -> int:
    return BOARD_WORKERS.get(board, DEFAULT_BOARD_WORKERS)


class Scraped(NamedTuple):
    rows: list
    error: Optional[str]
    warnings: list
    unusable: int = 0  # freehire results that cannot be tracked or read
    repeated: int = 0  # freehire jobs met again under another term


def scrape_board(board: str, search: dict, verbose: bool = False) -> Scraped:
    """All rows of one board for every search term, its error text and its warnings (JobSpy reports both in attrs)."""
    shown = SOURCE_NAMES.get(board, board.title())
    log.info("BOARD_START | board=%s | terms=%d", board, len(search["search_query"]), extra={"source": shown})
    if board == "freehire":
        found = freehire_source.search_freehire_jobs(
            search["search_query"], search["location"], search["country"],
            search["recency_days"], search["uk_sponsor_only"],
        )
        log.info("%s_SCRAPE_SUMMARY | terms=%d | results=%d | before filtering",
                 board.upper(), len(search["search_query"]), len(found.rows), extra={"source": shown})
        return Scraped(found.rows, found.error, found.warnings, found.unusable, found.repeated)
    import jobspy  # looked up at call time: the tests replace jobspy.scrape_jobs
    if board == "linkedin":
        space_linkedin_details()

    def one_term(term: str):
        args: dict[str, Any] = dict(
            site_name=[board], search_term=term, location=search["location"] or None,
            results_wanted=search["results_per_term"], hours_old=C.hours_old(search["recency_days"]),
            fetch_description=True, description_format="markdown", verbose=2 if verbose else 0,
        )
        if search["country"]:
            args["country_indeed"] = search["country"]
        if board == "google":
            args["google_search_term"] = C.google_search_term(term, search["location"], search["recency_days"])
        # each line names its board and term: the terms run in worker threads
        log.info("%s_SCRAPE_STARTED | term=%s", board.upper(), term, extra={"source": shown})
        try:
            frame = jobspy.scrape_jobs(**args)
        except Exception as exc:  # noqa: BLE001  (JobSpy raises ValueError for bad arguments; a network library may raise anything)
            log.info("%s_SCRAPE_FINISHED | term=%s | status=failed | error=%s", board.upper(), term, exc, extra={"source": shown})
            return term, None, str(exc)
        log.info("%s_SCRAPE_FINISHED | term=%s | status=success | results=%d", board.upper(), term, len(frame), extra={"source": shown})
        return term, frame, None

    terms = search["search_query"]
    with ThreadPoolExecutor(max_workers=max(1, min(len(terms), workers_for(board)))) as pool:
        results = list(pool.map(one_term, terms))  # map keeps the order of the terms: the first copy of a job wins
    rows: list[dict] = []
    errors: list[str] = []
    warnings: list[str] = []
    for term, frame, failure in results:
        if failure is not None:
            errors.append(f"{term!r}: {failure}")
            continue
        attrs = getattr(frame, "attrs", None) or {}
        errors += [f"{term!r}: {text}" for text in attrs.get("errors", {}).values()]
        for text in attrs.get("warnings", {}).values():
            warnings += [w for w in (text if isinstance(text, list) else [text]) if w not in warnings]
        rows += [normalize(rec, board) for rec in frame.to_dict("records")]
    log.info("%s_SCRAPE_SUMMARY | terms=%d | results=%d | before filtering",
             board.upper(), len(terms), len(rows), extra={"source": shown})
    return Scraped(rows, "; ".join(errors) or None, warnings)


def known_keys(root: Path) -> set[str]:
    """The job keys that already have a folder in any of the workspace's job folders."""
    keys = set()
    for name in FOLDER_ROOTS:
        folder = root / name
        if folder.is_dir():
            keys.update(m.group(1) for p in folder.iterdir() if p.is_dir() and (m := KEY_RE.match(p.name)))
    return keys


def snapshot(root: Path) -> None:
    """--git-snapshot: commit the tracker and Applications/ as they are before this run (skipped outside a git repo)."""
    if not (root / ".git").exists():
        say("WARNING --git-snapshot: the workspace is not a git repo; skipped")
        return
    names = [n for n in ("Job_Tracker.xlsx", "Applications") if (root / n).exists()]
    # .gitignore is respected: the workspace template ignores the personal files, and the user removes a line to version them
    paths = [n for n in names if subprocess.run(["git", "-C", str(root), "check-ignore", "-q", n]).returncode != 0]
    if not paths:
        if names:
            say("WARNING --git-snapshot: the tracker and Applications/ are ignored by .gitignore; nothing to snapshot")
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    steps = (("add", ["add", "--", *paths]),  # the pathspec of the commit keeps the user's other staged changes out
             ("commit", ["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", f"ohmyjob snapshot {stamp}", "--", *paths]))
    for name, cmd in steps:
        done = subprocess.run(["git", "-C", str(root), *cmd], capture_output=True, text=True)
        if done.returncode != 0 and not any(t in done.stdout + done.stderr for t in ("nothing to commit", "no changes added")):
            say(f"WARNING --git-snapshot: git {name} failed ({(done.stderr or done.stdout).strip()[:120]}); continuing")
            return


def _print_search_results(jobs: list[dict], boards: list[str]) -> None:
    listed = [j for j in jobs if j.get("excluded") is None]
    if not listed:
        return
    say("Search Results:")
    sources_in = [b for b in boards if any(j["source"] == b for j in listed)]
    multi = len(sources_in) > 1
    index = 1
    for source in sources_in:
        group = [j for j in listed if j["source"] == source]
        if multi:
            shown = SOURCE_NAMES.get(source, source.title())
            say(f"{_color(shown + ':', _CYAN)}")
        for j in group:
            prefix = "  " if multi else ""
            say(f"{prefix}{index}. {_color(j['company'], _BLUE)} : {_color(j['title'], _GREEN)}")
            index += 1


def _print_summary(totals: dict, boards_info: dict, boards: list[str],
                   dry_run: bool = False, written: int = 0) -> None:
    header = "Job Finder Summary" + (" (dry run, nothing written)" if dry_run else "")
    say(f"\n{_color(header, _BOLD + _CYAN)}")
    say(_color("------------------", _CYAN))
    added = totals["new"] if dry_run else written
    say(f"{_color('Added             :', _GREEN)} {added} new job(s)")
    say(f"{_color('Skipped           :', _YELLOW)} {totals['skipped']} already tracked")
    say(f"{_color('Auto-excluded     :', _RED)} {totals['excluded']} by company name")
    say(f"{_color('Scraped pre-filter:', _CYAN)} {totals['scraped']} job(s)")

    say(f"\n{_color('Filtered', _BOLD + _CYAN)}")
    say(_color("--------", _CYAN))
    say(f"{_color('Job title         :', _YELLOW)} {totals['job_title']}")
    say(f"{_color('Job level         :', _YELLOW)} {totals['job_level']}")
    if totals.get("filter_terms"):
        say(f"{_color('Filter terms      :', _YELLOW)} {totals['filter_terms']}")
    say(f"{_color('Job type          :', _YELLOW)} {totals['job_type']}")
    say(f"{_color('Duplicate job id  :', _YELLOW)} {totals['dup_id']}")
    say(f"{_color('Duplicate title   :', _YELLOW)} {totals['duplicate']}")
    if totals.get("lang"):
        say(f"{_color('Posting language  :', _YELLOW)} {totals['lang']}")
    if totals.get("junk"):
        say(f"{_color('Junk              :', _YELLOW)} {totals['junk']}")
    say(f"{_color('Unusable          :', _YELLOW)} {totals['unusable']}")
    if "freehire" in boards:
        say(f"{_color('Freehire unusable :', _YELLOW)} {totals['freehire_unusable']}")
        say(f"{_color('Freehire duplicate:', _YELLOW)} {totals['freehire_duplicate']}")
    say(f"{_color('Twin of earlier   :', _YELLOW)} {totals['twin_board']}")
    say(f"{_color('Twin of tracker   :', _YELLOW)} {totals['twin_tracker']}")
    for name, n in totals["failed_boards"].items():
        say(f"{_color(f'{name} failed   :', _YELLOW)} {n}")

    say(f"\n{_color('Sources', _BOLD + _CYAN)}")
    say(_color("-------", _CYAN))
    for board in boards:
        shown = SOURCE_NAMES.get(board, board.title())
        info = boards_info.get(board, {})
        label = f"{shown} passed"
        say(f"{_color(f'{label:<19}:', _GREEN)} {info.get('passed', 0)}")
    if "freehire" in boards:
        info = boards_info.get("freehire", {})
        n_failed = 1 if info.get("error") else 0
        status_color = _RED if n_failed else _GREEN
        say(f"{_color('Freehire status    :', status_color)} {n_failed} freehire search(es) failed.")

    if not dry_run and written:
        say("\nrun /ohmyjob:prep-jobs")


def run(root: Path, search: dict, dry_run: bool = False, git_snapshot: bool = False, verbose: bool = False) -> int:
    template = W.template_files(root)
    if template:  # the mock values would be searched for and tracked as if they were yours
        raise Stop(f"{template[0].name} still has the template marker ({W.TEMPLATE_MARKER}): replace the mock data with "
                   "yours and delete the marker line (the `_template` line in ohmyjob.json), then run again")
    if not search["search_query"]:
        raise Stop('search.search_query is empty in ohmyjob.json. Add search terms, for example ["Data Analyst"].')
    profile = root / "Profile.md"
    try:
        spoken, unresolved = LANG.profile_languages(profile.read_text(encoding="utf-8")) if profile.is_file() else (set(), [])
    except (OSError, UnicodeDecodeError):  # an unreadable profile turns the language filter off, never the run
        spoken, unresolved = set(), []
    if unresolved:  # a named language must always survive: with one we cannot match, filter on nothing
        say(f'WARNING Profile.md: the # Languages line "{unresolved[0]}" names no language this filter knows; '
            "the language filter is off for this run")
        spoken = set()
    boards = list(search["sources"])
    if verbose:
        start_logging()

    open_tracker(root)  # fail before the long scrape if there is no usable tracker
    lemmas = C.Lemmas(search["languages"])
    matcher = C.TitleMatcher(search["search_query"], search["languages"], search["title_filter"])
    filters = C.Filters(search, lemmas)
    if git_snapshot and not dry_run:
        snapshot(root)

    scraped = {board: scrape_board(board, search, verbose) for board in boards}  # minutes; the tracker is not open meanwhile

    tracker = open_tracker(root)  # a short load, change, save
    rows = tracker.rows()
    seen_keys = {r["_key"] for r in rows} | known_keys(root)
    window = C.twin_window(search["recency_days"])
    tracker_twins = C.TwinIndex()
    board_twins = C.TwinIndex()
    for row in rows:
        if (row["Status"] or "").strip().casefold() in C.KEPT_STATUSES_FOLDED or C.recent_enough(row["Date Found"], window):
            tracker_twins.add(row["Company"], row["Job Title"], row["Location"], exact=True)

    found = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_jobs: list[dict] = []
    failed = 0
    totals: dict[str, Any] = {"scraped": 0, "new": 0, "excluded": 0, "skipped": 0,
                               "job_title": 0, "job_level": 0, "job_type": 0, "dup_id": 0, "duplicate": 0,
                               "twin_board": 0, "twin_tracker": 0, "unusable": 0,
                               "freehire_unusable": 0, "freehire_duplicate": 0, "lang": 0, "junk": 0,
                               "filter_terms": 0, "failed_boards": {}}
    boards_info: dict[str, dict] = {}
    run_ids: set[str] = set()
    run_titles: set[tuple[str, str, str]] = set()
    for board in boards:
        got, error, warnings, unusable, repeated = scraped[board]
        for text in warnings:
            say(f"WARNING {board}: {text}")
        if error:
            failed += 1
        count = {"bad": unusable, "known": 0, "dup id": repeated, "failed": 0, "type": 0, "lang": 0, "level": 0, "term": 0, "title": 0,
                 "junk": 0, "duplicate": 0, "twin_tracker": 0, "twin_board": 0}
        kept: list[dict] = []
        known: list[dict] = []
        for job in got:
            drop = None  # the reason this job is dropped, logged once below
            key = job_key(job["url"]) if job["url"] else ""
            if not job["title"] or not key.isdigit():
                count["bad"] += 1
                drop = "bad"
            elif key in seen_keys:
                count["known"] += 1
                drop = "known"
                known.append(job)
            elif key in run_ids:
                count["dup id"] += 1
                drop = "dup id"
            elif not job["description"] and board == "linkedin":
                # fetch_description is always on, so no description means the job's own page did not load (throttling)
                count["failed"] += 1
                drop = "failed"
            elif not C.employment_ok(job["job_type"], search["employment_types"]):
                count["type"] += 1
                drop = "type"
            elif not LANG.ok(job["posting_language"], spoken):
                count["lang"] += 1
                drop = "lang"
            elif (why := filters.reason(job)) is not None:
                count[why] += 1
                drop = why
            elif not matcher.matches(job["title"]):
                count["title"] += 1
                drop = "title"
            elif C.junk_reason(job) is not None:
                count["junk"] += 1
                drop = "junk"
            elif (job["title"], job["company"], C.twin_city(job["location"])) in run_titles:
                count["duplicate"] += 1  # the same title, company and city under another id: the first one stays
                drop = "duplicate"
            elif tracker_twins.find(job["company"], job["title"], job["location"]):
                count["twin_tracker"] += 1
                drop = "twin_tracker"
            elif board_twins.find(job["company"], job["title"], job["location"]):
                count["twin_board"] += 1
                drop = "twin_board"
            else:
                run_ids.add(key)
                run_titles.add((job["title"], job["company"], C.twin_city(job["location"])))
                job.update(key=key, found=found, employment=C.employment_label(job["job_type"]),
                           excluded=filters.excluded_company(job["company"]))
                kept.append(job)
            if drop:
                _log_filter(drop, board, job)
            elif job.get("excluded") is not None:
                log.info("COMPANY_EXCLUDED | title=%s | company=%s | reason=company_matches_excluded_entry=%s",
                         job["title"], job["company"], job["excluded"])
        for job in kept + known:
            board_twins.add(job["company"], job["title"], job["location"])
        new_jobs += kept
        excluded = sum(1 for job in kept if job["excluded"] is not None)
        scraped_n = len(got) + unusable + repeated
        totals["scraped"] += scraped_n
        totals["new"] += len(kept) - excluded
        totals["excluded"] += excluded
        totals["skipped"] += count["known"]
        totals["job_title"] += count["title"]
        totals["job_level"] += count["level"]
        totals["filter_terms"] += count["term"]
        totals["job_type"] += count["type"]
        totals["dup_id"] += count["dup id"] - repeated
        totals["duplicate"] += count["duplicate"]
        totals["twin_tracker"] += count["twin_tracker"]
        totals["twin_board"] += count["twin_board"]
        totals["unusable"] += count["bad"] - unusable
        totals["lang"] += count["lang"]
        totals["junk"] += count["junk"]
        if board == "freehire":
            totals["freehire_unusable"] += unusable
            totals["freehire_duplicate"] += repeated
        shown_name = SOURCE_NAMES.get(board, board.title())
        if count["failed"]:
            totals["failed_boards"][shown_name] = count["failed"]
        boards_info[board] = {"passed": len(kept) - excluded, "error": error}
        if count["failed"] >= 3 and count["failed"] * 2 > scraped_n:
            say(f"WARNING {board}: {count['failed']} of {scraped_n} detail pages did not load (rate limited); "
                "lower search.results_per_term or search_query")
        if error:
            say(f"WARNING {board}: ERROR {error}")

    if failed == len(boards) and not totals["scraped"]:
        raise Stop("every board failed; see the ERROR lines above")
    if dry_run:
        _print_search_results(new_jobs, boards)
        _print_summary(totals, boards_info, boards, dry_run=True)
        return EXIT_BOARD_FAILED if failed else EXIT_OK

    written = excluded_rows = 0
    for job in new_jobs:
        row = {"Job URL": job["url"], "Company": job["company"], "Job Title": job["title"], "Date Found": found,
               "Location": job["location"], "Source": job["source"]}
        if job["excluded"] is not None:  # recorded as Deleted, so it is not met again; no folder
            tracker.add_row({**row, "Status": "Deleted",
                             "Notes": f"Auto-excluded: company matches excluded entry '{job['excluded']}'"})
            excluded_rows += 1
            continue
        folder = root / "Applications" / C.folder_name(job["key"], job["company"], job["title"])
        try:
            folder.mkdir(parents=True)  # never into an existing folder
        except FileExistsError:
            say(f"WARNING {folder.name}: folder exists; skipped")
            continue
        try:
            (folder / "job.md").write_text(C.job_md(job), encoding="utf-8")
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)  # an empty folder would make known_keys call the job known for good
            raise
        tracker.add_row({**row, "Status": "Found", "Notes": job["note"]})
        written += 1
    if written or excluded_rows:
        try:
            tracker.save()
        except TrackerError as exc:
            raise Stop(f"{exc} The {written} new folders are in Applications/; /ohmyjob:prep-jobs adds their rows at Found.") from exc
    _print_search_results(new_jobs, boards)
    _print_summary(totals, boards_info, boards, written=written)
    return EXIT_BOARD_FAILED if failed else EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ohmyjob scrape", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would be new; write nothing")
    parser.add_argument("--git-snapshot", action="store_true",
                        help="commit the tracker and Applications/ before the run (skipped outside a git repository)")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="log progress to stderr while searching")
    args = parser.parse_args(argv)
    try:
        root = W.find_root()
        return run(root, W.load_config(root)["search"], args.dry_run, args.git_snapshot, args.verbose)
    except (W.WorkspaceError, Stop) as exc:
        say(f"FATAL: {exc}")
        return EXIT_FATAL
    except Exception as exc:  # noqa: BLE001  (a full disk, a permission error: one line, not a traceback)
        say(f"FATAL: unexpected {type(exc).__name__}: {exc}")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
