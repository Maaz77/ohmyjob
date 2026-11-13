<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/ohmyjob-wordmark-dark.svg">
    <img alt="ohmyjob" src="assets/ohmyjob-wordmark-light.svg" width="250">
  </picture>
</p>

<h3 align="center">Your job search, done up to the last click. You press Submit.</h3>

<p align="center">
  <a href="LICENSE"><img alt="MIT license" src="https://img.shields.io/badge/license-MIT-blue"></a>
  <img alt="Claude Code plugin" src="https://img.shields.io/badge/Claude%20Code-plugin-D97757">
  <img alt="macOS and Linux" src="https://img.shields.io/badge/macOS%20%7C%20Linux-lightgrey">
  <img alt="Python" src="https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white">
</p>

<p align="center">
  ohmyjob finds jobs, screens them, tailors your resume and fills in the application form.<br>
  It stops before the last button. <b>You press every Submit button yourself.</b>
</p>

> [!NOTE]
> Read [DISCLAIMER.md](DISCLAIMER.md) before you use this on a real account.

<!-- Demo: place docs/demo.gif here (centered, width 720) once it is recorded. -->

## How it works

Every round has four stages. You start each one with a command and check the result before the next.

<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/pipeline-dark.svg">
    <img alt="The ohmyjob pipeline. ohmyjob does the work in three stages. 1, Find (/ohmyjob:find-jobs): searches up to 10 job boards, filters by title and level, drops duplicates; tracker status Found. 2, Screen and Tailor (/ohmyjob:prep-jobs): scores each job out of 10, drops the weak matches, tailors your resume and builds the PDF; status Resume Built. 3, Fill (/ohmyjob:apply-jobs): one Chrome tab per job, fills the form, uploads the resume, stops before the final button; status Pending Review. Then you, by hand: read each form and press Submit. 4, Reconcile (/ohmyjob:reconcile): shows a dry run first, syncs the tracker with your folders, archives the sent jobs; status Submitted. Below: it never submits, because hooks block every final button; resumes come only from your Profile.md; /ohmyjob:dream turns runs into notes about each site." src="docs/pipeline-light.svg" width="100%">
  </picture>
</p>

## Quick start

Make a folder for your job search. Everything the plugin writes about you stays inside it.

```bash
mkdir -p /path/to/job-search && cd /path/to/job-search
```

Add the plugin to that folder.

```bash
claude plugin marketplace add Maaz77/ohmyjob --scope project
```

Install it there.

```bash
claude plugin install ohmyjob@ohmyjob --scope project
```

Start Claude in that same folder. Use this line every time.

```bash
claude --setting-sources project,local --strict-mcp-config --chrome
```

> [!IMPORTANT]
> Start Claude in the job-search folder itself. From any other folder the never-submit hooks do not load.

Then run the one-time setup inside Claude. It is safe to run again.

```
/ohmyjob:setup
```

The rest (what you need, your resumes, your profile, Chrome) is in [docs/SETUP.md](docs/SETUP.md).

## What ohmyjob does

- **Finds postings.** It searches the job boards you choose and drops what does not fit your filters.
- **Screens them for you.** It reads each posting against your own profile and gives it a score. Weak matches are dropped, and the reason is written in your tracker.
- **Drops what you cannot take.** A posting that says outright "no visa sponsorship", "citizens only" or "security clearance required" is dropped before any resume is written, but only when the `# Work Authorization` lines of your `Profile.md` say you do not meet it for that country. A posting that says nothing about it, or a country you did not list, is never dropped (the job stays and its Gaps say it was not checked, with or without a requirement in the posting); a drop is a `Skipped-LowFit` row whose `Notes` start with "Work authorization block:" and quote the posting and your own line. English and German postings are read; a posting in another language is never dropped on this rule.
- **Tailors your resume.** Each good match gets a resume rewritten for that posting, built as a PDF.
- **Fills the forms.** It opens each application in your own Chrome and fills it from your profile.
- **Asks you once.** A question it cannot answer is asked at the end of the run, and your answer is saved for next time.
- **Keeps the books.** A spreadsheet in your folder shows every job, from the day it was found to the day you sent it.

## What it will never do

- Press Submit. Hooks refuse the final button of every form.
- Type a password or create an account on a job site. It stops and tells you instead.
- Invent a fact for your resume. It uses only what is in your own profile and resumes.
- Send your data anywhere except your own folder, your job boards and your own Chrome.

## Why it is different

| Idea | In plain words |
|---|---|
| **Hooks, not a promise** | Never-submit is enforced by hooks on every Chrome call, in the main session and in every agent. An agent cannot skip them. |
| **Your facts only** | A tailored resume is built from your own profile and the resume you started from. Numbers, dates, employers, degrees and past job titles stay exactly as you wrote them. |
| **Same page count** | The tailored PDF has exactly the page count of the resume it started from. It never shrinks the font or the margins. |
| **One worker per job** | Each application runs in its own Chrome tab, and a watchdog stops a worker that is going nowhere. |
| **Asked once, remembered** | Questions from the forms are collected, asked once, and saved in your profile. |
| **It learns each site** | `/ohmyjob:dream` turns the traces of your runs into notes about each application system. Traces hold no filled values, drafted answers or email addresses. |
| **No runs on sample data** | The commands refuse to run while the made-up "Alex Example" data is still in your files. |
| **A usage table** | Every run ends with a table of agents, API calls and tokens. |

## The four stages

