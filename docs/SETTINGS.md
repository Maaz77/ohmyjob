# Settings and status

The `ohmyjob.json` settings, the job status life cycle, and what the workers learn. [Back to the README](../README.md)

## Settings: `ohmyjob.json`

Setup creates this file with mock values; you edit it. It is plain JSON with no comments, so this table is the only guide. Everything lives in the `search` section. Unknown keys and bad values are refused with a message naming the key. `/ohmyjob:help` prints this table.

| Field | Type | Default | Choices / meaning |
|---|---|---|---|
| `sources` | list of text | `["linkedin"]` | **Required in practice.** The boards, searched in this order — when two boards list the same job, the first one keeps it. Choices: `linkedin indeed zip_recruiter glassdoor google bayt naukri bdjobs hellowork freehire`. |
| `search_query` | list of text | `[]` | **Required.** Job titles; one search per term per board. Nothing is searched while this is empty. |
| `location` | text | `""` | **Required.** City or region, for example `"Berlin, Germany"`. Google needs a city. |
| `country` | text | `""` | **Required while `indeed` or `glassdoor` is in `sources`** — without it they search the USA. Choices: `argentina australia austria bahrain belgium brazil canada chile china colombia "costa rica" "czech republic" denmark ecuador egypt finland france germany greece "hong kong" hungary india indonesia ireland israel italy japan kuwait luxembourg malaysia mexico morocco netherlands "new zealand" nigeria norway oman pakistan panama peru philippines poland portugal qatar romania "saudi arabia" singapore "south africa" "south korea" spain sweden switzerland taiwan thailand türkiye ukraine "united arab emirates" uk usa uruguay venezuela vietnam`. |
| `recency_days` | whole number | `1` | Only postings from the last N days. `0` means no age limit. |
| `results_per_term` | whole number, 1 or more | `30` | How many results to request per term per board. |
| `employment_types` | list of text | `["fulltime", "parttime", "contract"]` | Checked against the posting's own type field. Choices: `fulltime parttime contract temporary internship perdiem other volunteer`. `[]` means every type. |
| `exclude_companies` | list of text | `[]` | Companies to skip. |
| `exclude_seniority_level` | list of text | `[]` | Seniority words to skip, for example `"director"`, `"intern"`. Matched in the title, and against the board's own seniority label as a whole value. |
| `filter_title_terms` | list of text | `[]` | Words that drop a posting when they appear in its title. |
| `title_filter` | text | `"strict"` | `strict` keeps a posting only if its title matches a search term (accents, word forms and built-in aliases applied); `off` keeps every title. |
| `languages` | list of text | `["en"]` | The languages of the postings, used for word-form matching. Choices: `ar ast bg ca cs cy da de el en enm eo es et fa fi fr ga gd gl grc gv hbs he hi hu hy id is it ka la lb lt lv mk ml ms nb nl nn pl pt ro ru se sk sl sq sv sw tl tr uk`. |
| `uk_sponsor_only` | `true` or `false` | `false` | `freehire` only: keep only jobs from UK sponsor-licensed employers. Other boards ignore it. |

**Not settings.** The title aliases (the software preset in `templates/presets/software.json`, plus common abbreviations such as "ops", "mgr", "sr") are always applied. The git snapshot is a flag you ask for when you run stage 1. How many workers run at once is Claude Code's own subagent cap, `CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS` (default 20), minus one for the master. The paper size is `a4` unless a resume yaml sets `paper: letter`. And the page count of a tailored resume is always the page count of the resume it came from.

**Per-board notes.**

- **Indeed and Glassdoor** search one country at a time: set `country`.
- **ZipRecruiter** covers the US and Canada only.
- **Google** needs a city in `location`; the search text is built as "`<term>` jobs near `<location>`" plus the recency.
- **freehire** is opt-in and mostly tech jobs: add `"freehire"` to `sources`. It carries its own seniority labels (intern, junior, middle, senior, lead, staff, principal, c_level), which `exclude_seniority_level` can use, and the tracker's `Notes` says which site listed the job. With `uk_sponsor_only: true` and a UK country, only employers on the Home Office list of licensed visa sponsors are searched.
- The same posting found twice on one board gets the same job id and is added once. Copies on different boards get different ids, and the twin check — company, title and city — drops the later copy.

