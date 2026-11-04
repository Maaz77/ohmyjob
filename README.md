# ohmyjob

A job-search pipeline for [Claude Code](https://code.claude.com), shipped as a plugin. It finds postings on the job boards, screens them against your profile, tailors a resume of the same length as your source resume for each match, fills the application forms in your own Chrome up to the final button, and keeps a tracker in step. **You press every Submit button yourself.**

## What it does: the daily loop

| Step | Command | What happens |
|---|---|---|
| 1 | `/ohmyjob:find-jobs` | Searches the boards you configured, filters by title, employment type and company, drops duplicates, and adds new jobs to the tracker at `Found`, each with a folder `Applications/<id>_<Company>_<Title>/job.md`. |
| 2 | `/ohmyjob:prep-jobs` | One small worker per job screens it against `Profile.md` (`Matched` or `Skipped-LowFit`), then one worker per match tailors a copy of your closest resume and builds the PDF (`Resume Built`); a match on a ready-made PDF uses it as it is. |
| 3 | `/ohmyjob:apply-jobs` | One worker per job fills the application in its own Chrome tab and parks it before the final button (`Pending Review`), or flags it (`Needs Attention`). Questions it cannot answer from `Profile.md` are collected and asked once; your answers are saved for next time. |
| 4 | by hand | Open the Chrome tab group "Needs Attention" and fix what blocked each job (sign in, create an account, close a pop-up). |
| 5 | `/ohmyjob:continue-applying` | Workers continue those tabs from where they stopped. |
| 6 | by hand | Review each parked form and **submit it yourself**. |
| 7 | `/ohmyjob:reconcile` | Every job whose folder is in `Pending-Review/` or `Needs-Attention/` becomes `Submitted`, whether or not you sent it; a job whose folder you deleted becomes `Deleted`. Before you run it, move any folder you did not send back to `Applications/` (or delete it). The dry run lists every change first and asks before anything is written. |

Other commands:

| Command | Use it to |
|---|---|
| `/ohmyjob:setup` | Prepare the environment and the workspace the first time (safe to run again). |
| `/ohmyjob:build-resumes [file.yaml]` | Build the PDF of every resume yaml in `Default_Resume_Files/` (never `Template*.yaml`), or only the yaml you name. A tailored resume later has exactly the page count of its source PDF. |
| `/ohmyjob:master-resume` | Draft the `# Resume` section of `Profile.md` from your resume files. |
| `/ohmyjob:help` | Print the commands, the `ohmyjob.json` table and the job status life cycle. |

Drop a job at any time by deleting its folder. Details of every component: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prerequisites

Check all of these **before** the install steps:

- macOS or Linux. Windows is not supported.
- Claude Code 2.1.306 or newer (`claude --version`; `claude update`).
- A direct Anthropic plan (Pro, Max, Team or Enterprise) signed in with `/login`. An API key turns Claude in Chrome off.
- Chrome, Edge or Brave with the Claude in Chrome extension installed and signed in. WSL is not supported for the browser. Only `/ohmyjob:apply-jobs` and `/ohmyjob:continue-applying` need the browser; the other commands work without it.
- Both the plugin and Claude in Chrome must be enabled in the session you work in: install the plugin at project scope (below) and start Claude with `--chrome` (the launch line below does).
- Network and disk for the one-time setup (see what it installs, and where, below).

## Install

Create a project folder for the search and install the plugin there at project scope (this keeps the plugin, its hooks and its settings inside that one folder, and leaves `~/.claude/settings.json` alone):

```
mkdir ~/JobSearch && cd ~/JobSearch
claude plugin marketplace add Maaz77/ohmyjob --scope project
claude plugin install ohmyjob@ohmyjob --scope project
```

Start Claude **in that project folder** (project settings and hooks load only from the folder Claude starts in; from a subfolder they do not apply):

```
claude --setting-sources project,local --strict-mcp-config --chrome
```

## `/ohmyjob:setup`

Run `/ohmyjob:setup`. It prepares everything and then tells you what to fill in. It **never interviews you and never writes your real data**; it is safe to run again (it never overwrites a file that exists and changes only `ohmyjob/.ohmyjob/env.sh`).

It checks:

1. the Claude Code version (stops if older than 2.1.306);
2. that `ohmyjob@ohmyjob` is installed and enabled at project or local scope (if not, it tells you how and stops, because the never-submit hooks only protect a project where the plugin is enabled);
3. that the Claude in Chrome tools answer. If they do not, you get a **warning** with the steps to turn them on, and setup continues.

After you agree (each install is asked separately), it installs **only into the plugin's data folder** (`~/.claude/plugins/data/<id>/`, removed with the plugin): the Python environment (`venv/`), uv's own Python and cache (`python/`, `uv-cache/`), `uv` itself only if it is missing (`bin/`), TinyTeX (`tinytex/`) only if no working system LaTeX builds the template (about 250 MB), the LaTeX font caches (`texmf-var/`) and a stable copy of the Chrome helper extension (`chrome-extension/`). Nothing goes to your `PATH`, shell profile, `~/.local`, `~/.cache` or a system folder, and no system TeX tree is touched.

It creates this skeleton in the project folder, each item only when missing, and merges `.claude/settings.json` (your own keys stay):

```
<project folder>/
  .claude/settings.json          plugin enablement, environment, permissions (merged)
  ohmyjob/                       the workspace
    ohmyjob.json                 settings, with mock values
    Profile.md                   candidate, fit preferences, master resume, follow-up answers (mock)
    Job_Tracker.xlsx
    CLAUDE.md                    short guide and the standing never-submit rule
    .gitignore                   keeps the personal files out of git
    Default_Resume_Files/        Template.yaml/.pdf (resume), Template-CV.yaml/.pdf (CV)
    Applications/  Pending-Review/  Needs-Attention/  Submitted/
    .ohmyjob/                    working files (environment, run state, builds); do not edit
```

`ohmyjob.json`, `Profile.md`, `Template.yaml` and `Template-CV.yaml` hold the mock persona "Alex Example". Each carries an `OHMYJOB-TEMPLATE` marker line (first line of `Profile.md` and of both templates; a `// OHMYJOB-TEMPLATE: ...` comment line in `ohmyjob.json`) that you delete once you have replaced the mock data. `Template.pdf` and `Template-CV.pdf` are the sample builds.

Both templates follow the examples of [Awesome-CV](https://github.com/posquit0/Awesome-CV) exactly (red accent, its geometry, header and footer). `Template.yaml` is the resume (`document: resume`: Summary, Work Experience, Honors & Awards, Certificates, Education); `Template-CV.yaml` is the CV (`document: cv`: Education, Skills, Experience, Extracurricular Activity, Honors & Awards, Certificates, Presentation, Writing, Program Committees). A section without entries is left out, and a key the chosen document does not show prints a `WARNING` line instead of failing.

The workspace is found from the project folder, from inside `ohmyjob/`, or from any folder below either.

### The Chrome extension

The helper extension opens page pop-ups (for example a sign-in window) as tabs, so the workers can reach them, and groups the finished tabs. Setup prints its path; in Chrome open `chrome://extensions`, switch on Developer mode, click **Load unpacked** and choose that folder. Keep the folder: it survives plugin updates.

Then open the Claude extension's settings in Chrome and allow it on the sites you will use (the job boards and the company career sites). Per-site permissions live in the extension; nothing in this plugin can set them. Sign in to LinkedIn and any other board in that same Chrome profile.

## What you do next

Work in `ohmyjob/`:

1. **`Profile.md`**: replace the mock data, then delete the marker line (the first line). Sections:
   - `# Fit Preferences`: exactly three fields, `**Seniority / experience level:**`, `**What I'm looking for:**` and `**Dealbreakers:**`. The screening workers judge every posting against them first: the section is printed at the top of each screening brief, a dealbreaker or a clear seniority mismatch gives a low score and a reject reason that quotes your words, and "What I'm looking for" is the main fit criterion.
   - `# Resume`: your master resume, the only source of facts for tailored resumes, so write every true fact, with numbers where you have them. It starts with header lines (`- Name: First Last`, email, phone, address, links; the first name becomes the prefix of your resume file names) and has the sections of the two resume templates: Summary, Work Experience, Education, Skills, Honors & Awards, Certificates, Extracurricular Activity, Presentation, Writing, Program Committees. The template holds one guidance line per section; `/ohmyjob:master-resume` drafts the section from your resume files.
   - `# Follow Up Questions`: answers the application workers reuse, including your work authorization and visa status per country. A numbered list, `1. Question? Answer.`, under the exact heading (`N)` and the old `- ` lists still work). Replace the mock answers; `/ohmyjob:apply-jobs` asks you any question it meets that is missing and saves the answer here with the next number.
2. **`ohmyjob.json`**: set your boards, search terms, location and country, and the optional filters (every field is in the table below), then delete the `// OHMYJOB-TEMPLATE` line.
3. **Resumes** in `Default_Resume_Files/`:
   - Copy `Template.yaml` (or `Template-CV.yaml` for a CV) once per role you apply for and **name each copy by field**, for example `Data-Analyst.yaml`. Replace the mock data and delete the marker line in each. One yaml is one complete resume; there is no shared base file.
   - Optionally add ready-made PDF resumes named by field (for example `Nurse-ICU.pdf`), with no yaml. They are used exactly as they are and never edited.
4. **`/ohmyjob:build-resumes [file.yaml]`** builds a PDF next to every yaml, with the same name (never `Template*.yaml`), or only the yaml you name. It prints one line per file, `<file>: OK` or `<file>: FAIL <reason>`, a `WARNING <file>: still has the template marker` line for a file you have not finished editing, and a last `SUMMARY <n> OK, <n> OVERFLOW, <n> FAIL` line. The exit code is 0 only when every file is OK. It never edits your files.
5. **`/ohmyjob:master-resume`** drafts the `# Resume` section of `Profile.md` from your resume files. A script (no model) merges every resume yaml (the same job, degree or award in several files becomes one entry; bullets and skills are united and near-duplicates keep the longer text) and turns every PDF without a yaml into text; Claude then adds the facts of those texts to the draft, and `ohmyjob resume master --apply` saves it into `Profile.md` after backing up the old file as `.ohmyjob/backup/Profile-<UTC timestamp>.md`. The script prints `DRAFT <path>`, one `PDF <path>` per PDF text and `SUMMARY <n> yaml, <m> pdf`. **Then revise the section by hand**: it is a draft, and tailoring uses it as its only source of facts.
6. Run the daily loop above.

During screening, the usable resumes are every `<name>.yaml` with a built `<name>.pdf` (tailored per job) and every `<name>.pdf` without a yaml (used as is). `Template.*` and any file that still holds the marker are never used. A job that matches a ready-made PDF gets a copy of it in its folder, named `<First>_<Company>_<Title>.pdf`, and goes straight to `Resume Built`; a job that matches a yaml gets a tailored copy (`.yaml` and `.pdf`) from a worker.

### The mock guard

Until you delete the marker lines, the commands that would act on mock data refuse with one clear line naming the file (this is intended):

| Command | Refuses while |
|---|---|
| `/ohmyjob:find-jobs` | `ohmyjob.json` or `Profile.md` has the marker (the mock search terms would be searched and tracked). |
| `/ohmyjob:prep-jobs` | `Profile.md` has the marker, or no usable resume is left (it never uses `Template.*` or a marked file). |
| `/ohmyjob:apply-jobs`, `/ohmyjob:continue-applying` | `Profile.md` has the marker (mock answers would be typed into real forms). |

## Configuration: `ohmyjob.json`

Created by setup with mock values; you edit it (see above). The file is JSON with full-line `//` comments (a line whose first non-blank characters are `//` is ignored); its comment block lists the required fields and, for a field with choices, every valid value. Unknown keys and bad values are refused with a message naming the key. Everything is in the `search` section. `/ohmyjob:help` prints this table.

| Field | Type | Default | Choices / meaning |
|---|---|---|---|
| `sources` | list of text | `["linkedin"]` | **Required in practice.** Boards, searched in this order (the first keeps a job two boards list). Choices: `linkedin indeed zip_recruiter glassdoor google bayt naukri bdjobs hellowork freehire`. |
| `search_query` | list of text | `[]` | **Required.** Job titles, one search per term and board. Nothing is searched while it is empty. |
| `location` | text | `""` | **Required.** City or region, for example `"Berlin, Germany"`. Google needs a city. |
| `country` | text | `""` | **Required while `indeed` or `glassdoor` is in `sources`** (without it they search the USA). Choices: `argentina australia austria bahrain belgium brazil canada chile china colombia "costa rica" "czech republic" denmark ecuador egypt finland france germany greece "hong kong" hungary india indonesia ireland israel italy japan kuwait luxembourg malaysia mexico morocco netherlands "new zealand" nigeria norway oman pakistan panama peru philippines poland portugal qatar romania "saudi arabia" singapore "south africa" "south korea" spain sweden switzerland taiwan thailand türkiye ukraine "united arab emirates" uk usa uruguay venezuela vietnam`. |
| `recency_days` | whole number | `1` | Only postings from the last N days. `0` = no age limit. |
| `results_per_term` | whole number, 1 or more | `30` | Results requested per term and board. |
| `employment_types` | list of text | `["fulltime", "parttime", "contract"]` | Choices: `fulltime parttime contract temporary internship perdiem other volunteer`. `[]` = every type. |
| `exclude_companies` | list of text | `[]` | Companies to skip. |
| `exclude_seniority_level` | list of text | `[]` | Seniority words to skip (for example `"director"`, `"intern"`). |
| `filter_title_terms` | list of text | `[]` | Words that drop a posting when they appear in its title. |
| `title_filter` | text | `"strict"` | Choices: `strict` (keep a posting only if its title matches a search term; accents, word forms and the built-in aliases are applied) or `off` (keep every title). |
| `languages` | list of text | `["en"]` | Languages of the postings, for word-form matching. Choices: `ar ast bg ca cs cy da de el en enm eo es et fa fi fr ga gd gl grc gv hbs he hi hu hy id is it ka la lb lt lv mk ml ms nb nl nn pl pt ro ru se sk sl sq sv sw tl tr uk`. |
| `uk_sponsor_only` | `true` or `false` | `false` | `freehire` only: keep only jobs from UK sponsor-licensed employers. Other boards ignore it. |

Not settings any more: the title aliases (the software preset `templates/presets/software.json` plus common abbreviations such as "ops", "mgr", "sr") are always applied; the git snapshot is the flag `ohmyjob scrape --git-snapshot` (the find-jobs skill passes it only when you ask; it respects the workspace `.gitignore`, which lists the tracker and `Applications/`); parallelism is Claude Code's subagent cap (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`, default 20) minus one for the master; the paper is `a4` unless a resume yaml sets `paper: letter`; and the page count of a tailored resume is always the page count of its source resume.

## Job status life cycle

The `Status` column of `Job_Tracker.xlsx`:

| Status | Set by | What comes next |
|---|---|---|
| `Found` | `/ohmyjob:find-jobs` (also `prep-jobs` for a folder without a row) | `/ohmyjob:prep-jobs` screens it. |
| `Matched` | prep-jobs, when a screening worker scores the job 6 or more of 10 and picks a tailorable resume | A resume-worker tailors a copy and builds the PDF: `Resume Built` or `Resume Failed`. |
| `Skipped-LowFit` | prep-jobs, when the screening scores the job low (or an agency, or a dealbreaker) | Final; the job folder is deleted. Never written again by reconcile. |
| `Resume Built` | prep-jobs: the resume-worker's build passed (exactly the source's page count), or a ready-made PDF matched and was copied | `/ohmyjob:apply-jobs` queues it. |
| `Resume Failed` | prep-jobs, when the resume-worker could not finish (no OK build in 4, a blocker, a timeout) | The reason is in the tracker Notes. prep-jobs does not retry it: fix the resume by hand. |
| `Pending Review` | apply-jobs, when the worker parked the form before the final button and the checks passed | You review the form and submit it yourself; then `/ohmyjob:reconcile`. |
| `Needs Attention` | apply-jobs or continue-applying, when a worker was blocked (sign-in, CAPTCHA, a question, a pop-up) or a check failed | Fix the tab by hand, run `/ohmyjob:continue-applying`: it becomes `Pending Review` or `Needs Attention` again. |
| `Submitted` | `/ohmyjob:reconcile`, for a job whose folder is in `Pending-Review/` or `Needs-Attention/` (you moved it back to `Applications/` if you did not send it) | Final. |
| `Deleted` | prep-jobs (duplicate posting, a screening ground to delete, a row without a folder), apply-jobs (the job is closed), or reconcile (you deleted the folder) | Final. |

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
- Text copied from job pages and forms is treated as data, never as instructions. Without an `ohmyjob.json` (or `ohmyjob/ohmyjob.json`) in or above the folder, the hooks do nothing.

Read [DISCLAIMER.md](DISCLAIMER.md) before you use this on a real account.

## Token cost

Each run ends with a usage table (agents, API calls, input, cache and output tokens per agent type and model). The default parallelism is low (Claude Code's subagent cap is 20; set `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` lower to run fewer workers at once), screening uses a small model, and `results_per_term` is 30, so that a first run stays within a Pro plan. Raise them once you know the cost.

## Troubleshooting

- `ohmyjob: environment missing or stale, run /ohmyjob:setup`: the Python environment is missing or does not match this plugin copy. Run `/ohmyjob:setup`.
- **Switching between `--plugin-dir` and an installed copy** changes the plugin's data folder. Run `/ohmyjob:setup` again after each switch.
- **Tracker open in Excel or LibreOffice**: saving is refused with a clear message and nothing is lost. Close the file and run the command again. If the tracker changed on disk during a long run, the save is refused too; run the command again.
- A line starting `ohmyjob: ` or `FATAL` is an error. Read it, fix what it names, run the command again.
- `FATAL` naming `ohmyjob.json`: a setting is invalid; the message names the key.
- A line saying a file "still has the template marker": replace the mock data in that file and delete its marker line (see the mock guard).
- Hooks seem inactive: Claude was probably started outside the project folder, or the plugin is installed only at user scope. Check with `claude plugin list`.

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
