#!/usr/bin/env python3
"""Find new jobs on the configured boards (/ohmyjob:find-jobs): `ohmyjob scrape [--dry-run]`.

For each board in search.sources (in order), JobSpy is asked for every search_query term. A posting is kept when its
title matches a term, its employment type and company pass the filters, it is not known yet (tracker row or folder with
the same job key) and it is not a twin of a posting from an earlier board or of a recent tracker row. Each new job
gets Applications/<key>_<Company>_<Title>/job.md and a tracker row at "Found". The next step is /ohmyjob:prep-jobs.

Output: one line per board, one total line, `WARNING ...` lines, and `FATAL: ...` when the run cannot continue (exit 1;
exit 2: the run finished but a board failed).
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

import freehire_source
import scrape_core as C
import workspace as W
from job_key import job_key, strip_tracking
from tracker import Tracker, TrackerError

FOLDER_ROOTS = ("Applications", "Pending-Review", "Needs-Attention", "Submitted")
KEY_RE = re.compile(r"^(\d+)_")


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
    }


MAX_SCRAPE_WORKERS = 10  # terms of one board run at once: a scrape is almost all network wait (LinkedIn pauses between pages)


class Scraped(NamedTuple):
    rows: list
    error: Optional[str]
    warnings: list
    unusable: int = 0  # results the source returned but cannot track (freehire)


def scrape_board(board: str, search: dict) -> Scraped:
    """All rows of one board for every search term, its error text and its warnings (JobSpy reports both in attrs)."""
    if board == "freehire":
        found = freehire_source.search_freehire_jobs(
            search["search_query"], search["location"], search["country"],
            search["recency_days"], search["uk_sponsor_only"],
        )
        return Scraped(found.rows, found.error, found.warnings, found.fetched - len(found.rows))
    import jobspy  # looked up at call time: the tests replace jobspy.scrape_jobs

    def one_term(term: str):
        args: dict[str, Any] = dict(
            site_name=[board], search_term=term, location=search["location"] or None,
            results_wanted=search["results_per_term"], hours_old=C.hours_old(search["recency_days"]),
            fetch_description=True, description_format="markdown", verbose=0,
        )
        if search["country"]:
            args["country_indeed"] = search["country"]
        if board == "google":
            args["google_search_term"] = C.google_search_term(term, search["location"], search["recency_days"])
        try:
            return term, jobspy.scrape_jobs(**args), None
        except Exception as exc:  # noqa: BLE001  (JobSpy raises ValueError for bad arguments; a network library may raise anything)
            return term, None, str(exc)

    terms = search["search_query"]
    with ThreadPoolExecutor(max_workers=max(1, min(len(terms), MAX_SCRAPE_WORKERS))) as pool:
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
    """git_snapshot: commit the tracker and Applications/ as they are before this run (skipped outside a git repo)."""
    if not (root / ".git").exists():
        say("WARNING git_snapshot: the workspace is not a git repo; skipped")
        return
    paths = [n for n in ("Job_Tracker.xlsx", "Applications") if (root / n).exists()]
    if not paths:
        return
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    steps = (("add", ["add", "-f", "--", *paths]),  # the pathspec keeps the user's other staged changes out of the commit
             ("commit", ["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", f"ohmyjob snapshot {stamp}", "--", *paths]))
    for name, cmd in steps:
        done = subprocess.run(["git", "-C", str(root), *cmd], capture_output=True, text=True)
        if done.returncode != 0 and not any(t in done.stdout + done.stderr for t in ("nothing to commit", "no changes added")):
            say(f"WARNING git_snapshot: git {name} failed ({(done.stderr or done.stdout).strip()[:120]}); continuing")
            return


def run(root: Path, search: dict, dry_run: bool = False) -> int:
    if not search["search_query"]:
        raise Stop('search.search_query is empty in ohmyjob.json. Add search terms, for example ["Data Analyst"].')
    boards = list(search["sources"])

    open_tracker(root)  # fail before the long scrape if there is no usable tracker
    lemmas = C.Lemmas(search["languages"])
    matcher = C.TitleMatcher(search["search_query"], search["languages"], search["title_filter"],
                             search["title_aliases"], search["title_only_aliases"])
    filters = C.Filters(search, lemmas)
    if search["git_snapshot"] and not dry_run:
        snapshot(root)

    scraped = {board: scrape_board(board, search) for board in boards}  # minutes; the tracker is not open meanwhile

    tracker = open_tracker(root)  # a short load, change, save
    rows = tracker.rows()
    seen_keys = {r["_key"] for r in rows} | known_keys(root)
    window = C.twin_window(search["recency_days"])
    twins = C.TwinIndex()
    for row in rows:
        if (row["Status"] or "").strip().casefold() in C.KEPT_STATUSES_FOLDED or C.recent_enough(row["Date Found"], window):
            twins.add(row["Company"], row["Job Title"], row["Location"], exact=True)

    found = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_jobs: list[dict] = []
    failed = 0
    totals = {"scraped": 0, "new": 0, "excluded": 0}
    run_ids: set[str] = set()  # keys met earlier in this run (another term or board)
    run_titles: set[tuple[str, str]] = set()  # (title, company) as written, of the jobs kept so far
    for board in boards:
        got, error, warnings, unusable = scraped[board]
        for text in warnings:
            say(f"WARNING {board}: {text}")
        if error:
            failed += 1
        count = {"bad": unusable, "known": 0, "dup id": 0, "failed": 0, "type": 0, "level": 0, "term": 0, "title": 0,
                 "duplicate": 0, "twin": 0}
        kept: list[dict] = []
        known: list[dict] = []
        for job in got:
            key = job_key(job["url"]) if job["url"] else ""
            if not job["title"] or not key.isdigit():
                count["bad"] += 1
            elif key in seen_keys:
                count["known"] += 1
                known.append(job)
            elif key in run_ids:
                count["dup id"] += 1
            elif not job["job_type"] and board == "linkedin" and search["employment_types"]:
                # JobSpy gets the type from the job's own page; none means that page did not load (throttling)
                count["failed"] += 1
            elif not C.employment_ok(job["job_type"], search["employment_types"]):
                count["type"] += 1
            elif (why := filters.reason(job)) is not None:
                count[why] += 1
            elif not matcher.matches(job["title"]):
                count["title"] += 1
            elif (job["title"], job["company"]) in run_titles:
                count["duplicate"] += 1  # the same title and company under another id: the first one stays
            elif twins.find(job["company"], job["title"], job["location"]):
                count["twin"] += 1
            else:
                run_ids.add(key)
                run_titles.add((job["title"], job["company"]))
                job.update(key=key, found=found, employment=C.employment_label(job["job_type"]),
                           excluded=filters.excluded_company(job["company"]))
                kept.append(job)
        for job in kept + known:  # a board's own postings are compared with the earlier boards only
            twins.add(job["company"], job["title"], job["location"])
        new_jobs += kept
        excluded = sum(1 for job in kept if job["excluded"] is not None)
        totals["scraped"] += len(got) + unusable
        totals["new"] += len(kept) - excluded
        totals["excluded"] += excluded
        dropped = ", ".join(f"{n} {why}" for why, n in count.items() if n)
        say(f"{board}: {len(got) + unusable} scraped, {len(kept) - excluded} new"
            + (f", {excluded} auto-excluded" if excluded else "")
            + (f" ({dropped} dropped)" if dropped else "") + (f" ERROR {error}" if error else ""))

    if failed == len(boards) and not totals["scraped"]:
        raise Stop("every board failed; see the ERROR lines above")
    if dry_run:
        say(f"total: {totals['scraped']} scraped, {totals['new']} would be new, {totals['excluded']} auto-excluded "
            "(dry run, nothing written)")
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
        (folder / "job.md").write_text(C.job_md(job), encoding="utf-8")
        tracker.add_row({**row, "Status": "Found", "Notes": job["note"]})
        written += 1
    if written or excluded_rows:
        try:
            tracker.save()
        except TrackerError as exc:
            raise Stop(f"{exc} The {written} new folders are in Applications/; /ohmyjob:prep-jobs adds their rows at Found.") from exc
    say(f"total: {totals['scraped']} scraped, {written} new jobs at Found, {excluded_rows} auto-excluded"
        + ("; run /ohmyjob:prep-jobs" if written else ""))
    return EXIT_BOARD_FAILED if failed else EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ohmyjob scrape", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would be new; write nothing")
    args = parser.parse_args(argv)
    try:
        root = W.find_root()
        return run(root, W.load_config(root)["search"], args.dry_run)
    except (W.WorkspaceError, Stop) as exc:
        say(f"FATAL: {exc}")
        return EXIT_FATAL
    except Exception as exc:  # noqa: BLE001  (a full disk, a permission error: one line, not a traceback)
        say(f"FATAL: unexpected {type(exc).__name__}: {exc}")
        return EXIT_FATAL


if __name__ == "__main__":
    sys.exit(main())
