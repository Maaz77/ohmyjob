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
