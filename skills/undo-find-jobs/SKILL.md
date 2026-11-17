---
name: undo-find-jobs
description: Undo the latest find-jobs run. The rows that run added to the tracker (still at Found, or Deleted) are removed and their job folders in Applications/ are deleted; a backup of the tracker is kept. Shows the counts first and asks before it changes anything. Use only when the user types /ohmyjob:undo-find-jobs.
disable-model-invocation: true
argument-hint: "[YYYY-MM-DD | 'YYYY-MM-DD HH:MM']"
allowed-tools: Bash(ohmyjob *)
---

Undo a find-jobs run now. `$ARGUMENTS` may hold a `Date Found` prefix (`2026-11-17` or `2026-11-17 09:30`) that picks the run; without it the run is every row of the newest `Date Found` date.

A line of output that starts with `ohmyjob: ` or `FATAL` (`FATAL: ...`) is an error: show that line as it is, say what it asks for, and stop. A message that the tracker changed on disk or is open in Excel/LibreOffice means: close it or run again; do not retry in a loop. You never edit the tracker or delete a folder yourself: only `ohmyjob undo-find-jobs` does.

What the rule does:

- A row of the run at `Found` or `Deleted` is removed; the folder in `Applications/` whose leading id matches it is deleted (an auto-excluded `Deleted` row has none).
- A row of the run that moved on (`Matched`, `Skipped-LowFit`, `Resume Built`, `Pending Review`, `Needs Attention`, `Submitted`) is kept with its folder. `Pending-Review/`, `Needs-Attention/` and `Submitted/` are never touched.
- Rows added by `/ohmyjob:add-job` on the same date are part of the run; a `YYYY-MM-DD HH:MM` argument narrows it to one minute.

## Steps

1. Run the dry run. It writes nothing: `ohmyjob undo-find-jobs $ARGUMENTS`
2. Show the user the report as printed: the run, rows removed by status, folders deleted, rows kept, the sample, and note the `Plan digest` line. Say plainly that the rows and folders are deleted for good, and that the tracker is copied to `Job_Tracker.xlsx.pre-undo` first (the folders have no backup).
3. Ask the user to confirm (AskUserQuestion, options "Delete them" / "Cancel"). If the run is not the one they meant, tell them to give a date or minute and run again. Do not go on without a clear yes.
4. After a yes, run `ohmyjob undo-find-jobs $ARGUMENTS --apply --expect <digest>` with the digest you showed. If it refuses because the plan changed, show the new dry run and ask again. Show the final counts and the backup path.
