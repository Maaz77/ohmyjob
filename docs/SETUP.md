# Setup

Everything you need before the first round: what to check, how to install, how to set up, and how to fill in your details. [Back to the README](../README.md)

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

Three sections matter, and a fourth is optional (below). Revise the first one, write the other two, and then delete the marker line at the top of the file:

- **`# Fit Preferences`** — three fields: `**Seniority / experience level:**`, `**What I'm looking for:**` and `**Dealbreakers:**`. The screener in stage 2 judges every posting against these before it looks at anything else. "What I'm looking for" is the main test: a job that is not what you asked for scores low however well your skills match. A dealbreaker you listed, or a role far above or below your stated level, drags the score down, and the rejection note quotes your own words back to you. Write it as if you were briefing a recruiter in three lines.
- **`# Resume`** — your master resume, and **the only source of facts for every tailored resume**. If a fact is not here, it will never appear on a resume. Step 2 drafted it from your resume files; go through it now and make every line true, with numbers where you have them. It starts with header lines (`- Name: First Last`, email, phone, address, links — your first name becomes the prefix of your resume file names) and then uses the same sections as the two templates. Each section carries one guidance line telling you what belongs in it.
- **`# Follow Up Questions`** — the answers that stage 3 reuses on application forms, including your work authorization and visa status per country. A numbered list: `1. Question? Answer.` Replace the mock answers. When stage 3 meets a question it cannot answer from this file, it asks you once and saves your answer here with the next number, so the same question is never asked twice.
- **`# Work Authorization`** — optional, but without it stage 2 can never drop a posting for a visa, citizenship or clearance requirement. One line per country, `- <Country>: <status>. your own words`, where the status starts the line and is `citizen`, `permanent resident`, `authorized` (you may work there without sponsorship) or `needs sponsorship`; `- EU: ...` covers every EU country without a line of its own; `- Security clearance: none` says you hold none. A country you leave out is never a reason to drop a job. Existing workspaces need no change: add the section when you want the rule.

### 4. `ohmyjob.json`

Set your boards, your search terms, your location and country, and whichever filters you want. Every field is in the [settings table](SETTINGS.md#settings-ohmyjobjson) below — the file is plain JSON with no room for comments, so that table is the only guide. Delete the `"_template"` line when the values are yours.

### 5. Set up Chrome

Only stage 3 needs this, so you can leave it until you get there — but do all five steps before your first application run, because each one of them blocks the run on its own.

1. **Install the Claude in Chrome extension** in Chrome, Edge or Brave, from the Chrome Web Store, and sign in to it with the same Anthropic account you use in Claude Code. This is the extension that lets Claude see and click pages at all.
2. **Load the ohmyjob helper extension.** It opens page pop-ups — a sign-in window, for instance — as ordinary tabs, so the application workers can reach them, and it groups finished tabs for you. Setup prints its folder path. In Chrome open `chrome://extensions`, switch on **Developer mode**, click **Load unpacked** and choose that folder. Keep the folder where it is: it survives plugin updates.
3. **Allow the Claude extension on the sites you will use** — the job boards and the company career sites. Open the Claude extension's settings in Chrome and grant per-site access there. These permissions live in the extension and nothing in this plugin can set them for you.
4. **Sign in to the job boards in that Chrome profile** — LinkedIn above all, and any other board you search. The workers use the session that is already in the browser; they never type a password, and a site that asks for one stops the job with a note instead.
5. **Sign in to Google in that Chrome profile**, with the account whose email is in your `Profile.md`. Many career sites offer "Continue with Google", and that is the one sign-in route the workers can take on their own. Without it, every site that puts a wall in front of its form ends the round at "Needs Attention" for you to handle by hand.

Next: [the four stages](STAGES.md).

[Back to the README](../README.md)
