---
name: help
description: Print the ohmyjob commands with their use-cases, the ohmyjob.json field table and the job status life cycle. Use when the user asks what ohmyjob can do, how to configure ohmyjob.json, what a job status means, or types /ohmyjob:help.
allowed-tools: Read
---

1. Read `${CLAUDE_PLUGIN_ROOT}/README.md` (Read tool). It is the only source: do not guess.
2. Print, in this order, copied from the README and shortened to plain tables or lists:
   - The commands with their use-cases: the daily loop table ("What it does: the daily loop") and the table of the other commands (`/ohmyjob:setup`, `/ohmyjob:build-resumes [file.yaml]`, `/ohmyjob:master-resume`, `/ohmyjob:help`), one line each: when to use it and what it does.
   - The `ohmyjob.json` table ("Configuration: `ohmyjob.json`"): every field with type, default and choices, all choices in full (sources, country, employment_types, languages, title_filter). Mark the required fields.
   - The job status life cycle table ("Job status life cycle"): each status, what sets it, what comes next.
3. End with one line: edit `ohmyjob/ohmyjob.json` (lines that start with `//` are comments) and the commands refuse to run while a `OHMYJOB-TEMPLATE` marker line is left in a file.
