# The four stages

What you do in each stage of a round, what you can steer, and what can go wrong. [Back to the README](../README.md)

## What it is

**What it is.** ohmyjob is a plugin for [Claude Code](https://code.claude.com) that runs your job search for you, one round at a time. It searches the job boards you choose, drops the postings that do not fit, reads each surviving posting against your own profile and throws out the weak matches, writes a tailored resume for every real match and builds it as a PDF, then opens each application in your own Chrome, fills the form, and stops right before the final button. You read the finished form and press Submit yourself. A spreadsheet in your folder keeps the state of every job, from the day it was found to the day you sent it.

**You press every Submit button yourself.** The plugin cannot send an application: hooks refuse the final controls of every form, in every Chrome call, in the main session and in every ohmyjob agent. That is a hard rule, not a setting.

## Every day: the four stages

**Before anything else:** open a terminal, go to your job-search folder — the one you created before you installed the plugin, the parent of `ohmyjob/` — and start Claude there:

```bash
cd /path/to/job-search && claude --setting-sources project,local --strict-mcp-config --chrome
```

Every round runs from that folder. If you start Claude somewhere else, the hooks and settings of the project do not load and the commands will not work properly.

### Stage 1 — Job Posting Finder

```
/ohmyjob:find-jobs
```

**What it does.** Postings come from two places. Most boards — LinkedIn, Indeed, ZipRecruiter, Glassdoor, Google, Bayt, Naukri, BDJobs, HelloWork — are searched through [JobSpy](https://github.com/speedyapply/JobSpy), which queries each board's own job search. `freehire` is separate: it is freehire.me's own keyless API, searched by country, and it mostly carries tech jobs. The boards are searched in the order you list them in `sources`, one search per search term per board.

**What actually reaches the board.** Only five things: your search term, your location, your country (for Indeed and Glassdoor, which search one country at a time — the USA if you leave it out), how many results you want per term, and how recent the postings must be. For Google the search text is built for you as "`<term>` jobs near `<location>`".

**Everything else is filtered here, on your computer**, and this is worth understanding because it is where the postings disappear. The filters work on two different kinds of thing:

- **On the text of the job title.** `title_filter: strict` keeps a posting only if its title matches one of your search terms — accents and word forms are ignored, and built-in aliases are always applied, so "Sr." meets "senior" and "ops" meets "operations". `filter_title_terms` drops a posting whose title contains a word you listed. `exclude_seniority_level` looks for your words in the title too.
- **On the attributes the board sent back with the posting.** `employment_types` is checked against the posting's own type field, not its title. `exclude_seniority_level` also matches the board's own seniority label when the board sends one, as a whole value — so `"senior"` does not drop LinkedIn's "Mid-Senior level". `exclude_companies` is matched against the company name.

On top of that, several checks remove jobs you already have: a posting already in your tracker or already holding a folder, the same posting met twice under two search terms, and the same job listed by two boards (compared by company, title and city — the board earlier in your `sources` list keeps it).

**What you get.** One line per board — how many were scraped, how many are new, and a breakdown of why the rest were dropped — then a total. Every new job gets a tracker row at `Found` and a folder, `Applications/<id>_<Company>_<Title>/`, with a `job.md` holding the posting. A job whose company is in `exclude_companies` is recorded as `Deleted` with a note and gets no folder at all.

**What you can steer.** All of it through `ohmyjob.json`. The command itself takes no arguments, but you can ask for two things in plain words when you run it:

- *"do a dry run"* — it counts what would be new and writes nothing.
- *"take a git snapshot first"* — it commits the tracker and `Applications/` as they are before the run, so you can go back. It is skipped outside a git repository, and it only commits what `.gitignore` does not ignore — the workspace `.gitignore` ignores both the tracker and `Applications/` by default, so remove those two lines first if you want this.

**Caveats.** A board can fail for one search term and still deliver jobs from the others; a failed board is reported as `ERROR` and the other boards carry on. LinkedIn in particular rate-limits: jobs reported as `failed` are ones whose detail page did not load, and they are left for the next run rather than lost. If almost everything is dropped on `title`, your search terms and the boards' titles do not meet — loosen the terms, or set `title_filter: off` for one run and see what comes back.

### Stage 2 — Job Posting Screener and Resume Tailoring

```
/ohmyjob:prep-jobs
```

This stage has two halves, and both run as a queue of small workers — a new one starts as soon as one finishes.

**First half: screening.** One worker reads one job. It is given your Fit Preferences first, then your whole `Profile.md`, then the list of your resumes with the text of each, then the posting. It plays a senior recruiter in your field who judges the substance of the work rather than keywords, and it is deliberately unkind: it is told not to inflate a score to be nice. It scores the job out of 10.

- **6 or more** is a match. The job goes to `Matched`, the score goes in the tracker, and the worker names the one resume of yours closest to this job.
- **5 or less** is a drop. The job becomes `Skipped-LowFit` and its folder is deleted. **The reason is written in the tracker's `Notes` column** — read it whenever you are surprised that a job vanished; a few rounds of those notes tell you what your Fit Preferences are really doing. `/ohmyjob:retro` (below) walks you through them and turns your verdict into edits of the Fit Preferences. The end of a prep run says when ten or more skips wait for a review.
- **A dealbreaker you listed, or a clear seniority mismatch**, caps the score at 5 whatever your skills look like. The note quotes the preference it used.
- **A work authorization requirement you cannot meet** is dropped before a worker is spent on the job. A script (`work_auth.py`) reads the posting for an outright "no visa sponsorship", "must have the right to work", "citizens only", "permanent residency required" or a named security clearance, takes the one country of the job's `Location`, and compares it with your `# Work Authorization` lines in `Profile.md` (`- <Country>: <status>. your own words`, status `citizen`, `permanent resident`, `authorized` or `needs sponsorship`; `- EU: ...` covers the EU; `- Security clearance: none`). Only when both sides are explicit is the job dropped: `Skipped-LowFit`, with a `Notes` entry that starts "Work authorization block:" and quotes the posting's sentence and your own line, so these drops can be searched and counted apart from low scores. Silence, wording like "visa sponsorship available", a hedged sentence ("unless", "preferred"), a remote or multi-country job, a country with no line of yours and a profile with no such section never drop a job; the job goes on, and its `Gaps` say "Work authorization not checked" whenever the job's country is known and your lines do not cover it, or the posting states a requirement that your lines (or an unclear location) cannot decide. English and German postings are read; a posting in any other language is never dropped on this rule.
- **A posting from a recruiter or staffing agency** has to clear a higher bar: 8, not 6. Below that it is dropped as an agency posting, with the signal it saw. The judgement comes from the company name and the description together, never the name alone.

The screen is strict by design, and your Fit Preferences, not your resume, are the main lever: three vague lines there drop good jobs, three sharp ones drop the right ones.

**Second half: resume tailoring.** How this goes depends on which of your resumes was matched.

- **A ready-made PDF** (one you dropped in yourself, with no yaml) is copied into the job's folder as it is and the job goes straight to `Resume Built`. Nothing is tailored or edited — that is the trade you make by using your own PDFs. The screener weighs your ready-made PDFs and your role yamls against each other in one pass, and when two are about as close it takes the yaml, because that one gets rewritten for the posting and your PDF does not.
- **A role yaml** gets a worker of its own. It starts from an exact copy of your yaml and rewrites it for this posting.

What that worker may and may not do is worth knowing, because it decides how much your tailored resumes can differ from what you wrote:

- **Its source of facts is your `Profile.md` plus the matched yaml, and nothing else.** Anything in `Profile.md` may go onto the resume even if the yaml never mentioned it — but nothing from outside that pool may be invented, inferred, upgraded or borrowed from the job description.
- **It may rewrite freely inside that pool**: reorder and reword bullets, merge or split them, pull in a project or a skill from `Profile.md`, regroup and rename the skills rows, rewrite the summary so the specialty this employer wants comes first, and change the resume's headline title to the title in the posting.
- **It may not drop your content to make room.** Tailoring here means putting the relevant material first, not deleting the rest; a low-relevance bullet gets reworded and moved down, not cut. The only removals allowed are to hold the length.
- **It may not touch facts or structure.** Numbers, dates, employers, degrees, credentials and the titles of your past roles carry over exactly — only the headline at the top of the resume is retitled. Your contact details, education and the section order are frozen. It may not swap wording in a way that promotes a claim ("helped run a budget" never becomes "owned the budget").
- **The PDF comes out with exactly the page count of the resume it started from** — one page in, one page out. If it runs over, it tightens wording first, then drops the least relevant bullets, and only as a last resort a whole project. It never shrinks the font or the margins. It gets four build attempts.
- **It may refuse the job before it edits anything**, on three grounds only: the deciding requirements have no support at all in your material, the posting is a different profession, or a dealbreaker applies. A missing tool, a missing industry, or a seniority title above your level is never a refusal — in a close call it builds the resume. A refused job becomes `Deleted`, with the reason in `Notes`.

**What you get.** A tracker row at `Resume Built` for each job that made it, with `<First>_<Company>_<Title>.yaml` and `.pdf` in the job folder, and a `job.md` that records what was changed and why, which gaps your material could not cover, and what was dropped to fit the page. A job whose resume could not be finished sits at `Resume Failed` with the reason in `Notes`; it is not retried automatically. Fix its yaml by hand and run `/ohmyjob:build-job-resumes <name>` to build it again.

**What you can steer.** Nothing at run time — this command takes no arguments. You steer it entirely through `Profile.md` and the set of role resumes you keep in `Default_Resume_Files/`. More role files means a closer starting point for each job.

**Caveats.** Screening uses a small, fast model: it is a first read, not a careful one, and the resume worker does its own reading of the posting afterwards. Each worker has 17 minutes. One that runs out of time reports a failure, so its job lands at `Resume Failed` with the reason; if nothing comes back from it at all, the job stays at `Matched` for the next run.

### Stage 3 — Job Application Assistant Browser Agent

```
/ohmyjob:apply-jobs
```

One worker per job at `Resume Built`, each in its own Chrome tab. It opens the posting, finds the application, fills the form from your `Profile.md`, uploads that job's resume, and **stops before the final button**. Then:

- **`Pending Review`** — the form is filled and parked, waiting for you.
- **`Needs Attention`** — something blocked it: a sign-in wall, a CAPTCHA, an account it is not allowed to create, a pop-up, a question it could not answer. The tab stays open, in the Chrome tab group "Needs Attention".

Any question the workers could not answer from `Profile.md` is collected and **asked once, at the end of the run**, with the jobs that asked it. You answer in your own words — or skip any of them — and your answers are saved into `# Follow Up Questions` so the next round answers them itself.

**Arguments.** This is the one command where limiting the run is useful, since it is the slow and expensive stage:

- `/ohmyjob:apply-jobs --selected "Acme, Lumenfield"` — run only those job folders, named by a comma-separated list. Only letters, digits, spaces and `, . _ & + ( ) -` are allowed in the names.
- `/ohmyjob:apply-jobs --top-k-match 5` — run only the five jobs with the best Match Score.
- You cannot give both at once. With neither, the whole queue runs.

**Then, by hand:** open the Chrome tab group "Needs Attention" and fix what blocked each job — sign in, create the account yourself, dismiss the pop-up. Then:

```
/ohmyjob:continue-applying
```

The workers pick those tabs up where they stopped, and each job ends at `Pending Review` or back at `Needs Attention`. This command brings back every tab in that group, so you can also put a tab there yourself — a job you opened by hand, or one whose tab you moved out — and it will be matched to its job by the page it shows. A tab that matches no job is left open and stays in that group. You can repeat this stage as often as you like.

**Then, by hand again:** go through each parked form, check what was filled, and **press Submit yourself**.

**What you can steer.** `Profile.md` and `# Follow Up Questions` decide what the workers can answer without you. `ohmyjob/Worker_Limits.json` decides when a stuck worker is stopped — see ["What the workers learn"](SETTINGS.md#what-the-workers-learn).

**Caveats.** This stage needs all five Chrome steps in [Setup](SETUP.md#5-set-up-chrome) done. A site that requires an account with a password always stops at "Needs Attention": a worker never creates an account and never types a password. A worker never touches another worker's tab and never closes one. And a parked form is not a sent application — nothing goes out until you press the button.

### Stage 4 — Reconcile the round

```
/ohmyjob:reconcile
```

This closes the round: it brings the tracker in line with your folders and moves the folders of the jobs you sent into `Submitted/`.

**How it decides**, for every row that is still open:

- Its folder is in `Pending-Review/` or `Needs-Attention/` → the row becomes `Submitted` and the folder moves to `Submitted/`.
- Its folder is gone → the row becomes `Deleted`.
- Its folder is already in `Submitted/` → the row becomes `Submitted`; nothing moves.
- Its folder is still in `Applications/` → left alone. `Applications/` is never touched. If you applied to such a job entirely by hand, move its folder to `Submitted/` yourself before you reconcile.

Rows already at `Deleted` or `Skipped-LowFit` are never changed.

> [!WARNING]
> **Read this before you run it.** Reconciliation treats a folder in `Pending-Review/` or `Needs-Attention/` as **sent**. It does not and cannot check whether you actually submitted the form. So, before you reconcile:
>
> - **Any job you did not send, move its folder back to `Applications/` or delete it.** Otherwise it is marked `Submitted` and you will never see it again.
> - **Delete the folder of every job you dropped**, at whatever stage you dropped it. Deleting a folder is how you drop a job, and reconcile records it as `Deleted`.
> - **Any job you applied to entirely by hand, move its folder from `Applications/` to `Submitted/`.** Reconcile never touches `Applications/`, so otherwise the job stays open.

**What protects you.** Before it runs anything, it shows you the caveat above and asks whether you have tidied your folders; it starts only after you say yes. Then it shows a dry run — every row that would change, with its old and new status and where its folder would go, plus any folder it could not match — and then asks you to confirm. Nothing is written without a clear yes. If the list holds a row you did not expect, say no, move that folder back to `Applications/`, and run it again. The command takes no arguments; the caveat check, the dry run and the confirmation are built in.

### Optional: let the plugin learn the sites

```
/ohmyjob:dream
```

Over a few application rounds the plugin collects traces of what it met on each application system — no filled values, no drafted answers, no email addresses. When enough new traces have piled up, the end of a run says so. `/ohmyjob:dream` turns them into notes in `ohmyjob/ats/`, which the workers read the next time they reach that site, plus a `Dream_Report.md` explaining what it learned. To undo a whole pass, ask Claude to run `ohmyjob dream reject`.

### Optional: tune your Fit Preferences after a few rounds

```
/ohmyjob:retro
```

Your Fit Preferences in `Profile.md` (`Seniority / experience level`, `What I'm looking for`, `Dealbreakers`) decide what stage 2 drops. Retro closes that loop with you, in the conversation:

1. **You judge.** It lists the jobs that were skipped and that you have not reviewed yet (company, title, link, score and the screener's reason), and you say which were wrong. You can also type standing preferences in your own words, for example "stop matching consulting roles". Either alone is enough. A long list can be reviewed in parts; the skips you answered are not shown again.
2. **It proposes.** From your marks and your words it proposes a small number of edits, one per field, each showing the current wording, the new wording and the evidence. It changes as little as it can and keeps your voice. A fact about you (a skill, a role) is not a preference, so it points you to the `# Resume` section instead. If your evidence conflicts, it shows you the conflict and does not pick a side.
3. **You decide, one edit at a time.** `Profile.md` is saved to `.ohmyjob/backup/Profile-<timestamp>.md` before the first accepted edit, then a script changes that one field and nothing else. A refused proposal is not offered again for the same evidence.

The reason of a skip was written by a model from the posting, so it is shown as evidence but never followed and never copied into your wording. Retro never touches `# Resume`, `# Follow Up Questions`, `ohmyjob.json` or the score thresholds, and it does not bring a skipped job back. To undo the last pass, ask Claude to run `ohmyjob retro reject`: it puts `Profile.md` and the review marks back.

[Back to the README](../README.md)
