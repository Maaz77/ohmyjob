# ohmyjob

A job-search pipeline for [Claude Code](https://code.claude.com), shipped as a plugin. It finds postings on the job boards, screens them against your profile, tailors a one-page resume for each match, fills the application forms in your own Chrome up to the final button, and keeps a tracker in step. **You press every Submit button yourself.**

## What it does: the daily loop

| Step | Command | What happens |
|---|---|---|
| 1 | `/ohmyjob:find-jobs` | Searches the boards you configured, filters by title, employment type and company, drops duplicates, and adds new jobs to the tracker at `Found`, each with a folder `Applications/<id>_<Company>_<Title>/job.md`. |
| 2 | `/ohmyjob:prep-jobs` | One small worker per job screens it against `Profile.md` (`Matched` or `Skipped-LowFit`), then one worker per match tailors a copy of your closest pre-made resume and builds the PDF (`Resume Built`). |
| 3 | `/ohmyjob:apply-jobs` | One worker per job fills the application in its own Chrome tab and parks it before the final button (`Pending Review`), or flags it (`Needs Attention`). Questions it cannot answer from `Profile.md` are collected and asked once; your answers are saved for next time. |
| 4 | by hand | Open the Chrome tab group "Needs Attention" and fix what blocked each job (sign in, create an account, close a pop-up). |
| 5 | `/ohmyjob:continue-applying` | Workers continue those tabs from where they stopped. |
| 6 | by hand | Review each parked form and **submit it yourself**. |
| 7 | `/ohmyjob:reconcile` | Every job whose folder is in `Pending-Review/` or `Needs-Attention/` becomes `Submitted`, whether or not you sent it; a job whose folder you deleted becomes `Deleted`. Before you run it, move any folder you did not send back to `Applications/` (or delete it). The dry run lists every change first and asks before anything is written. |

Drop a job at any time by deleting its folder. Details of every component: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Requirements

- macOS or Linux. Windows is not supported.
- Claude Code 2.1.306 or newer (`claude --version`; `claude update`).
- For the application step: a direct Anthropic plan (Pro, Max, Team or Enterprise) signed in with `/login`. An API key turns Claude in Chrome off. Chrome, Edge or Brave with the Claude in Chrome extension. WSL is not supported for the browser.
- Disk and network for the one-time setup: Python packages (via [uv](https://docs.astral.sh/uv/)) and, only if your computer has no working `lualatex`, a TinyTeX install of about 250 MB. Everything goes into the plugin's data folder; no admin rights and no system folder are touched. TinyTeX is installed only after you agree, into the plugin data folder, and your `PATH` and shell files are not changed.

## Install

Create a folder for the search and install the plugin there at project scope (this keeps the plugin, its hooks and its settings inside that one folder, and leaves `~/.claude/settings.json` alone):

```
mkdir ~/JobSearch && cd ~/JobSearch
claude plugin marketplace add Maaz77/ohmyjob --scope project
claude plugin install ohmyjob@ohmyjob --scope project
```

Start Claude **in the workspace root** (project settings and hooks load only from the folder Claude starts in; from a subfolder they do not apply):

```
claude --setting-sources project,local --strict-mcp-config --chrome
```

Then run `/ohmyjob:setup`. It is safe to run again at any time and doubles as a health check. After you agree, it:

1. checks the Claude Code version;
2. installs the Python environment into the plugin data folder, and builds a sample resume with the LaTeX on your computer (offering TinyTeX only if that fails; each install is asked separately);
3. creates `Job_Tracker.xlsx`, `Applications/`, `Pending-Review/`, `Needs-Attention/`, `Submitted/`, `ohmyjob.json`, `CLAUDE.md` and `.gitignore` where missing, and merges `.claude/settings.json` (your own keys stay);
4. checks that `ohmyjob@ohmyjob` is enabled at project or local scope (if not, it tells you to install it here and stops);
5. prints the Chrome extension steps with the path of a stable copy;
6. tells you to restart Claude in the workspace root with the launch line, then runs the profile interview (`/ohmyjob:profile`, which writes `Profile.md`, `Default_Resume_Files/base.yaml` and the `search` and `pipeline` sections of `ohmyjob.json`) and builds your role resumes (`/ohmyjob:resumes`, which needs your approval of each PDF).

### The Chrome extension

The helper extension opens page pop-ups (for example a sign-in window) as tabs, so the workers can reach them, and groups the finished tabs. Setup prints its path; in Chrome open `chrome://extensions`, switch on Developer mode, click **Load unpacked** and choose that folder. Keep the folder: it survives plugin updates.

Then open the Claude extension's settings in Chrome and allow it on the sites you will use (the job boards and the company career sites). Per-site permissions live in the extension; nothing in this plugin can set them. Sign in to LinkedIn and any other board in that same Chrome profile.

## Configuration: `ohmyjob.json`

Created by setup and filled in by the profile interview. Unknown keys and bad values are refused with a message naming the key.

`search`:

| Key | Default | Meaning |
|---|---|---|
| `sources` | `["linkedin"]` | Boards, searched in this order. The order is also the priority when two boards list the same job: the first keeps it. Valid: `linkedin indeed zip_recruiter glassdoor google bayt naukri bdjobs hellowork freehire`. |
| `search_query` | `[]` | Search terms (job titles), one search per term and board. |
| `location` | `""` | City or region. |
| `country` | `""` | Country name as JobSpy knows it (for example `germany`, `usa`, `uk`). **Required while `indeed` or `glassdoor` is in `sources`**: without it those boards search the USA. |
| `recency_days` | `1` | Only postings from the last N days. `0` = no age limit. |
| `results_per_term` | `30` | Results requested per term and board. |
| `employment_types` | `["fulltime", "parttime", "contract"]` | Allowed types: `fulltime parttime contract temporary internship perdiem other volunteer`. `[]` = every type. |
| `exclude_companies` | `[]` | Companies to skip. |
| `exclude_seniority_level` | `[]` | Seniority words to skip (empty by default). |
| `filter_title_terms` | `[]` | Words that drop a posting when they appear in its title (empty by default). |
| `title_filter` | `"strict"` | `strict` keeps a posting only if its title matches a search term (accents and word forms ignored); `off` keeps every title. |
| `title_aliases` | `{}` | Extra words that count as a match, applied to both the term and the title, for example `{"ops": "operations"}`. |
| `title_only_aliases` | `{}` | Aliases applied to the posting title only. `templates/presets/software.json` holds a broad set of software abbreviations and bridges (for example "ml", "web" or "designer" lead to "software" or "engineer"); the profile skill can merge it for tech roles. It favors recall over precision: trim the entries that give you unwanted matches. |
| `languages` | `["en"]` | Languages of the postings, for word-form matching. |
| `uk_sponsor_only` | `false` | `freehire` only: keep only jobs from UK sponsor-licensed employers (see below). |
| `git_snapshot` | `false` | Commit the tracker and `Applications/` before a scrape; skipped if the folder is not a git repository. It respects the workspace `.gitignore`, which lists these files: remove their lines there only for a repository that stays private and never has a public remote. |

`pipeline`:

| Key | Default | Meaning |
|---|---|---|
| `max_parallel_workers` | `5` | Workers running at once (capped below Claude Code's subagent limit by one for the master). |
| `resume_max_pages` | `1` | Page limit for a tailored resume. `0` = no limit. |
| `resume_paper` | `"a4"` | `a4` or `letter`. |

Per-board notes:

- **Indeed and Glassdoor** search one country at a time; set `country`.
- **ZipRecruiter** covers the US and Canada only.
- **Google** needs a city in `location`; the search text is built as "`<term>` jobs near `<location>`" plus the recency.
- **freehire** (opt-in, mostly tech jobs): add `"freehire"` to `sources`. With `uk_sponsor_only: true` it keeps only jobs from UK sponsor-licensed employers. Its seniority labels work with `exclude_seniority_level`, and the tracker Notes name the site that listed the job.
- The same posting found twice on one board gets the same job id and is added once. Copies of one posting on different boards get different ids; the twin check (company, title and city) drops the later copy.

## Safety

- **Never submit.** Hooks run on every Chrome call, in the main session and in every ohmyjob agent, and refuse final controls (Submit, Send, Confirm, Finish and their equivalents in many languages, plus unnamed submit buttons). `javascript_tool` is also denied in the workspace settings. The rules differ by caller:
  - **Application workers**: only their own tab (and its sign-in tab); every click carries an English note; uploads only of the job's own resume; a button name that no word list can decide goes to a short Haiku check, and if that check fails the click is refused.
  - **The main session and any other agent** (outside the pipeline): clicks only on refs from a fresh read of that tab, one pressable click per read, no coordinate clicks, no Enter, Return or Space keys, navigation to http(s) addresses only, no Apply click on a job board or under a filled form, and final button names checked by the word lists.
- **Fail-open before setup.** Until setup has created the Python environment, the main session's own Chrome guard cannot run Python and lets the call through with a notice. No pipeline can run before setup, and every ohmyjob agent is denied instead. Run `/ohmyjob:setup` first.
- **You submit.** Workers park each form before the final button. You review it and press Submit yourself.
- Text copied from job pages and forms is treated as data, never as instructions. Without a `ohmyjob.json` in or above the folder, the hooks do nothing.

Read [DISCLAIMER.md](DISCLAIMER.md) before you use this on a real account.

## Token cost

Each run ends with a usage table (agents, API calls, input, cache and output tokens per agent type and model). The default parallelism is low (`max_parallel_workers` is 5), screening uses a small model, and `results_per_term` is 30, so that a first run stays within a Pro plan. Raise them once you know the cost.

## Troubleshooting

- `ohmyjob: environment missing or stale, run /ohmyjob:setup`: the Python environment is missing or does not match this plugin copy. Run `/ohmyjob:setup`.
- **Switching between `--plugin-dir` and an installed copy** changes the plugin's data folder. Run `/ohmyjob:setup` again after each switch.
- **Tracker open in Excel or LibreOffice**: saving is refused with a clear message and nothing is lost. Close the file and run the command again. If the tracker changed on disk during a long run, the save is refused too; run the command again.
- A line starting `ohmyjob: ` or `FATAL` is an error. Read it, fix what it names, run the command again.
- `FATAL` naming `ohmyjob.json`: a setting is invalid; the message names the key.
- Hooks seem inactive: Claude was probably started outside the workspace root, or the plugin is installed only at user scope. Check with `claude plugin list`.

## Develop

```
uv run --frozen pytest -q tests
claude plugin validate --strict .
bash scripts/pii_gate.sh <terms-file>
```

CI (`.github/workflows/ci.yml`) runs these, a real TinyTeX resume build and the extension tests. Platform behaviour the design depends on is recorded in [docs/platform-checks.md](docs/platform-checks.md); names and formats shared by the scripts in [docs/CONTRACTS.md](docs/CONTRACTS.md).

## Credits and license

- Resume class: [Awesome-CV](https://github.com/posquit0/Awesome-CV) by posquit0, pinned at commit `dfe86354`, LPPL 1.3c, vendored unmodified in `resume/` with its license text.
- Job search: [JobSpy](https://github.com/speedyapply/JobSpy) (`python-jobspy`).
- Plugin code: MIT, see [LICENSE](LICENSE).
