---
name: reconcile
description: Bring the job tracker in line with the folders and close the round. A job whose folder you reviewed (in Pending-Review or Needs-Attention) becomes Submitted and its folder moves to Submitted; a job whose folder you deleted becomes Deleted; every follow-up question left unanswered is dropped. First shows the caveat and asks to go on, then shows a dry run and asks again before it changes anything. Use only when the user types /ohmyjob:reconcile.
disable-model-invocation: true
allowed-tools: Bash(ohmyjob *)
---

# Reconcile the tracker with the folders

Run this after you submitted applications by hand, or deleted folders of jobs you dropped.

What the rule does for every open tracker row (Found, Matched, Resume Built, Resume Failed, Pending Review, Needs Attention):

- Its folder is in `Pending-Review/` or `Needs-Attention/`: the row becomes Submitted and the folder moves to `Submitted/`.
- No folder anywhere: the row becomes Deleted.
- Its folder is already in `Submitted/`: the row becomes Submitted (nothing moves).
- Its folder is in `Applications/`: left alone. `Applications/` is never touched. A job the user applied to entirely by hand while its folder was in `Applications/` must be moved to `Submitted/` by the user before reconcile; it then counts under the rule above.

Rows that are already Deleted or Skipped-LowFit are never changed. A row that is already Submitted but whose folder is still in `Pending-Review/` or `Needs-Attention/` (an earlier run stopped half way) only gets its folder moved.

Closing the round also clears the follow-up questions: every question the application workers asked that the user did not answer (`ohmyjob apply questions`) is dropped, so it does not carry over into the next round. The answers already saved in `Profile.md` are not touched, and a job that runs again asks its question again. Only an apply drops them; a dry run and an invariant failure drop nothing.

## Steps

1. Before you run anything, show the user this caveat and ask whether to go on. Do not run any command until they agree:

   > Reconcile treats every folder in `Pending-Review/` or `Needs-Attention/` as **sent**. It cannot check whether you actually submitted the form. Before going on:
   > - Move the folder of any job you did not send back to `Applications/`, or delete it. Otherwise it is marked Submitted.
   > - Delete the folder of every job you dropped. Reconcile records it as Deleted.
   > - Move the folder of any job you applied to entirely by hand from `Applications/` to `Submitted/`. Reconcile never touches `Applications/`, so otherwise it stays open.
   > - Any follow-up question you have not answered yet is dropped when the round closes, and will not be asked again. Answer the ones you want kept first (`/ohmyjob:apply-jobs` or `/ohmyjob:continue-applying` asks them); the answers already in `Profile.md` stay.
   >
   > Have you done this, and should I go on?

   If the user says no or wants to tidy the folders first, stop here. Run the dry run only after a clear yes.
2. Run the dry run. It writes nothing:
   `ohmyjob reconcile`
3. If any output line starts with `FATAL` or `ohmyjob:`, stop and tell the user the line as it is. A message that the tracker changed on disk or is open in Excel/LibreOffice means: close it or run again; do not retry in a loop.
4. Show the user the report: each row that would change (sheet row, company, title, old and new status, folder move), the unresolved folders, the `Unanswered questions to clear` line (say they are dropped for good), and the invariant line with the rows and folders it names. Note the `Plan digest` line. Exit code 2 (invariant FAIL) means nothing can be written: show the report and stop. Exit code 1 only means some folders are unresolved; the rest can still be applied.
5. Ask the user to confirm. Do not apply without a clear yes. If the list holds a row the user did not expect (for example a folder they never reviewed), tell them to move it back to `Applications/` and run the dry run again.
6. After a yes, run `ohmyjob reconcile --apply --expect <digest>` with the digest you showed. If it refuses because the plan changed, show the new dry run and ask again. Show the report and the final counts: rows marked Submitted, rows marked Deleted, folders moved, unanswered questions cleared.
