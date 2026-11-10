---
name: apply-jobs-run
description: Internal step of /ohmyjob:apply-jobs that runs the application queue in the application-master agent. Use only when the apply-jobs skill says so.
user-invocable: false
context: fork
agent: ohmyjob:application-master
background: false
---

Run the application queue now.
Options from /ohmyjob:apply-jobs (none when empty): $ARGUMENTS
