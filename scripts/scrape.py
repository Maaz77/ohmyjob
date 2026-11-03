#!/usr/bin/env python3
"""Find new jobs on the configured boards (/ohmyjob:find-jobs): `ohmyjob scrape [--dry-run]`.

For each board in search.sources (in order), JobSpy is asked for every search_query term. A posting is kept when its
title matches a term, its employment type and company pass the filters, it is not known yet (tracker row or folder with
the same job key) and it is not a twin of a posting from an earlier board or of a recent tracker row. Each new job
gets Applications/<key>_<Company>_<Title>/job.md and a tracker row at "Found". The next step is /ohmyjob:prep-jobs.

Output: one line per board, one total line, `WARNING ...` lines, and `FATAL ...` when the run cannot continue (exit 1).
"""
from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import scrape_core as C
import workspace as W
from job_key import job_key, strip_tracking
from tracker import Tracker, TrackerError

TWIN_DAYS = 30  # tracker rows found within this many days count for the twin check
FOLDER_ROOTS = ("Applications", "Pending-Review", "Needs-Attention", "Submitted")
KEY_RE = re.compile(r"^(\d+)_")


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
    url = strip_tracking(cell(record.get("job_url")))
    apply_url = strip_tracking(cell(record.get("job_url_direct")))
    if not apply_url.lower().startswith(("http://", "https://")) or apply_url == url:
        apply_url = ""
    return {
        "title": C.one_line(cell(record.get("title"))), "company": C.one_line(cell(record.get("company"))),
        "url": url, "apply_url": apply_url, "source": board, "location": cell(record.get("location")),
        "posted": cell(record.get("date_posted")), "job_type": cell(record.get("job_type")),
        "job_level": cell(record.get("job_level")), "description": cell(record.get("description")),
    }


def scrape_board(board: str, search: dict) -> tuple[list[dict], Optional[str]]:
    """All rows of one board for every search term, and its error text (JobSpy reports a board's failure in attrs)."""
    import jobspy  # looked up at call time: the tests replace jobspy.scrape_jobs

    rows: list[dict] = []
    errors: list[str] = []
    for term in search["search_query"]:
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
            frame = jobspy.scrape_jobs(**args)
        except Exception as exc:  # noqa: BLE001  (JobSpy raises ValueError for bad arguments; a network library may raise anything)
            errors.append(f"{term!r}: {exc}")
            continue
        errors += [f"{term!r}: {text}" for text in (getattr(frame, "attrs", None) or {}).get("errors", {}).values()]
        rows += [normalize(rec, board) for rec in frame.to_dict("records")]
    return rows, "; ".join(errors) or None


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
    steps = (("add", ["add", "-f", "--", *paths]),
             ("commit", ["-c", "commit.gpgsign=false", "commit", "-q", "--no-verify", "-m", f"ohmyjob snapshot {stamp}"]))
    for name, cmd in steps:
        done = subprocess.run(["git", "-C", str(root), *cmd], capture_output=True, text=True)
        if done.returncode != 0 and "nothing to commit" not in done.stdout + done.stderr:
            say(f"WARNING git_snapshot: git {name} failed ({(done.stderr or done.stdout).strip()[:120]}); continuing")
            return


def run(root: Path, search: dict, dry_run: bool = False) -> int:
    if not search["search_query"]:
        raise Stop('search.search_query is empty in ohmyjob.json. Add search terms, for example ["Data Analyst"].')
    boards = []
    for source in search["sources"]:
        if source == "freehire":
            say("WARNING freehire: no client in this version; skipped (uk_sponsor_only has no effect yet)")
        else:
            boards.append(source)
    if not boards:
        raise Stop("none of search.sources can run in this version (freehire is skipped); add a JobSpy board such as linkedin")

    try:
        tracker = Tracker(root / "Job_Tracker.xlsx")
    except TrackerError as exc:
        raise Stop(f"{exc}. Run /ohmyjob:setup.") from exc
    rows = tracker.rows()
    seen_keys = {r["_key"] for r in rows} | known_keys(root)
    twins = C.TwinIndex()
    for row in rows:
        if C.recent_enough(row["Date Found"], TWIN_DAYS):
            twins.add(row["Company"], row["Job Title"], row["Location"])

    lemmas = C.Lemmas(search["languages"])
    matcher = C.TitleMatcher(search["search_query"], search["languages"], search["title_filter"],
                             search["title_aliases"], search["title_only_aliases"])
    filters = C.Filters(search, lemmas)
    if search["git_snapshot"] and not dry_run:
        snapshot(root)

    found = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_jobs: list[dict] = []
    failed = 0
    totals = {"scraped": 0, "new": 0}
    for board in boards:
        got, error = scrape_board(board, search)
        if error:
            failed += 1
        count = {"title": 0, "excluded": 0, "type": 0, "known": 0, "twin": 0, "bad": 0}
        kept: list[dict] = []
        for job in got:
            key = job_key(job["url"]) if job["url"] else ""
            if not job["title"] or not key.isdigit():
                count["bad"] += 1
            elif key in seen_keys:
                count["known"] += 1
            elif filters.reason(job):
                count["excluded"] += 1
            elif not matcher.matches(job["title"]):
                count["title"] += 1
            elif not C.employment_ok(job["job_type"], search["employment_types"]):
                count["type"] += 1
            elif twins.find(job["company"], job["title"], job["location"]):
                count["twin"] += 1
            else:
                seen_keys.add(key)
                job.update(key=key, found=found, employment=C.employment_label(job["job_type"]))
                kept.append(job)
        for job in kept:  # a board's own postings are compared with the earlier boards only
            twins.add(job["company"], job["title"], job["location"])
        new_jobs += kept
        totals["scraped"] += len(got)
        totals["new"] += len(kept)
        dropped = ", ".join(f"{n} {why}" for why, n in count.items() if n)
        say(f"{board}: {len(got)} scraped, {len(kept)} new" + (f" ({dropped} dropped)" if dropped else "")
            + (f" ERROR {error}" if error else ""))

    if failed == len(boards) and not totals["scraped"]:
        raise Stop("every board failed; see the ERROR lines above")
    if dry_run:
        say(f"total: {totals['scraped']} scraped, {totals['new']} would be new (dry run, nothing written)")
        return 1 if failed else 0

    written = 0
    for job in new_jobs:
        folder = root / "Applications" / C.folder_name(job["key"], job["company"], job["title"])
        try:
            folder.mkdir(parents=True)  # never into an existing folder
        except FileExistsError:
            say(f"WARNING {folder.name}: folder exists; skipped")
            continue
        (folder / "job.md").write_text(C.job_md(job), encoding="utf-8")
        tracker.add_row({"Job URL": job["url"], "Status": "Found", "Company": job["company"], "Job Title": job["title"],
                         "Date Found": found, "Location": job["location"], "Source": job["source"]})
        written += 1
    if written:
        try:
            tracker.save()
        except TrackerError as exc:
            raise Stop(f"{exc} The {written} new folders are in Applications/; /ohmyjob:prep-jobs adds their rows at Found.") from exc
    say(f"total: {totals['scraped']} scraped, {written} new jobs at Found" + ("; run /ohmyjob:prep-jobs" if written else ""))
    return 1 if failed else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="ohmyjob scrape", description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count what would be new; write nothing")
    args = parser.parse_args(argv)
    try:
        root = W.find_root()
        return run(root, W.load_config(root)["search"], args.dry_run)
    except (W.WorkspaceError, Stop) as exc:
        say(f"FATAL {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
