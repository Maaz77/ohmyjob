---
name: reconcile
description: Bring the job tracker in line with the folders. A job whose folder you reviewed (in Pending-Review or Needs-Attention) becomes Submitted and its folder moves to Submitted; a job whose folder you deleted becomes Deleted. Shows a dry run and asks before it changes anything. Use only when the user types /ohmyjob:reconcile.
disable-model-invocation: true
allowed-tools: Bash(ohmyjob *)
---

# Reconcile the tracker with the folders

Run this after you submitted applications by hand, or deleted folders of jobs you dropped.

What the rule does for every open tracker row (Found, Matched, Resume Built, Resume Failed, Pending Review, Needs Attention):

- Its folder is in `Pending-Review/` or `Needs-Attention/`: the row becomes Submitted and the folder moves to `Submitted/`.
- No folder anywhere: the row becomes Deleted.
- Its folder is already in `Submitted/`: the row becomes Submitted (nothing moves).
- Its folder is in `Applications/`: left alone. `Applications/` is never touched.

Rows that are already Deleted or Skipped-LowFit are never changed. A row that is already Submitted but whose folder is still in `Pending-Review/` or `Needs-Attention/` (an earlier run stopped half way) only gets its folder moved.

## Steps

1. Run the dry run. It writes nothing:
   `ohmyjob reconcile`
2. If any output line starts with `FATAL` or `ohmyjob:`, stop and tell the user the line as it is. A message that the tracker changed on disk or is open in Excel/LibreOffice means: close it or run again; do not retry in a loop.
3. Show the user the report: each row that would change (sheet row, company, title, old and new status, folder move), the unresolved folders and the invariant line with the rows and folders it names. Note the `Plan digest` line. Exit code 2 (invariant FAIL) means nothing can be written: show the report and stop. Exit code 1 only means some folders are unresolved; the rest can still be applied.
4. Ask the user to confirm. Do not apply without a clear yes. If the list holds a row the user did not expect (for example a folder they never reviewed), tell them to move it back to `Applications/` and run the dry run again.
5. After a yes, run `ohmyjob reconcile --apply --expect <digest>` with the digest you showed. If it refuses because the plan changed, show the new dry run and ask again. Show the report and the final counts: rows marked Submitted, rows marked Deleted, folders moved.