| | Stage | Command | What it gives you |
|---|---|---|---|
| 1 | **Job Posting Finder** | `/ohmyjob:find-jobs` | New postings from the boards, filtered, in the tracker and in one folder per job. |
| 2 | **Job Posting Screener and Resume Tailoring** | `/ohmyjob:prep-jobs` | The weak matches dropped, and a tailored resume PDF for each of the rest. |
| 3 | **Job Application Assistant Browser Agent** | `/ohmyjob:apply-jobs`, then `/ohmyjob:continue-applying` | Each application filled in its own Chrome tab, parked before Submit, waiting for you. |
| 4 | **Reconcile the round** | `/ohmyjob:reconcile` | The tracker brought in line with what you actually sent, and the round closed. |

What you do in each stage, what you can steer and what can go wrong: [docs/STAGES.md](docs/STAGES.md).

## Commands

| Command | What it is for |
|---|---|
| `/ohmyjob:setup` | Prepare the environment and the workspace. Run once. |
| `/ohmyjob:find-jobs` | Stage 1. Search the boards and add new jobs to the tracker. |
| `/ohmyjob:prep-jobs` | Stage 2. Screen every new job, then tailor a resume for each match. |
| `/ohmyjob:apply-jobs` | Stage 3. Fill each application and park it before Submit. |
| `/ohmyjob:continue-applying` | Stage 3. Continue the tabs you unblocked by hand. |
| `/ohmyjob:reconcile` | Stage 4. Bring the tracker in line with your folders. |

<details>
<summary>All the commands</summary>

| Command | Arguments | What it is for |
|---|---|---|
| `/ohmyjob:setup` | none | Prepare the environment and the workspace. Run once; safe to run again. |
| `/ohmyjob:build-resumes` | `[file.yaml]` | Build a PDF next to every resume yaml in `Default_Resume_Files/` (never `Template*`), or only the file you name. |
| `/ohmyjob:master-resume` | none | Draft the `# Resume` section of `Profile.md` from your resume files, then revise it by hand. |
| `/ohmyjob:find-jobs` | none (ask in words for a dry run or a git snapshot) | **Stage 1.** Search the boards, filter, add the new jobs to the tracker at `Found`. |
| `/ohmyjob:prep-jobs` | none | **Stage 2.** Screen every `Found` job, then tailor and build a resume for every match. |
| `/ohmyjob:apply-jobs` | `[--selected "<name, name>"]` or `[--top-k-match <N>]`, not both | **Stage 3.** Fill each application in its own Chrome tab and park it before Submit. |
| `/ohmyjob:continue-applying` | none | **Stage 3.** Continue the tabs in the "Needs Attention" group after you unblocked them. |
| `/ohmyjob:reconcile` | none (caveat check, dry run and confirmation are built in) | **Stage 4.** Bring the tracker in line with your folders and close the round. |
| `/ohmyjob:build-job-resumes` | `<name, name, ...>` (required) | Build the tailored resume of the named job folders again, after you fixed a yaml by hand, and set each row back to `Resume Built`. |
| `/ohmyjob:dream` | none | Turn the traces of your runs into ATS notes in `ohmyjob/ats/`, with a `Dream_Report.md`. |
| `/ohmyjob:help` | none | Print the commands, the settings table and the job status life cycle. |

Two commands are easy to mix up: **`/ohmyjob:build-resumes` works in `Default_Resume_Files/`** and builds your own role resumes there; **`/ohmyjob:build-job-resumes` works in the job folders** and rebuilds one tailored resume for a job that already went through stage 2. Neither of them ever edits a yaml — only you change your resumes.

You can drop a job at any time, at any stage, by deleting its folder.

</details>

## Architecture

- **Skills** are the slash commands you type.
- **Agents** judge and act: a master runs a stage and starts one worker per job.
- **Scripts**, behind one command called `ohmyjob`, do all the bookkeeping: queues, tracker rows, folder moves.
- **Hooks** run on every tool call. They allow, refuse or rewrite it, and an agent cannot skip them.
- A small **Chrome helper extension** opens sign-in pop-ups as tabs and groups the finished ones.
- Everything about you lives in the **`ohmyjob/` workspace folder**: settings, profile, resumes, the tracker and one folder per job.

The full design, with diagrams: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

<details>
<summary>Requirements</summary>

- macOS or Linux. Windows is not supported.
- Claude Code 2.1.306 or newer.
- git, to fetch and update the plugin.
- A direct Anthropic plan (Pro, Max, Team or Enterprise), signed in with `/login`.
- Chrome, Edge or Brave with the Claude in Chrome extension. Only stage 3 needs a browser.
- Network and disk space for the one-time setup. Details in [docs/SETUP.md](docs/SETUP.md).

</details>

## Documentation

- [Setup](docs/SETUP.md): what you need, install, and fill in your details
- [Stages](docs/STAGES.md): the four stages in full
- [Settings](docs/SETTINGS.md): `ohmyjob.json`, the job status life cycle, worker limits
- [Troubleshooting](docs/TROUBLESHOOTING.md): errors and what a run costs
- [Architecture](docs/ARCHITECTURE.md): how it is put together
- [Disclaimer](DISCLAIMER.md): read before using it on a real account

## Safety

Hooks refuse the final control of every form (Submit, Send, Confirm, Finish and their equivalents in many languages). Workers park each form and you press Submit. Text copied from job pages is treated as data, never as instructions. Full text: [docs/SAFETY.md](docs/SAFETY.md).

## Develop

```bash
uv run --frozen pytest -q tests
claude plugin validate --strict .
bash scripts/pii_gate.sh <terms-file>
```

CI (`.github/workflows/ci.yml`) runs these, a real TinyTeX resume build and the extension tests.

## Credits and license

- Resume class: [Awesome-CV](https://github.com/posquit0/Awesome-CV) by posquit0, pinned at commit `dfe86354`, LPPL 1.3c, vendored unmodified in `resume/` with its license text.
- Job search: [JobSpy](https://github.com/speedyapply/JobSpy) (`python-jobspy`).
- Plugin code: MIT, see [LICENSE](LICENSE).
