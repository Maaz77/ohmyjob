"""Undo the latest find-jobs run: delete the tracker rows it added and their folders in Applications/.

The run = every row whose "Date Found" starts with the given prefix ("YYYY-MM-DD" or "YYYY-MM-DD HH:MM"), by default
the newest date in the sheet. (/ohmyjob:add-job stamps rows the same way, so its rows of that date are part of it;
narrow the prefix to a minute to leave them out.) Only rows still at Found or Deleted go: a row that prep, apply or
reconcile already moved on (Matched, Skipped-LowFit, Resume Built, Pending Review, Submitted ...) is kept, and so is its folder.
A folder goes only when it sits in Applications/ and its leading id is the job_key of a removed row.

Default is a dry run with a plan digest; `--apply --expect <digest>` copies Job_Tracker.xlsx to Job_Tracker.xlsx.pre-undo,
then writes the tracker and deletes the folders. Exit codes: 0 done / dry run, 3 fatal.
"""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from typing import Optional

import workspace as W
from reconcile import FOLDER_ID_RE
from tracker import Tracker, TrackerError

UNDOABLE = ("Found", "Deleted")


def fatal(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    sys.exit(3)


def plan(tracker: Tracker, prefix: Optional[str]):
    """(prefix used, rows to remove, rows of that run that stay, folders to delete)."""
    rows = [r for r in tracker.rows() if r["Date Found"] not in (None, "")]
    if not rows:
        fatal("the tracker has no row with a Date Found")
    prefix = prefix or max(str(r["Date Found"])[:10] for r in rows)
    run = [r for r in rows if str(r["Date Found"]).startswith(prefix)]
    if not run:
        fatal(f"no row has a Date Found starting with '{prefix}'")
    remove = [r for r in run if str(r["Status"] or "").strip() in UNDOABLE]
    keep = [r for r in run if r not in remove]
    keys = {r["_key"] for r in remove} - {r["_key"] for r in keep}  # an id shared with a kept row keeps its folder
    folders = sorted(p for p in W.APPLICATIONS.iterdir()
                     if p.is_dir() and (m := FOLDER_ID_RE.match(p.name)) and m.group(1) in keys) if W.APPLICATIONS.is_dir() else []
    return prefix, remove, keep, folders


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="undo-find-jobs")
    parser.add_argument("run", nargs="?", help="Date Found prefix: YYYY-MM-DD or 'YYYY-MM-DD HH:MM' (default: newest date)")
    parser.add_argument("--apply", action="store_true", help="back up, remove the rows, delete the folders")
    parser.add_argument("--expect", metavar="DIGEST", help="with --apply: refuse when the plan digest differs")
    args = parser.parse_args(argv)
    try:
        tracker = Tracker(W.TRACKER)
        prefix, remove, keep, folders = plan(tracker, args.run)
        digest = hashlib.sha256("\n".join([prefix, *(str(r["_index"]) + r["_key"] for r in remove), *(p.name for p in folders)]).encode()).hexdigest()[:12]
        if args.apply:
            if args.expect != digest:
                fatal(f"--apply needs --expect <digest> from the dry run (now {digest}); the plan may have changed.")
            backup = W.TRACKER.with_name(W.TRACKER.name + ".pre-undo")
            shutil.copy2(W.TRACKER, backup)
            for r in sorted(remove, key=lambda r: -r["_index"]):  # bottom up, so the indexes above stay right
                tracker.sheet.delete_rows(r["_index"] + 1)
            tracker.save()  # before the folders: a failed save leaves everything as it was
            for p in folders:
                shutil.rmtree(p)
        counts = {}
        for r in remove:
            counts[r["Status"]] = counts.get(r["Status"], 0) + 1
        print(f"Undo find-jobs{'' if args.apply else ' (dry run, nothing changed)'}")
        print(f"Run (Date Found) : {prefix}")
        print(f"Rows removed     : {len(remove)} ({', '.join(f'{n} {s}' for s, n in counts.items()) or 'none'})")
        print(f"Folders deleted  : {len(folders)}")
        print(f"Rows kept        : {len(keep)} of that run (already past Found)")
        for r in remove[:5]:
            print(f"  sample: {r['Company']} : {r['Job Title']} [{r['Status']}]")
        if args.apply:
            print(f"Backup           : {backup}")
        else:
            print(f"Plan digest: {digest}")
    except TrackerError as exc:
        fatal(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
