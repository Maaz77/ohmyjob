---
name: help
description: Print the ohmyjob commands with their use-cases, the ohmyjob.json field table and the job status life cycle. Use when the user asks what ohmyjob can do, how to configure ohmyjob.json, what a job status means, or types /ohmyjob:help.
allowed-tools: Read
---

1. Read `${CLAUDE_PLUGIN_ROOT}/README.md` (Read tool). It is the only source: do not guess.
2. Print, in this order, copied from the README and shortened to plain tables or lists:
   - The four stages of a round ("The four stages"): the stage, its command and what it gives the user, one line each.
   - Every command with its arguments and its use-case ("All the commands"): the whole table, one line each. Keep the
     arguments exactly as the README gives them (`/ohmyjob:apply-jobs` takes `--selected "<name, name>"` or
     `--top-k-match <N>` and not both; `/ohmyjob:build-resumes` takes an optional `file.yaml`;
     `/ohmyjob:build-job-resumes` requires a comma-separated list of job folder names; every other command takes
     none). Keep the closing note about `build-resumes` working in `Default_Resume_Files/` while
     `build-job-resumes` works in the job folders.
   - The three workspace files the application runs use ("What the workers learn"): `ats/`, `Dream_Report.md` and
     `Worker_Limits.json` with its table.
   - The settings table ("Settings: `ohmyjob.json`"): every field with its type, default and choices, all choices in
     full (sources, country, employment_types, languages, title_filter). Mark the required fields.
   - The job status life cycle table ("Job status life cycle"): each status, what sets it and what comes next.
3. End with one line: edit `ohmyjob/ohmyjob.json` (plain JSON, no comments; this table is the guide), and the commands
   refuse to run while an `OHMYJOB-TEMPLATE` marker line is left in a file.
