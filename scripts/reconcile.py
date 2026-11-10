"""Deterministic reconciliation of Job_Tracker.xlsx against the workspace folders.

Every job folder is named `<job_key>_<company>_<title>` by the tooling that creates it: a LinkedIn job id, or the
15-digit number job_key() makes from the address of a job found on another board. A folder is matched to a tracker row
by comparing that leading id with job_key() of the row's Job URL. Folders without a leading id are ignored.

The rule, for every OPEN row (Found, Matched, Resume Built, Resume Failed, Pending Review, Needs Attention):
  - its folder is in Pending-Review/ or Needs-Attention/: the row becomes Submitted and the folder moves to Submitted/;
  - its folder is already in Submitted/: the row becomes Submitted (nothing to move);
  - its folder is in Applications/: the row is left alone (the job is not reviewed yet);
  - no folder anywhere: the row becomes Deleted.
Rows that are already Deleted or Skipped-LowFit are never written. A Submitted row whose folder is still in
Pending-Review/ or Needs-Attention/ (an earlier run stopped after saving) only gets the folder moved. All terminal rows
take part in matching and in the invariant. A row whose id is shared by several rows, or whose folder is claimed twice, is left untouched.

Invariant: the number of Submitted rows equals the number of folders in Submitted/ (after the planned changes).
Default is a dry run; --apply writes the tracker and moves the folders. The dry run prints a plan digest; with
`--apply --expect <digest>` the run refuses (nothing written) when the plan has changed since.

Exit codes: 0 clean, 1 done but some folder is unresolved, 2 invariant failed (nothing written), 3 fatal.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import workspace as W
from tracker import STATUSES, Tracker, TrackerError

SCAN_ROOTS = [W.APPLICATIONS, W.PENDING_REVIEW, W.NEEDS_ATTENTION, W.SUBMITTED]
REVIEWED_ROOTS = (W.PENDING_REVIEW, W.NEEDS_ATTENTION)  # a folder here means the user reviewed and submitted it

TERMINAL_STATUSES = {"Submitted", "Deleted", "Skipped-LowFit"}
OPEN_STATUSES = set(STATUSES) - TERMINAL_STATUSES

FOLDER_ID_RE = re.compile(r"^(\d+)_")


def fatal(message: str) -> None:
    print(f"FATAL: {message}", file=sys.stderr)
    sys.exit(3)


def date_found_sort_key(row: dict) -> datetime:
    value = row.get("Date Found")
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except ValueError:
            continue
    return datetime.max


@dataclass
class FolderEntry:
    path: Path
    root: Path
    key: str


@dataclass
class UnresolvedFolder:
    path: Path
    reason: str


def scan_folders() -> list[FolderEntry]:
    entries: list[FolderEntry] = []
    for base in SCAN_ROOTS:
        if not base.is_dir():
            continue
        for sub in sorted(p for p in base.iterdir() if p.is_dir()):
            m = FOLDER_ID_RE.match(sub.name)
            if m is not None:
                entries.append(FolderEntry(path=sub, root=base, key=m.group(1)))
    return entries


def resolve(entries: list[FolderEntry], rows: list[dict]):
    """Match folders to rows. Returns (folder -> row, unresolved folders, entangled row indices, duplicate-row notes)."""
    by_key: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_key[row["_key"]].append(row)

    resolved: dict[Path, dict] = {}
    unresolved: list[UnresolvedFolder] = []
    entangled: set[int] = set()
    duplicate_notes: list[tuple[dict, str]] = []
    seen_duplicate_keys: set[str] = set()

    for entry in entries:
        matches = by_key.get(entry.key, [])
        if len(matches) == 1:
            resolved[entry.path] = matches[0]
        elif not matches:
            unresolved.append(UnresolvedFolder(entry.path, "no-match"))
        else:
            unresolved.append(UnresolvedFolder(entry.path, "ambiguous-row"))
            entangled.update(r["_index"] for r in matches)
            if entry.key not in seen_duplicate_keys:
                seen_duplicate_keys.add(entry.key)
                oldest = min(matches, key=date_found_sort_key)
                for row in matches:
                    if row is not oldest and row["Status"] in OPEN_STATUSES:
                        duplicate_notes.append((row, oldest["Status"] or ""))

    # A row claimed by two folders (cannot happen with unique keys; kept as a safety net).
    row_to_folders: dict[int, list[Path]] = defaultdict(list)
    for folder, row in resolved.items():
        row_to_folders[row["_index"]].append(folder)
    final: dict[Path, dict] = {}
    for folder, row in resolved.items():
        if len(row_to_folders[row["_index"]]) > 1:
            entangled.add(row["_index"])
            unresolved.append(UnresolvedFolder(folder, "shared-folder"))
        else:
            final[folder] = row
    return final, unresolved, entangled, duplicate_notes


@dataclass
class Change:
    row: dict
    new_status: Optional[str]  # None: leave Status alone, append the note only
    note_line: str
    move_entry: Optional[FolderEntry]


def plan_changes(rows, final_resolved, entangled, entry_by_path, duplicate_notes) -> list[Change]:
    row_to_folder = {row["_index"]: path for path, row in final_resolved.items()}
    today = date.today().isoformat()
    changes: list[Change] = []

    for row in rows:
        if row["_index"] in entangled:
            continue
        folder = row_to_folder.get(row["_index"])
        if row["Status"] == "Submitted":
            # An earlier run saved the tracker but did not finish the move: finish it now.
            if folder is not None and entry_by_path[folder].root in REVIEWED_ROOTS:
                changes.append(Change(row, None, "", entry_by_path[folder]))
            continue
        if row["Status"] not in OPEN_STATUSES:
            continue
        if folder is None:
            changes.append(Change(row, "Deleted", f"Reconciled {today}: no folder found, marked Deleted", None))
            continue
        entry = entry_by_path[folder]
        if entry.root == W.APPLICATIONS:
            continue  # not reviewed yet
        move = entry if entry.root in REVIEWED_ROOTS else None
        changes.append(Change(row, "Submitted", f"Reconciled {today}: folder found in {entry.root.name}/, marked Submitted", move))

    for row, older_status in duplicate_notes:
        stamp = f"the row previously exists with status={older_status}"
        if stamp in (row["Notes"] or ""):
            continue  # already annotated; do not repeat on every run
        changes.append(Change(row, None, f"Reconciled {today}: {stamp}", None))
    return changes


def compute_invariant(rows, changes, entries, final_resolved):
    new_status = {c.row["_index"]: c.new_status for c in changes if c.new_status is not None}
    submitted_rows = [r for r in rows if new_status.get(r["_index"], r["Status"]) == "Submitted"]
    final_folders = {e.path for e in entries if e.root == W.SUBMITTED} | {
        c.move_entry.path for c in changes if c.move_entry is not None
    }
    submitted_ids = {r["_index"] for r in submitted_rows}
    folder_of = {row["_index"]: path for path, row in final_resolved.items()}
    rows_without_folder = [r for r in submitted_rows if folder_of.get(r["_index"]) not in final_folders]
    folders_without_row = sorted(
        f for f in final_folders if f not in final_resolved or final_resolved[f]["_index"] not in submitted_ids
    )
    return len(submitted_rows), len(final_folders), len(submitted_rows) == len(final_folders), rows_without_folder, folders_without_row


def plan_digest(changes: list[Change]) -> str:
    """A short hash of what the plan would do (rows, statuses, moves), not of the dated note text."""
    parts = sorted(
        f"{c.row['_index']}|{c.new_status}|{c.move_entry.path if c.move_entry else ''}|{'n' if c.note_line else ''}"
        for c in changes
    )
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:12]


def build_report(apply_mode, applied, changes, unresolved, invariant) -> str:
    count_rows, count_folders, passed, rows_without_folder, folders_without_row = invariant
    lines: list[str] = []
    if not apply_mode:
        lines.append("DRY RUN - no changes written")
    elif not applied:
        lines.append("NOTHING APPLIED - " + ("the invariant failed" if not passed else "no changes planned"))
    for c in changes:
        who = f"  sheet row {c.row['_index'] + 1}: {c.row['Company'] or '?'} - {c.row['Job Title'] or '?'}: "
        if c.new_status is None and c.move_entry is not None:
            lines.append(who + f"already Submitted, folder moves from {c.move_entry.root.name}/ to {W.SUBMITTED.name}/")
        elif c.new_status is None:
            continue
        elif c.move_entry is not None:
            lines.append(who + f"{c.row['Status']} -> {c.new_status} (folder in {c.move_entry.root.name}/ moves to {W.SUBMITTED.name}/)")
        elif c.new_status == "Submitted":
            lines.append(who + f"{c.row['Status']} -> Submitted (already in {W.SUBMITTED.name}/)")
        else:
            lines.append(who + f"{c.row['Status']} -> {c.new_status} (no folder)")
    for u in unresolved:
        lines.append(f"  unresolved ({u.reason}): {u.path.parent.name}/{u.path.name}")
    if not apply_mode or applied:
        lines.append(f"Rows -> Submitted: {sum(1 for c in changes if c.new_status == 'Submitted')}")
        lines.append(f"Rows -> Deleted:   {sum(1 for c in changes if c.new_status == 'Deleted')}")
        lines.append(f"Folders moved:     {sum(1 for c in changes if c.move_entry is not None)}")
        lines.append(f"Duplicate-row notes added: {sum(1 for c in changes if c.new_status is None and c.move_entry is None)}")
    lines.append(f"Invariant: {count_rows} Submitted rows == {count_folders} Submitted folders  {'PASS' if passed else 'FAIL'}")
    for r in rows_without_folder:
        lines.append(f"  Submitted row without a folder in {W.SUBMITTED.name}/: sheet row {r['_index'] + 1}, {r['Company'] or '?'} - {r['Job Title'] or '?'}")
    for f in folders_without_row:
        lines.append(f"  folder in {W.SUBMITTED.name}/ without a Submitted row: {f.name}")
    lines.append(f"Unresolved folders: {len(unresolved)}")
    lines.append(f"Plan digest: {plan_digest(changes)}")
    return "\n".join(lines)


def apply_changes(tracker: Tracker, changes: list[Change]) -> None:
    """Save the tracker first (a stale file stops the run with nothing moved), then move the folders."""
    moves = [(c.move_entry.path, W.SUBMITTED / c.move_entry.path.name) for c in changes if c.move_entry is not None]
    for _, dest in moves:
        if dest.exists():
            fatal(f"Move destination already exists, refusing to overwrite: {dest}")
    edited = False
    for c in changes:
        if c.new_status is not None:
            tracker.set_status(c.row["_index"], c.new_status, c.note_line)
            edited = True
        elif c.note_line:
            tracker.set_status(c.row["_index"], c.row["Status"], c.note_line)
            edited = True
    if edited:
        tracker.save()
    if moves:
        W.SUBMITTED.mkdir(parents=True, exist_ok=True)
    left = []
    for src, dest in moves:
        try:
            shutil.move(str(src), str(dest))
        except OSError as exc:
            # the tracker is saved already: the folders that stayed are listed, and the next run moves them
            left.append(f"{src.name} ({exc.strerror or exc})")
    if left:
        print(f"WARNING {len(left)} folder(s) were not moved to Submitted/ and stay where they are; the next "
              f"reconcile moves them: {', '.join(left)}")


def run(args) -> int:
    try:
        tracker = Tracker(W.TRACKER)
    except TrackerError as exc:
        fatal(str(exc))
    rows = tracker.rows()
    entries = scan_folders()
    entry_by_path = {e.path: e for e in entries}

    final_resolved, unresolved, entangled, duplicate_notes = resolve(entries, rows)
    changes = plan_changes(rows, final_resolved, entangled, entry_by_path, duplicate_notes)
    invariant = compute_invariant(rows, changes, entries, final_resolved)

    if args.apply and args.expect is not None and args.expect != plan_digest(changes):
        fatal(f"The plan changed since the dry run (expected digest {args.expect}, now {plan_digest(changes)}). "
              "Nothing was applied; run the dry run again.")
    applied = False
    if invariant[2] and args.apply and changes:
        try:
            apply_changes(tracker, changes)
        except (TrackerError, OSError) as exc:
            fatal(str(exc))
        applied = True

    print(build_report(args.apply, applied, changes, unresolved, invariant))
    if not invariant[2]:
        return 2
    return 1 if unresolved else 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="reconcile")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="report only (the default)")
    mode.add_argument("--apply", action="store_true", help="write the tracker and move the folders")
    parser.add_argument("--expect", metavar="DIGEST", help="with --apply: refuse when the plan digest differs")
    args = parser.parse_args(argv)
    if args.expect is not None and not args.apply:
        parser.error("--expect needs --apply")
    try:
        return run(args)
    except Exception as exc:  # noqa: BLE001 - exit 1 means "unresolved folders", so an error must not look like it
        fatal(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    sys.exit(main())
