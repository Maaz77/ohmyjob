---
name: find-jobs
description: Find new jobs on the job boards you configured (search.sources in ohmyjob.json), keep the ones that fit your search terms and filters, and add them to the tracker at "Found". Use only when the user types /ohmyjob:find-jobs or a scheduled task runs it.
disable-model-invocation: true
argument-hint: "[verbose]"
allowed-tools: Bash(ohmyjob *), Monitor, TaskStop
---

Find new jobs now.

A line of output that starts with `ohmyjob: ` or `FATAL` (`FATAL: ...`) is an error: show that line as it is, say what it asks for, and stop. Do not retry in a loop and do not work around it.

The scrape refuses (one `FATAL:` line naming the file) while `ohmyjob.json` or `Profile.md` still has the template marker: the user must replace the mock data and delete the marker first.

0. Read `$ARGUMENTS`. When it contains `verbose`, `-v` or `--verbose`, the user wants live progress: run `ohmyjob scrape --verbose` in step 1 and, right after starting it, arm `Monitor({command: "tail -F -n +1 <the output file path from the Bash result> | grep --line-buffered -E 'TERM_|BOARD_|FREEHIRE_|WARNING|ERROR|FATAL'", description: "ohmyjob scrape progress", timeout_ms: 1800000})`, with the absolute path the Bash tool result gives after "Output is being written to" (that file holds stdout and stderr); if the monitor expires while the scrape still runs, arm it again on the same file. Relay each line that arrives to the user in short form (for example "linkedin: searching 'Data Analyst'", "linkedin: 25 results") and stop the monitor with `TaskStop` once the scrape command exits. A `LEVEL | Source | ...` line is live progress, not a report line: the report is still the scrape command's own stdout (step 2). Without the argument, use neither `--verbose` nor Monitor.
1. Run `ohmyjob scrape` as a background Bash command (`run_in_background: true`). A search can take several minutes. While it runs, do not start anything else; when it ends, read its output. (`ohmyjob scrape --dry-run` counts what would be new and writes nothing. Add `--git-snapshot` ONLY when the user asked for a git snapshot: it commits the tracker and `Applications/` before the run, only what `.gitignore` does not ignore (the workspace template ignores both, so the user removes those lines to version them), and is skipped when the folder is not a git repository.)
Exit codes of `ohmyjob scrape`: 0 all boards ran; 1 a `FATAL:` line (nothing more was done); 2 the run finished but at least one board reported an `ERROR`.
2. Read the output. It holds one line per board (`<board>: <n> scraped, <n> new (<n> <reason> dropped)`), `WARNING` lines, and a `total:` line. Tell the user, in a few short lines:
   - the total of new jobs, and per board how many were scraped and how many are new;
   - why jobs were dropped, if many were (`title`: the title did not match a search term; `known`: already in the tracker or a folder; `twin`: the same job on an earlier board or a recent tracker row; `type`: employment type; `lang`: a freehire posting written in a language your `# Languages` section does not list; `level`: a seniority word of `exclude_seniority_level` in the title or the board's level; `term`: a `filter_title_terms` entry in the title; `junk`: the posting reads as a scam or spam (fees, chat-app contact, commission-only, personal email); `duplicate`: the same title, company and city under another id; `dup id`: the same job met under two search terms; `failed`: LinkedIn's detail page did not load (rate limit), so the job is left for the next run; `bad`: a posting without a title or with an address that cannot be turned into a job id);
   - `auto-excluded` jobs: their company is in `exclude_companies`; each is recorded as Deleted with a note and gets no folder;
   - every `WARNING` and `ERROR` text as printed (a `detail pages did not load` warning means LinkedIn rate limited the run: say so and suggest fewer results or terms). A board with `ERROR` (for example a block by the site) failed for at least one search term; it may still have added jobs from the other terms, and the other boards ran.
3. If jobs were added, tell the user that `/ohmyjob:prep-jobs` screens them and builds the resumes. Do not start it yourself.

What the settings do (all in the `search` section of `ohmyjob.json`):
- `sources` is the list of boards, searched one after the other in that order. The order is also the priority when two boards list the same job: the first one keeps it.
- `freehire` (freehire.me, keyless, searched by country; a `WARNING freehire: no country found ...` line means neither `location` nor `country` names one, and it was skipped) is a source like the others. `uk_sponsor_only` applies to it only: when true and the country is the UK, only employers on the Home Office list of licensed visa sponsors are searched. It is off by default.
- A freehire job carries freehire's seniority label (intern, junior, middle, senior, lead, staff, principal, c_level) as its level, so `exclude_seniority_level` can drop it. Its tracker Notes say which site listed it.
- `title_filter` `strict` keeps a posting only if its title matches a search term (accents and word forms ignored; built-in aliases for common abbreviations and the software preset are always applied); `off` keeps every title.
- `recency_days` 0 means no age limit.
