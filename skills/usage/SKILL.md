---
name: usage
description: Show the saved cost log of every /ohmyjob:prep-jobs, /ohmyjob:apply-jobs and /ohmyjob:continue-applying run, per run and per unit of work, in API-equivalent dollars. Use only when the user types /ohmyjob:usage.
disable-model-invocation: true
allowed-tools: Bash(ohmyjob *)
---

Run `ohmyjob usage` in this session (no agent runs it) and show the output verbatim. It takes no arguments: ignore
anything the user typed after the command. A line that starts with `ohmyjob: ` or `FATAL` is an error: show it and stop.