---

## Job status life cycle

The `Status` column of `Job_Tracker.xlsx`. The `Notes` column next to it holds the reason whenever a job is dropped or fails — read it first when something surprises you.

| Status | Set by | What comes next |
|---|---|---|
| `Found` | `/ohmyjob:find-jobs` (also `prep-jobs`, for a folder without a row) | `/ohmyjob:prep-jobs` screens it. |
| `Matched` | `prep-jobs`, when the screener scores the job 6 or more and picks a tailorable resume | A worker tailors a copy and builds the PDF: `Resume Built` or `Resume Failed`. |
| `Skipped-LowFit` | `prep-jobs`, when the score is low, or the poster is an agency, or a dealbreaker applies, or the posting states a work authorization requirement your `# Work Authorization` lines say you cannot meet (its `Notes` start "Work authorization block:") | Final. The folder is deleted; the reason is in `Notes`. Reconcile never touches it. |
| `Resume Built` | `prep-jobs`, when the build passed with exactly the source's page count — or a ready-made PDF matched and was copied | `/ohmyjob:apply-jobs` queues it. Applied entirely by hand instead? Move its folder to `Submitted/`, then `/ohmyjob:reconcile`. |
| `Resume Failed` | `prep-jobs`, when the worker could not finish: no good build in four tries, a blocker, a timeout | The reason is in `Notes`. It is not retried: fix the yaml, then `/ohmyjob:build-job-resumes`. |
| `Pending Review` | `apply-jobs`, when the worker parked the form before the final button and the checks passed | You review the form and submit it yourself, then run `/ohmyjob:reconcile`. |
| `Needs Attention` | `apply-jobs` or `continue-applying`, when a worker was blocked or a check failed | Fix the tab by hand, then `/ohmyjob:continue-applying`. |
| `Submitted` | `/ohmyjob:reconcile`, for a job whose folder was in `Pending-Review/`, `Needs-Attention/` or `Submitted/` (where you moved it after applying by hand) | Final. |
| `Deleted` | `prep-jobs` (a duplicate posting, a resume worker's refusal, a row without a folder), `apply-jobs` (the job is closed), or `reconcile` (you deleted the folder) | Final. |

---

## What the workers learn

Three files in your workspace shape how the application workers behave.

- **`ohmyjob/ats/`** holds the notes about application systems that **your own** runs produced. A note here replaces the plugin's note of the same name, and a worker reads it when it reaches that site. The plugin ships four generic notes (LinkedIn Easy Apply, Workday, Greenhouse, Phenom); your folder starts empty and fills up as you run `/ohmyjob:dream`.
- **`Dream_Report.md`** is what the last `/ohmyjob:dream` pass learned and wrote. Asking Claude to run `ohmyjob dream reject` puts the notes, the report and the watermark back as they were.
- **`ohmyjob/Worker_Limits.json`** holds the limits of the watchdog that stops a worker going nowhere. A missing file, key or unusable value falls back to the default, and the run prints the values it used.

| Key | Default | Meaning |
|---|---|---|
| `stall_nudge_calls` | 15 | Calls without progress before the worker is warned. |
| `stall_stop_calls` | 25 | Calls without progress before the worker is stopped. |
| `repeat_deny_stop` | 5 | This many identical refusals in a row stops the worker (waiting for a sign-in does not count). |
| `call_budget` | 150 | Browser and file-reading calls allowed in one worker. |
| `idle_minutes` | 6 | No tool call for this long frees the worker's slot. |

Progress means a new page, the first fill of a field, the first click on a choice control, an upload, or a step of a Google sign-in. There is no wall-clock limit.

[Back to the README](../README.md)
