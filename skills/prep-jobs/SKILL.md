---
name: prep-jobs
description: Screen every tracker row at "Found" (one Haiku worker per job) and build a tailored resume for every row at "Matched" (one Sonnet worker per job), as a queue (a new worker starts as soon as one finishes). Use only when the user types /ohmyjob:prep-jobs or a scheduled task runs it.
disable-model-invocation: true
allowed-tools: Bash(ohmyjob *)
context: fork
agent: ohmyjob:prep-master
background: false
---

Run the job-prep queue now.

The queue refuses to start while `Profile.md` still has the template marker, and when `Default_Resume_Files/` holds no usable resume (a `<Field>.yaml` with its PDF, or a ready-made `<Field>.pdf`; never `Template.*` nor a file with the marker). A job that matches a ready-made PDF gets that PDF copied into its folder and goes straight to Resume Built, with no resume-worker. If the queue prints a line that starts with FATAL or `ohmyjob: `, show it to the user and stop.
