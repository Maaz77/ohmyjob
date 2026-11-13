# ohmyjob

**What it is.** ohmyjob is a plugin for [Claude Code](https://code.claude.com) that runs your job search for you, one round at a time. It searches the job boards you choose, drops the postings that do not fit, reads each surviving posting against your own profile and throws out the weak matches, writes a tailored resume for every real match and builds it as a PDF, then opens each application in your own Chrome, fills the form, and stops right before the final button. You read the finished form and press Submit yourself. A spreadsheet in your folder keeps the state of every job, from the day it was found to the day you sent it.

**You press every Submit button yourself.** The plugin cannot send an application: hooks refuse the final controls of every form, in every Chrome call, in the main session and in every ohmyjob agent. That is a hard rule, not a setting.

Read [DISCLAIMER.md](DISCLAIMER.md) before you use this on a real account.

---

## The four stages

Every round of the search has four stages, and each one is a command you run:

| | Stage | Command | What it gives you |
|---|---|---|---|
| 1 | **Job Posting Finder** | `/ohmyjob:find-jobs` | New postings from the boards, filtered, in the tracker and in one folder per job. |
| 2 | **Job Posting Screener and Resume Tailoring** | `/ohmyjob:prep-jobs` | The weak matches dropped, and a tailored resume PDF for each of the rest. |
| 3 | **Job Application Assistant Browser Agent** | `/ohmyjob:apply-jobs`, then `/ohmyjob:continue-applying` | Each application filled in its own Chrome tab, parked before Submit, waiting for you. |
| 4 | **Reconcile the round** | `/ohmyjob:reconcile` | The tracker brought in line with what you actually sent, and the round closed. |

["Every day: the four stages"](#every-day-the-four-stages) explains what you do in each one, what you can steer, and what can go wrong. The rest of this page is what you need before the first round.

---

## What you need

Check all of this **before** you install anything:

- **macOS or Linux.** Windows is not supported.
- **Claude Code 2.1.306 or newer.** Check with `claude --version`, update with `claude update`.
- **git.** The install below fetches this plugin from GitHub with git, and `claude plugin update` uses it too. Check with `git --version`; on macOS that prompts to install the Xcode command line tools, on Debian or Ubuntu use `sudo apt install git`. Nothing in the daily workflow needs git.
- **A direct Anthropic plan** — Pro, Max, Team or Enterprise — signed in with `/login`. An API key turns Claude in Chrome off, and stage 3 then cannot run.
- **Chrome, Edge or Brave**, with the Claude in Chrome extension installed and signed in. WSL does not work for the browser. Only stage 3 needs a browser; stages 1, 2 and 4 run without one.
- **Network and disk space** for the one-time setup. It downloads a Python environment and, if your computer has no working LaTeX, about 250 MB of TinyTeX.

Everything else — the Python packages, LaTeX, the helper extension — setup installs for you.

## Install

Make a folder for your job search. It can be anywhere; everything the plugin writes about you stays inside it.

```bash
mkdir -p /path/to/job-search && cd /path/to/job-search
```

Install the plugin **in that folder**, at project scope. This keeps the plugin, its hooks and its settings inside the folder and leaves your `~/.claude/settings.json` alone:

```bash
claude plugin marketplace add Maaz77/ohmyjob --scope project
```

```bash
claude plugin install ohmyjob@ohmyjob --scope project
```

Now start Claude **in that same folder**:

```bash
claude --setting-sources project,local --strict-mcp-config --chrome
```

This is the line you use every time, not only the first time. Two things about it matter:

- **Start in the job-search folder itself**, the one you just created — not in `ohmyjob/` below it, and not in your home folder. Project settings and hooks load only from the folder Claude starts in. From a subfolder they do not apply, which means the hooks that keep Claude from pressing Submit are not there.
- **`--chrome` is what connects Claude to your browser.** Without it, stage 3 has nothing to drive.

## Set up

Run this once:

```
/ohmyjob:setup
```

Setup never asks you anything about yourself and never writes your real data. It is safe to run again at any time: it never overwrites a file that already exists.

**What it checks first.** Your Claude Code version (it stops if it is older than 2.1.306); that `ohmyjob@ohmyjob` is installed and enabled at project or local scope (it stops if not, because the never-submit hooks only protect a project where the plugin is enabled); and whether Claude in Chrome answers. A browser that does not answer is only a warning — setup tells you how to turn it on and carries on, because you do not need it until stage 3.

**What it installs.** It asks before each install, and everything goes into the plugin's own data folder (`~/.claude/plugins/data/<id>/`), which is deleted with the plugin: a Python environment, uv's own Python and cache, `uv` itself if you do not have it, TinyTeX only if no LaTeX already on your computer can build the resume template, the LaTeX font caches, and a stable copy of the Chrome helper extension. Nothing is added to your `PATH`, your shell profile, `~/.local`, `~/.cache` or any system folder, and no system LaTeX installation is touched.

**What it creates in your folder:**

```
/path/to/job-search/
  .claude/settings.json          plugin, environment and permissions (merged; your own keys stay)
  ohmyjob/                       your workspace — everything about you lives here
    ohmyjob.json                 the search settings
    Profile.md                   who you are, what you want, your master resume, your stock answers
    Job_Tracker.xlsx             one row per job
    CLAUDE.md                    a short guide, and the standing never-submit rule
    .gitignore                   keeps your personal files out of git
    Default_Resume_Files/        your resumes live here (see below)
    Applications/  Pending-Review/  Needs-Attention/  Submitted/
    .ohmyjob/                    working files: environment, run state, builds — do not edit
```

**The mock persona.** `ohmyjob.json`, `Profile.md`, `Template.yaml` and `Template-CV.yaml` arrive filled in with a made-up person, "Alex Example", so you can see the shape of every file before you write your own. Each of those files carries a marker line (the first line of `Profile.md` and of both templates, and the `"_template": ...` line in `ohmyjob.json`). **You delete that line once the data in the file is yours.** Until you do, the commands that would act on made-up data refuse to run and say which file is still unedited:

| Command | Refuses while |
|---|---|
| `/ohmyjob:find-jobs` | `ohmyjob.json` or `Profile.md` still has its marker — otherwise it would search for Alex Example's jobs. |
| `/ohmyjob:prep-jobs` | `Profile.md` still has its marker, or no usable resume is left. |
| `/ohmyjob:apply-jobs`, `/ohmyjob:continue-applying` | `Profile.md` still has its marker — otherwise made-up answers would be typed into real forms. |

## Fill in your details

Do these in order: the resumes come first, because the master resume is drafted from them. Stages 1 and 2 will not run until the first four are done.

### 1. Your resumes, in `Default_Resume_Files/`

This folder is where your own resumes live, and it decides what stage 2 can do. There are two ways to fill it, and you can mix them.

**Way one: fill in a template, and let the plugin build the PDF.** Setup puts two templates in the folder, so you can choose the document you want:

- `Template.yaml` — a **resume**: Summary, Work Experience, Honors & Awards, Certificates, Education.
- `Template-CV.yaml` — a **CV**: Education, Skills, Experience, Extracurricular Activity, Honors & Awards, Certificates, Presentation, Writing, Program Committees.

Both follow [Awesome-CV](https://github.com/posquit0/Awesome-CV) exactly — the red accent, its page geometry, header and footer. `Template.pdf` and `Template-CV.pdf` next to them are sample builds, so you can see the result before you write a word. A section with no entries is simply left out, and a key that the chosen document does not print gives a `WARNING` line rather than an error.

Copy a template once **per kind of role you apply for**, and name each copy after that role, for example `Data-Analyst.yaml`, `ML-Engineer.yaml`, `Nurse-ICU.yaml`. Fill in your own data, delete the marker line, and build the PDFs:

```
/ohmyjob:build-resumes
```

That builds a PDF next to every yaml in the folder (never the `Template*` files), one line of output per file. `/ohmyjob:build-resumes Data-Analyst.yaml` builds just that one. Each role file is a complete resume on its own; there is no shared base file. Copy the template as many times as you like — one per role is the useful unit, because stage 2 picks the closest one for each job and tailors a copy of it.

**Way two: drop in resumes you already have.** Copy your own finished PDFs into the folder, named after the role (`Nurse-ICU.pdf`), with no yaml beside them. Their layout and format do not matter — they do not have to look like the template at all. The catch: **a ready-made PDF is never tailored.** It is sent exactly as it is. The plugin reads its text to decide whether it fits a job, and if it does, it copies the file into the job's folder and moves straight on to stage 3.

Two rules for this folder:

- **A ready-made `X.pdf` must never sit next to an `X.yaml`.** A build writes the PDF next to its yaml and with the same name, so your file would be overwritten.
- **A yaml without a built PDF is not usable.** Run `/ohmyjob:build-resumes` before stage 2. Files that still carry the marker line, and the `Template.*` files, are never used for a real job.

### 2. Draft your master resume

```
/ohmyjob:master-resume
```

This fills in the `# Resume` section of `Profile.md` for you. A script — no model — merges every resume yaml in `Default_Resume_Files/` into one draft: the same job, degree or award appearing in several files becomes one entry, and bullets and skills are pooled. Every PDF that has no yaml is turned into plain text, and Claude adds the facts from that text to the draft. Your old `Profile.md` is backed up to `.ohmyjob/backup/Profile-<timestamp>.md` before anything is written, and if the section already holds your own text you are asked before it is replaced.

It is a merge, not an edit, so **read the draft and revise it by hand** in the next step: remove what is not true, fix what reads badly, add what none of your resumes mentioned, put in the numbers. Everything stage 2 writes on your behalf comes out of this section.

### 3. `Profile.md`

Three sections matter. Revise the first one, write the other two, and then delete the marker line at the top of the file:

- **`# Fit Preferences`** — three fields: `**Seniority / experience level:**`, `**What I'm looking for:**` and `**Dealbreakers:**`. The screener in stage 2 judges every posting against these before it looks at anything else. "What I'm looking for" is the main test: a job that is not what you asked for scores low however well your skills match. A dealbreaker you listed, or a role far above or below your stated level, drags the score down, and the rejection note quotes your own words back to you. Write it as if you were briefing a recruiter in three lines.
- **`# Resume`** — your master resume, and **the only source of facts for every tailored resume**. If a fact is not here, it will never appear on a resume. Step 2 drafted it from your resume files; go through it now and make every line true, with numbers where you have them. It starts with header lines (`- Name: First Last`, email, phone, address, links — your first name becomes the prefix of your resume file names) and then uses the same sections as the two templates. Each section carries one guidance line telling you what belongs in it.
- **`# Follow Up Questions`** — the answers that stage 3 reuses on application forms, including your work authorization and visa status per country. A numbered list: `1. Question? Answer.` Replace the mock answers. When stage 3 meets a question it cannot answer from this file, it asks you once and saves your answer here with the next number, so the same question is never asked twice.

### 4. `ohmyjob.json`

Set your boards, your search terms, your location and country, and whichever filters you want. Every field is in the [settings table](#settings-ohmyjobjson) below — the file is plain JSON with no room for comments, so that table is the only guide. Delete the `"_template"` line when the values are yours.

### 5. Set up Chrome

Only stage 3 needs this, so you can leave it until you get there — but do all five steps before your first application run, because each one of them blocks the run on its own.

1. **Install the Claude in Chrome extension** in Chrome, Edge or Brave, from the Chrome Web Store, and sign in to it with the same Anthropic account you use in Claude Code. This is the extension that lets Claude see and click pages at all.
2. **Load the ohmyjob helper extension.** It opens page pop-ups — a sign-in window, for instance — as ordinary tabs, so the application workers can reach them, and it groups finished tabs for you. Setup prints its folder path. In Chrome open `chrome://extensions`, switch on **Developer mode**, click **Load unpacked** and choose that folder. Keep the folder where it is: it survives plugin updates.
3. **Allow the Claude extension on the sites you will use** — the job boards and the company career sites. Open the Claude extension's settings in Chrome and grant per-site access there. These permissions live in the extension and nothing in this plugin can set them for you.
4. **Sign in to the job boards in that Chrome profile** — LinkedIn above all, and any other board you search. The workers use the session that is already in the browser; they never type a password, and a site that asks for one stops the job with a note instead.
5. **Sign in to Google in that Chrome profile**, with the account whose email is in your `Profile.md`. Many career sites offer "Continue with Google", and that is the one sign-in route the workers can take on their own. Without it, every site that puts a wall in front of its form ends the round at "Needs Attention" for you to handle by hand.

---

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
- **5 or less** is a drop. The job becomes `Skipped-LowFit` and its folder is deleted. **The reason is written in the tracker's `Notes` column** — read it whenever you are surprised that a job vanished; a few rounds of those notes tell you what your Fit Preferences are really doing.
- **A dealbreaker you listed, or a clear seniority mismatch**, caps the score at 5 whatever your skills look like. The note quotes the preference it used.
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

**What you can steer.** `Profile.md` and `# Follow Up Questions` decide what the workers can answer without you. `ohmyjob/Worker_Limits.json` decides when a stuck worker is stopped — see ["What the workers learn"](#what-the-workers-learn).

**Caveats.** This stage needs all five Chrome steps above done. A site that requires an account with a password always stops at "Needs Attention": a worker never creates an account and never types a password. A worker never touches another worker's tab and never closes one. And a parked form is not a sent application — nothing goes out until you press the button.

### Stage 4 — Reconcile the round

```
/ohmyjob:reconcile
```

This closes the round: it brings the tracker in line with your folders and moves the folders of the jobs you sent into `Submitted/`.

**How it decides**, for every row that is still open:

- Its folder is in `Pending-Review/` or `Needs-Attention/` → the row becomes `Submitted` and the folder moves to `Submitted/`.
- Its folder is gone → the row becomes `Deleted`.
- Its folder is already in `Submitted/` → the row becomes `Submitted`; nothing moves.
- Its folder is still in `Applications/` → left alone. `Applications/` is never touched.

Rows already at `Deleted` or `Skipped-LowFit` are never changed.

> **⚠️ Read this before you run it.** Reconciliation treats a folder in `Pending-Review/` or `Needs-Attention/` as **sent**. It does not and cannot check whether you actually submitted the form. So, before you reconcile:
>
> - **Any job you did not send, move its folder back to `Applications/` or delete it.** Otherwise it is marked `Submitted` and you will never see it again.
> - **Delete the folder of every job you dropped**, at whatever stage you dropped it. Deleting a folder is how you drop a job, and reconcile records it as `Deleted`.

**What protects you.** It always shows a dry run first — every row that would change, with its old and new status and where its folder would go, plus any folder it could not match — and then asks you to confirm. Nothing is written without a clear yes. If the list holds a row you did not expect, say no, move that folder back to `Applications/`, and run it again. The command takes no arguments; the dry run and the confirmation are built in.

### Optional: let the plugin learn the sites

```
/ohmyjob:dream
```

Over a few application rounds the plugin collects traces of what it met on each application system — no filled values, no drafted answers, no email addresses. When enough new traces have piled up, the end of a run says so. `/ohmyjob:dream` turns them into notes in `ohmyjob/ats/`, which the workers read the next time they reach that site, plus a `Dream_Report.md` explaining what it learned. To undo a whole pass, ask Claude to run `ohmyjob dream reject`.

---

## All the commands

| Command | Arguments | What it is for |
|---|---|---|
| `/ohmyjob:setup` | none | Prepare the environment and the workspace. Run once; safe to run again. |
| `/ohmyjob:build-resumes` | `[file.yaml]` | Build a PDF next to every resume yaml in `Default_Resume_Files/` (never `Template*`), or only the file you name. |
| `/ohmyjob:master-resume` | none | Draft the `# Resume` section of `Profile.md` from your resume files, then revise it by hand. |
| `/ohmyjob:find-jobs` | none (ask in words for a dry run or a git snapshot) | **Stage 1.** Search the boards, filter, add the new jobs to the tracker at `Found`. |
| `/ohmyjob:prep-jobs` | none | **Stage 2.** Screen every `Found` job, then tailor and build a resume for every match. |
| `/ohmyjob:apply-jobs` | `[--selected "<name, name>"]` or `[--top-k-match <N>]`, not both | **Stage 3.** Fill each application in its own Chrome tab and park it before Submit. |
| `/ohmyjob:continue-applying` | none | **Stage 3.** Continue the tabs in the "Needs Attention" group after you unblocked them. |
| `/ohmyjob:reconcile` | none (dry run and confirmation are built in) | **Stage 4.** Bring the tracker in line with your folders and close the round. |
| `/ohmyjob:build-job-resumes` | `<name, name, ...>` (required) | Build the tailored resume of the named job folders again, after you fixed a yaml by hand, and set each row back to `Resume Built`. |
| `/ohmyjob:dream` | none | Turn the traces of your runs into ATS notes in `ohmyjob/ats/`, with a `Dream_Report.md`. |
| `/ohmyjob:help` | none | Print the commands, the settings table and the job status life cycle. |

Two commands are easy to mix up: **`/ohmyjob:build-resumes` works in `Default_Resume_Files/`** and builds your own role resumes there; **`/ohmyjob:build-job-resumes` works in the job folders** and rebuilds one tailored resume for a job that already went through stage 2. Neither of them ever edits a yaml — only you change your resumes.

You can drop a job at any time, at any stage, by deleting its folder.

---

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
| `Skipped-LowFit` | `prep-jobs`, when the score is low, or the poster is an agency, or a dealbreaker applies | Final. The folder is deleted; the reason is in `Notes`. Reconcile never touches it. |
| `Resume Built` | `prep-jobs`, when the build passed with exactly the source's page count — or a ready-made PDF matched and was copied | `/ohmyjob:apply-jobs` queues it. |
| `Resume Failed` | `prep-jobs`, when the worker could not finish: no good build in four tries, a blocker, a timeout | The reason is in `Notes`. It is not retried: fix the yaml, then `/ohmyjob:build-job-resumes`. |
| `Pending Review` | `apply-jobs`, when the worker parked the form before the final button and the checks passed | You review the form and submit it yourself, then run `/ohmyjob:reconcile`. |
| `Needs Attention` | `apply-jobs` or `continue-applying`, when a worker was blocked or a check failed | Fix the tab by hand, then `/ohmyjob:continue-applying`. |
| `Submitted` | `/ohmyjob:reconcile`, for a job whose folder was in `Pending-Review/` or `Needs-Attention/` | Final. |
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

---

## Safety

- **Never submit.** Hooks run on every Chrome call — in the main session and in every ohmyjob agent — and refuse final controls: Submit, Send, Confirm, Finish and their equivalents in many languages, plus unnamed submit buttons. `javascript_tool` is denied in the workspace settings. The rules differ by caller:
  - **Application workers** may act only in their own tab and its sign-in tab; every click carries an English note; uploads are limited to that job's own resume; and a button name that no word list can decide goes to a short extra check, which refuses the click if it fails.
  - **The main session and any other agent** may click only on references from a fresh read of that tab, one pressable click per read, with no coordinate clicks, no Enter, Return or Space keys, navigation to http(s) addresses only, no Apply click on a job board or under a filled form, and final button names checked against the word lists.
- **Before setup, the guard cannot run.** Until setup has created the Python environment, the main session's Chrome guard lets a call through with a notice — but no pipeline can run before setup, and every ohmyjob agent is denied outright. Run `/ohmyjob:setup` first.
- **You submit.** Workers park every form before the final button. You review it and press Submit.
- Text copied from job pages and forms is treated as data, never as instructions. Without an `ohmyjob.json` in or above the folder, the hooks do nothing.

Read [DISCLAIMER.md](DISCLAIMER.md) before you use this on a real account.

## What it costs

Each run ends with a usage table: agents, API calls, and input, cache and output tokens per agent type and model. The defaults are deliberately modest so that a first round fits inside a Pro plan — screening uses a small model, `results_per_term` is 30, and the number of workers running at once is Claude Code's subagent cap (20) minus one. Raise them once you know what a round costs you.

## When something goes wrong

A line of output starting with `ohmyjob: ` or `FATAL` is an error. Read it, fix what it names, run the command again.

- **`ohmyjob: environment missing or stale, run /ohmyjob:setup`** — the Python environment is missing or does not match this copy of the plugin. Run `/ohmyjob:setup`.
- **`FATAL` naming `ohmyjob.json`** — a setting is invalid; the message names the key.
- **A line saying a file "still has the template marker"** — replace the mock data in that file with your own and delete its marker line.
- **The tracker is open in Excel or LibreOffice** — saving is refused with a clear message and nothing is lost. Close the file and run the command again. The same happens if the tracker changed on disk during a long run.
- **The hooks seem inactive** — Claude was probably started outside the project folder, or the plugin is installed only at user scope. Check with `claude plugin list`.
- **You switched between `--plugin-dir` and an installed copy** — that changes the plugin's data folder. Run `/ohmyjob:setup` again after each switch.

The full design of every component is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Develop

```bash
uv run --frozen pytest -q tests
```

```bash
claude plugin validate --strict .
```

```bash
bash scripts/pii_gate.sh <terms-file>
```

CI (`.github/workflows/ci.yml`) runs these, a real TinyTeX resume build and the extension tests. The design of every component is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Credits and license

- Resume class: [Awesome-CV](https://github.com/posquit0/Awesome-CV) by posquit0, pinned at commit `dfe86354`, LPPL 1.3c, vendored unmodified in `resume/` with its license text.
- Job search: [JobSpy](https://github.com/speedyapply/JobSpy) (`python-jobspy`).
- Plugin code: MIT, see [LICENSE](LICENSE).
