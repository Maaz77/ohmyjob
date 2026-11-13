# Architecture

How ohmyjob is put together: the layers, the four subsystems, the hooks that bind them and the state they share.

## Principle: agents decide, scripts keep the books, hooks enforce

- **Agents (LLM)** judge and act: a master runs the steps and starts workers; each worker handles one job.
- **Scripts** do all bookkeeping: queues, briefs, result checks, job notes, folder moves, tracker rows, follow-up questions, cleanup. A master calls them through one command, `ohmyjob`.
- **Hooks** run on every tool call they match; an agent cannot skip them. They allow, refuse or rewrite a call and save what the scripts need.

```mermaid
flowchart TB
    subgraph Top["You and the session"]
        USER["You"]
        CMD["Slash commands in skills/"]
    end
    subgraph AG["Agents decide"]
        MASTER["Masters: prep, application, dream"]
        WORKER["Workers: one job or one ATS key each"]
    end
    subgraph SC["Scripts keep the books"]
        OHMYJOB["bin/ohmyjob: scrape, prep, apply, reconcile, resume, dream"]
    end
    subgraph HO["Hooks enforce"]
        HOOK["hook.sh: guard, handback, snapshot, capture, report"]
    end
    subgraph OUT["Outside the plugin"]
        BOARDS["Job boards, through JobSpy and freehire"]
        CHROME["Your Chrome: Claude in Chrome plus the helper extension"]
        TEX["LuaLaTeX with the vendored Awesome-CV class"]
    end
    subgraph WS["The ohmyjob/ workspace"]
        INPUT["ohmyjob.json, Profile.md, Default_Resume_Files/"]
        TRACKER["Job_Tracker.xlsx"]
        FOLDERS["Applications/, Pending-Review/, Needs-Attention/, Submitted/"]
    end
    USER --> CMD
    CMD -->|"find-jobs, reconcile, build-resumes: no agent"| OHMYJOB
    CMD -->|"prep-jobs, apply-jobs, dream: fork a master"| MASTER
    MASTER -->|"one command per step"| OHMYJOB
    MASTER -->|"one worker per job"| WORKER
    WORKER -->|"a brief in, one JSON report out"| OHMYJOB
    HOOK -.->|"allows, denies or rewrites every tool call"| CMD
    HOOK -.-> MASTER
    HOOK -.-> WORKER
    INPUT --> OHMYJOB
    OHMYJOB --> BOARDS
    OHMYJOB --> TEX
    WORKER --> CHROME
    OHMYJOB --> TRACKER
    OHMYJOB --> FOLDERS
```

`prep-jobs`, `apply-jobs`, `continue-applying` and `dream` fork a master, which starts the workers. `find-jobs`, `reconcile` and the two resume builds are scripts the main session runs directly. Every tool call of all of them passes the hooks.

## How you use it

```mermaid
flowchart TD
    subgraph Once["Once, before the first round"]
        I1["Make a folder, install the plugin there, start Claude in it with --chrome"]
        I2["/ohmyjob:setup"]
        I3["Fill Default_Resume_Files/: one yaml per role, or your own ready-made PDFs"]
        I4["/ohmyjob:build-resumes"]
        I5["/ohmyjob:master-resume"]
        I6["Revise Profile.md: the Resume draft, Fit Preferences, Follow Up Questions"]
        I7["Edit ohmyjob.json, then set up Chrome"]
    end
    subgraph Round["Every round, in this order"]
        S1["/ohmyjob:find-jobs"]
        S2["/ohmyjob:prep-jobs"]
        S3["/ohmyjob:apply-jobs"]
        H1["You: fix the tabs in the Needs Attention group"]
        S4["/ohmyjob:continue-applying"]
        H2["You: review each parked form and press Submit"]
        H3["You: move back or delete the folder of every job you did not send"]
        S5["/ohmyjob:reconcile"]
    end
    I1 --> I2 --> I3 --> I4 --> I5 --> I6 --> I7 --> S1
    S1 --> S2 --> S3 --> H1 --> S4 --> H2 --> H3 --> S5
    S4 -->|"still blocked"| H1
    S5 -->|"next round"| S1
```

The three by-hand steps of a round are the design, not a gap: the plugin cannot submit a form, and `reconcile` cannot tell a form you sent from one you only looked at, so the folder clean-up before it is what keeps the tracker true.

## Components

### Skills (`skills/`, commands are `/ohmyjob:<name>`)

| Skill | Role |
|---|---|
| `setup` | Idempotent preparation and health check: version gate, plugin-scope and Chrome checks (Chrome is a warning only), bootstrap (scoped installs, the `ohmyjob/` skeleton, mock templates, `Template.pdf`), extension steps, then it prints what the user fills in. It never interviews and never writes the user's data. |
| `master-resume` | Runs `ohmyjob resume master`: a script merges the resume yamls and extracts PDF text into a draft of `# Resume`; Claude adds the PDF facts; `--apply` saves it into Profile.md after a backup. |
| `build-resumes` | Runs `ohmyjob resume build-all`: a PDF next to every yaml in `Default_Resume_Files/`; reports `OK` or `FAIL` per file (never `Template*.yaml`; an optional file name builds only that file). Never edits a yaml. |
| `help` | Reads the plugin README and `docs/SETTINGS.md` and prints the commands with their use-cases, the `ohmyjob.json` field table and the job status life cycle. The model may invoke it. |
| `find-jobs` | Runs `ohmyjob scrape` in the background and summarizes (also usable from a scheduled `claude -p`). |
| `add-job` | Puts postings the candidate found by hand into the tracker at `Found`: web addresses go straight to `ohmyjob add-job`; pasted text is read by the main session for company and title (it asks when unsure), written to an inbox file and handed over with `--paste`. Never writes the tracker or a folder itself; never starts stage 2. |
| `prep-jobs` | Forks the `prep-master` agent. |
| `apply-jobs`, `continue-applying` | Thin skills in the main session: call their `-run` skill, then ask the collected follow-up questions in one message and save the answers. |
| `apply-jobs-run`, `continue-applying-run` | Internal; fork the `application-master` agent (the second in continue mode). |
| `reconcile` | Caveat check, dry run, confirmation, then apply. |
| `build-job-resumes` | Runs `ohmyjob resume build-jobs "<names>"`: the tailored resume of each named job folder, built again and set back to `Resume Built`. |
| `dream` | Forks the `dream-master` agent: the traces since the last dream become the ATS notes of the workspace. |

### Agents (`agents/`, named `ohmyjob:<name>`)

| Agent | Model | Job |
|---|---|---|
| `prep-master` | Haiku | Loops on `ohmyjob prep next` and starts the workers it prints. Cannot read files or pages. |
| `screen-worker` | Haiku | Scores one job against the text catalog of the usable resumes; reads only its brief; reports one JSON object. |
| `resume-worker` | Sonnet | Tailors one resume yaml (or rejects a mis-matched posting before any edit); the only command it may run is the build of its own job. |
| `application-master` | Sonnet | Runs the apply steps through `ohmyjob apply` and starts workers; its browser tools are limited to tab handling and the sign-in check. |
| `application-worker` | Sonnet | Fills one application in one assigned tab, from its brief; stops before the final button; reports one JSON object. |
| `dream-master` | Opus | Runs `ohmyjob dream start/next/finish/end` and starts one host worker per ATS key. Reads no file. |
| `dream-host-worker` | Sonnet | Writes ONE ATS note of the workspace from the evidence bundle of its key. Reads its bundle, the notes and the application worker's rules; writes only its own note. |

Plugin agents ignore `permissionMode`, so a hook returns an explicit allow for every call it has validated. The `tools: Agent(...)` entry in an agent's frontmatter does not restrict what it can start, so the guards check the requested agent type themselves.

### Scripts (`scripts/`, run through `bin/ohmyjob`)

| Command | Script | Job |
|---|---|---|
| `ohmyjob scrape` | `scrape.py`, `scrape_core.py` | Search, filter, dedupe, write job folders and tracker rows. |
| `ohmyjob add-job` | `add_job.py` | Add postings found by hand: fetch the page, read its schema.org JobPosting, check what is known (id, twin), write the folder, `job.md` and the `Found` row. No search filter applies. |
| `ohmyjob prep <cmd>` | `pj.py` (hooks: `pj_hooks.py`) | Prep queue, briefs, yaml checks, build, records. |
| `ohmyjob apply <cmd>` | `aa.py`, `aa_lib.py` (hooks: `hooks.py`; `judge.py`, `lexicon.py`) | Apply queue, briefs, result checks, tab groups, questions. |
| `ohmyjob reconcile` | `reconcile.py` | Tracker versus folders. |
| `ohmyjob resume build`, `build-all`, `build-jobs` | `resume_build.py` | Render one yaml (or every yaml of a folder except `Template*.yaml`, or the one named) to LaTeX, run `lualatex`, count pages. `build-jobs "<names>"` builds the single yaml of each named job folder and sets its row to `Resume Built`. |
| `ohmyjob dream <cmd>` | `dream.py` (with `ledger.py`) | Group the new traces by ATS key, hand out one worker per key, check the notes, write `Dream_Report.md`, move the watermark; `reject` restores the last backup. |
| (internal) | `ledger.py` | Blocker codes, outcomes, the ledger lines and the redacted traces that `ohmyjob apply end` writes. |
| (internal) | `workspace.py`, `tracker.py`, `job_key.py` | Config and paths, the xlsx tracker, the one job-id rule. |
| (internal) | `hook.sh`, `bootstrap.sh`, `pii_gate.sh` | Hook router, environment setup, release check. |

`bin/ohmyjob` finds the workspace, reads `.ohmyjob/env.sh` (parsed, never sourced) and runs the script with the plugin's own virtualenv. Errors are one `ohmyjob: ...` line (exit 3); the scripts print `FATAL ...` lines. Agent prompts stop on either.

### Stage 1 — Job Posting Finder (`ohmyjob scrape`)

For each board in `search.sources`, in order, JobSpy is asked for every search term. A posting is kept when its title matches a term (word forms and aliases applied, or any title with `title_filter: off`), its employment type and company pass, it does not read as spam (`junk_reason`: scam, MLM or chat-app signals), it is not known yet (tracker row or a folder with the same job key) and it is not a twin of a posting from an earlier board or of a recent tracker row. Each new job gets `Applications/<key>_<Company>_<Title>/job.md` and a tracker row at `Found`. A board that fails is reported and skipped; the others run.

A posting the candidate found by hand enters the same stage through `ohmyjob add-job` (`/ohmyjob:add-job`): the same `job.md` and `Found` row, `Source` `Added by hand (<site>)`, and the checks against what is already there (`known`, `twin`; every row counts, whatever its age or status, and the existing row is named). None of the search filters applies, and a company in `exclude_companies` only gets a warning. The page is fetched by the script (standard library, no Chrome) and read from its JobPosting data; a page that cannot be read (error, sign-in wall, script-rendered, empty or boilerplate-only text) creates nothing and the candidate is asked to paste the text. A company or title that is not certain is asked for, never guessed. A posting pasted without any address gets the address `paste:<14 digits of a hash of its text>`, which `job_key.py` maps to an id of `8` plus those digits, so reconcile pairs its folder with its row like any other; `apply start` skips such a job unless it has an Apply URL, because there is no page to open.

```mermaid
flowchart TD
    A["ohmyjob scrape"] --> B{"Template marker left in ohmyjob.json or Profile.md?"}
    B -- yes --> BX["One FATAL line; nothing is searched"]
    B -- no --> C["Optional git snapshot of the tracker and Applications/"]
    C --> D["For every board in sources, in order, for every search term"]
    D --> E["The board itself is sent only: the term, the location, the country, results per term, recency"]
    E --> F["Postings come back, with their own attributes"]
    F --> T["Filters on the title text: title_filter strict, filter_title_terms, the seniority words"]
    F --> P["Filters on the posting's attributes: employment type, the board's own seniority label, the company name"]
    F --> Q["Checks against what you already have: unusable id, known row or folder, the same id twice, the same title plus company plus city, a twin from an earlier board or a recent row"]
    T --> R{"Dropped?"}
    P --> R
    Q --> R
    R -- yes --> S["Counted by reason in the per-board summary line"]
    R -- no --> K{"Company in exclude_companies?"}
    K -- yes --> M["Tracker row Deleted, with a note; no folder"]
    K -- no --> N["Tracker row Found, plus a job folder with job.md"]
```

The drop checks run in one fixed order, and the first one that hits wins: `bad`, `known`, `dup id`, `failed` (a LinkedIn detail page that did not load), `type`, `level`, `term`, `title`, `duplicate`, `twin`.

### Helper extension (`chrome-extension/`)

A small MV3 extension. In a run's tab group it opens page pop-ups (sign-in windows) as tabs, which Claude in Chrome can then control; it labels icon-only "Continue with Google" buttons (links and clickable icons too) and writes how many Google controls a job page has into its title (`[AA g=N named=M]`, which the ledger uses); and at the end of a run it moves finished job tabs into the tab groups "Pending Review" and "Needs Attention". For `continue-applying` it moves EVERY tab of the group "Needs Attention" back, also tabs the candidate put there by hand. It acts only for pages the master loads and only for tabs of the run group.

### Resume build (`resume/`, `resume_build.py`)

One complete yaml per resume (no shared base file; `Template.yaml` is the annotated starting point) is rendered through `resume.tex.j2` and compiled with LuaLaTeX using the vendored Awesome-CV class, in its own directory per build so parallel builds do not collide. The result is `OK`, `OVERFLOW` (a tailored resume with more pages than its source resume's PDF; a build with fewer pages is a check failure asking to keep the original length) or `FAIL`; character budgets for bullets and skill rows are checked before compiling. Every build uses `texmf-var/` in the plugin data folder for the LaTeX font caches.

## Hook routing and the guard model

Hooks are declared in `hooks/hooks.json` and all enter through `scripts/hook.sh <event>`:

| Event | Fires on | Does |
|---|---|---|
| `guard` | PreToolUse of Chrome tools, Read, Edit, Write, Bash, Agent | Allow, deny or rewrite. |
| `handback` | PreToolUse of `SubagentHandback` | Checks a worker's report; refuses a bad one (twice at most). |
| `snapshot` | PostToolUse of Chrome tools | Saves each page and its element refs; compacts long pages for the application worker. |
| `capture` | SubagentStop of an ohmyjob worker | The same report checks on the worker's final text. |
| `report` | Stop | Shows the usage table if the user has not seen it. Runs 2 to 3 times per run, so it is idempotent. |

A worker's report reaches the scripts by two equal paths, `handback` (auto mode only) and `capture` (the only path in Manual and `-p` mode). Both feed one processor.

Routing rules, in order:

1. **Workspace discovery** is one rule for `find_root`, `hook.sh`, `bin/ohmyjob` and the Python hooks: `$OHMYJOB_WORKSPACE` as given (tests), else walk up from the start folder (the project folder, the call's `cwd` or the shell's `cwd`); in each folder D, `D/ohmyjob.json` makes D the workspace, else `D/ohmyjob/ohmyjob.json` makes `D/ohmyjob` the workspace. So the project folder and a shell inside `ohmyjob/` both work.
2. `hook.sh` stays in bash and starts no Python unless it must. Outside a workspace it exits 0 with no output.
3. A `guard` of any Chrome tool goes to `hooks.py`, whoever calls. The main session and non-ohmyjob subagents get the rules for callers outside the pipeline: no `javascript_tool`; clicks only on refs from a fresh read of that tab, one pressable click per read; no coordinate clicks; no Enter, Return or Space keys; navigation to http(s) addresses only; no Apply click on a job board or under a filled form; final button names checked by the word lists.
4. Other calls with a prep agent type go to `pj_hooks.py`; with an application agent type go to `hooks.py`. The main session's non-Chrome calls and other agents' non-Chrome calls cost nothing.
5. The model controls `tool_input`, so the Python hooks decide only from the parsed top-level `agent_type`, `tool_name` and `cwd`, never from text inside `tool_input`. Each script re-checks the parsed agent type and hands over to the other when the prefilter picked wrongly.
6. Without the plugin's virtualenv, `guard` of any ohmyjob agent and of any Chrome call by an agent is denied with `ohmyjob: environment missing or stale, run /ohmyjob:setup`; the main session's Chrome call only gets a notice (fail-open, because no pipeline can run before setup). The guard is `onFailure: block`, so a crash blocks the call too.

What the guards enforce:

- **Prep agents** stay inside their own job: a screen worker reads only its brief; a resume worker reads its brief, yaml and PDF, edits only its yaml and runs only the build of its job. A master may start only its own worker types.
- **Application worker**: only its own tab (and its sign-in tab); no coordinate clicks, Enter or Space keys, or JavaScript; every click carries an English action note; refs of an old page are refused; a second click on the same element before a new page read is refused; typing only after a click on a text field; uploads only of the job's own resume; reads only its brief and the ATS notes in `ats/`.
- **Mock guard**: `scrape` refuses while `ohmyjob.json` or `Profile.md` holds the `OHMYJOB-TEMPLATE` marker; `prep start` while `Profile.md` holds it or no usable resume is left; `apply start` (also continue mode) while `Profile.md` holds it; `add-job` while either file holds it. The check is one helper, `has_template_marker` / `template_files` in `workspace.py`. Each refusal is one `FATAL:` line naming the file.
- **Final controls** are refused in about 50 languages (word lists in `lexicon.py`), as is a submit button without a name; once a tab has shown a form, every Apply button is refused. For application workers (and for Apply clicks on job boards), a name that no list can decide goes to a Haiku check (`judge.py`, one cached `claude -p` call with no tools); if it fails, the click is refused. Callers outside the pipeline use the word lists only.
- **Consent and sign-in**: cookie banners get the most private choice; OAuth permission grants are refused; on a Google account page only the account choice and the basic-profile consent are allowed; sign-up and delete buttons are refused.
- **Application master**: tab handling and the sign-in check only, and only the application worker agent type.

## Queues

Both masters are queue loops: a new worker starts as soon as one finishes, up to Claude Code's subagent cap minus one for the master (`W.slots()`; shared by all worker types: a resume-worker starts as soon as a screen-worker reports a match, while other screen-workers still run). The master must keep its turn alive with the `next` loop; a forked master that ends its turn after launching background workers stops at once.

### Stage 2 — Job Posting Screener and Resume Tailoring (`ohmyjob prep`)

1. `start` refuses on the mock guard (above), builds the screening catalog, queues `Found` rows for screening and `Matched` rows for building, and pairs rows with folders by the job id. A row without a folder becomes `Deleted`; a folder without a row gets a `Found` row; duplicate postings (same company and title) become `Deleted`, keeping a `Resume Built` row, then one with a stated location, then the lower id.
2. `next` is the loop: it records finished jobs and prints the workers to start. `assign-screen` writes one brief per job (full `Profile.md`, the path of the catalog text, the job). Screen workers report a match score, the matched resume, reasons and gaps. The catalog shows the title, introduction, roles and skills of a tailorable resume and the whole text (up to `CATALOG_FILE_CAP`) of a ready-made one; both pools are judged in that one pass, and a near tie goes to the tailorable resume.
3. `record` writes the result into `job.md`, sets the row, saves the tracker, and only then deletes skipped folders. A match on a yaml sets `Matched` and joins the build queue at once. A match on a ready-made PDF needs no worker: `record` copies the PDF into the job folder as `<First>_<Company>_<Title>.pdf` and sets `Resume Built` directly; such a folder holds a PDF and no yaml. A skip sets `Skipped-LowFit`.
4. `assign-build` copies the matched yaml into the job folder and writes the brief. The resume worker edits only that yaml and runs `ohmyjob prep build <job>`, which checks the mechanical rules (skeleton, bullet counts, title, line and skills budgets), compiles (at most 4 builds), and returns `OK`, `OVERFLOW` or `FAIL`. `record` then sets `Resume Built`, `Resume Failed`, or `Deleted` for a rejected posting.
5. `end` prints the summary (every deletion listed one by one) and the token table, and deletes the run folder.

```mermaid
flowchart TD
    A["/ohmyjob:prep-jobs forks prep-master"] --> B["prep start: mock guard, screening catalog, queue the Found and Matched rows"]
    B --> C["prep next: one screen-worker per Found job, in the shared slots"]
    C --> D["screen-worker, Haiku: reads only its brief, reports one JSON object"]
    D --> E{"Match score"}
    C -- "no report from the screen-worker" --> X["Left at Found for the next run"]
    E -- "junk, below 6, or below 8 for an agency posting" --> F["Skipped-LowFit; reason in Notes; folder deleted"]
    E -- "6 or more, closest resume is a ready-made PDF" --> G["The PDF is copied into the job folder: Resume Built, no worker"]
    E -- "6 or more, closest resume is a role yaml" --> H["Matched; joins the build queue at once"]
    H --> I["resume-worker, Sonnet: a copy of the yaml, at most 4 builds, exactly the source's page count"]
    I --> J{"Report"}
    J -- built --> K["Resume Built"]
    J -- failed --> L["Resume Failed; reason in Notes; not retried"]
    J -- rejected --> M["Deleted: core unsupported, wrong discipline or a dealbreaker"]
    J -- "no report at all" --> N["Left at Matched for the next run"]
    F --> W["Write order: job.md, then the tracker saved, then the folder deletes"]
    G --> W
    K --> W
    L --> W
    M --> W
```

Order of writes in every step: `job.md`, then the tracker (saved), then folder deletions. A folder is deleted only after its row is saved. A worker has a time limit; a job with no report after a further wait is recorded as missing. A crashed run is harmless: unrecorded jobs are still at `Found` or `Matched` and the next run takes them again.

### Stage 3 — Job Application Assistant Browser Agent (`ohmyjob apply`)

1. `start` clears old run state and queues rows at `Resume Built` (`--continue`: rows at `Needs Attention` whose tab can be found). `preflight` checks the LinkedIn sign-in, the helper extension and the Haiku check.
2. The master opens one tab per job; `assign` writes one brief per job: the full `Profile.md`, the tailored resume yaml and the job's overview and description.
3. `next` starts workers in free slots; the first page loads are staggered so that many workers do not hit one account at once. Each worker's report is checked by `handback` or `capture`.
4. A worker never guesses. On a question about the candidate that the brief does not answer, it stops with status `blocked` and the questions word for word; `finish` records them in `questions.json`, and the main session asks them and saves the answers in `Profile.md` (`ohmyjob apply questions` / `answer`).
5. `finish` checks every result, writes `job.md` notes, moves folders (`Pending-Review/` for a form parked at the final step, `Needs-Attention/` for the rest) and updates the tracker in one save. `group-plan` and `cleanup-plan` let the extension group the tabs and close stray ones (job tabs stay open). `end` prints the token table and removes the run folder.
6. `continue` mode (`adopt-plan`, `adopted`, `assign --continue`) takes every tab of the group "Needs Attention" back into the run group; each worker continues from the open page. `adopted` matches a tab to a job by the page it shows (the address of the last note, the Apply URL and the Job URL, then the shared path segments and the company in the tab title), not by Chrome's tab id, so a Chrome restart and a tab the candidate added are both handled. A job without a matching tab is left alone; a tab that fits no job stays open and goes back to "Needs Attention".
7. A worker has no wall-clock limit: a progress watchdog stops it when nothing moves (`Worker_Limits.json`, the counters live in the run state). `next` frees the slot of a worker that ended or made no call for `idle_minutes`; a late report of such a worker is still accepted.
8. `end` also writes the ledger and the traces (`ledger.py`) into `.ohmyjob/traces/`, before the run folder is deleted, and says when enough new traces are waiting for `/ohmyjob:dream`.

```mermaid
flowchart TD
    A["/ohmyjob:apply-jobs forks application-master"] --> B["apply start: queue the Resume Built rows; --selected or --top-k-match narrow the queue"]
    B --> C["preflight: the LinkedIn sign-in, the helper extension, the Haiku check, Worker_Limits.json"]
    C --> D["The master opens one tab per job and writes one brief per job"]
    D --> E["apply next: workers in the free slots, their first page loads staggered"]
    E --> F["application-worker: its own tab only; the guard decides every call; snapshot keeps each page"]
    F --> G{"Report, through handback or capture"}
    G -- "form parked before the final button" --> H["Pending Review; folder to Pending-Review/"]
    G -- "blocked, or a check failed" --> I["Needs Attention; folder to Needs-Attention/; tab kept in that tab group"]
    G -- "a question the brief could not answer" --> Q["Recorded in questions.json"]
    Q --> I
    Q --> K["The main session asks you each question once and saves your answer in Profile.md"]
    I --> J["You fix the tab by hand: sign in, create the account, close the pop-up"]
    K --> J
    J --> L["/ohmyjob:continue-applying adopts every tab of the Needs Attention group, matched by the page it shows"]
    L --> F
    H --> M["You review the form and press Submit yourself"]
    M --> N["/ohmyjob:reconcile"]
```

A worker that is stopped by an upload hang (a hidden browser alert) is retried once with a shortened file name (never over an existing file of that name); the new worker starts with clean counters.

### Optional: Dream (`ohmyjob dream`)

`start` groups the ledger lines since the watermark by ATS key, writes one evidence bundle per key into `.ohmyjob/run/dream/`, copies a plugin note into `ohmyjob/ats/` when only the plugin has it, and backs up the notes, the report and the watermark to `.ohmyjob/dream/backup/`. `next` hands out one worker per key (slots as above; a worker that sends no report within 20 minutes is given up on). A worker is bound to its key by the first Read of its bundle: the guard then lets it write that one note. `finish` checks every changed note (at most 40 lines, one H1, a `hosts:` line, no email address, no Profile.md header value), puts a note that breaks a rule back, and writes `Dream_Report.md`. `end` moves the watermark and deletes the run folder; `reject` restores the backup.

## State and files

The workspace is the `ohmyjob/` folder inside the project folder (the folder Claude starts in, which also holds `.claude/settings.json`). In it:

| Path | Content |
|---|---|
| `ohmyjob.json` | Config and the workspace marker; hooks do nothing without it. Its `_template` key (the only extra key accepted) means "not edited yet". |
| `Profile.md` | `# Fit Preferences` (three fields, screened against first), `# Resume` (the master resume; its `- Name:` line gives the file prefix), `# Follow Up Questions` (numbered). A first line with `OHMYJOB-TEMPLATE` means mock data. |
| `Job_Tracker.xlsx` | One row per job: Job URL, Status, Company, Job Title, Date Found, Location, Source, Match Score, Notes. Statuses: `Found`, `Matched`, `Skipped-LowFit`, `Resume Built`, `Resume Failed`, `Pending Review`, `Needs Attention`, `Submitted`, `Deleted`. |
| `Default_Resume_Files/` | `Template.yaml` and `Template.pdf` (never used for jobs); one complete `<Field>.yaml` per resume with its built `<Field>.pdf`; optional ready-made `<Field>.pdf` without a yaml. Named by field by the user; never edited by the pipeline. |
| `Applications/`, `Pending-Review/`, `Needs-Attention/`, `Submitted/` | One folder per job named `<job_key>_<Company>_<Title>`, holding `job.md`, the tailored yaml and PDF. |
| `ats/` | The ATS notes this workspace learned (Dream writes them). A note here replaces the plugin's note of the same name; the plugin ships only four generic notes. |
| `Worker_Limits.json` | The progress watchdog of the application workers: `stall_nudge_calls`, `stall_stop_calls`, `repeat_deny_stop`, `call_budget`, `idle_minutes`. A missing file, key or unusable value falls back to the default. |
| `Dream_Report.md` | What the last dream pass found: the metrics per ATS, the lessons, the proposals and the bugs. |
| `.ohmyjob/` | `env.sh`, run state (`run/prep`, `run/apply`, `run/dream`), `build/`, `catalog/` (the screening text catalog), `questions.json`, `last_report.txt`, `traces/` (`ledger.jsonl` and the redacted traces; never deleted with a run folder) and `dream/` (the watermark and the backup of the last dream). Do not edit. |
| `.claude/settings.json` | Plugin enablement, environment, the `Bash(ohmyjob *)` allowance and the `javascript_tool` deny; merged by setup. |

In the plugin data folder (`${CLAUDE_PLUGIN_DATA}`, survives updates): the virtualenv, uv's own Python and cache, `uv` if it was missing, TinyTeX if no system LaTeX built the template, the LaTeX font caches (`texmf-var/`) and a stable copy of the extension. Nothing is installed on the user's `PATH`, in `~/.local`, in `~/.cache` or in a system folder; `bin/ohmyjob` and the resume build pass the same scoped `UV_*` and `TEXMF*` settings to every later run. Its path depends on how the plugin is loaded, which is why setup writes it into `env.sh` on every run.

Job id: a LinkedIn `/jobs/view/<id>` keeps its id; any other address gives `9` plus 14 hash digits, with tracking parameters ignored; a pasted posting without an address (`paste:<14 digits>`) gives `8` plus those digits. One rule (`job_key.py`) serves the scraper, the folders and reconcile. The tracker is saved atomically; a save is refused while Excel or LibreOffice has the file open, or when the file changed on disk since the run loaded it.

### Job status life cycle

```mermaid
stateDiagram-v2
    state "Found" as Found
    state "Matched" as Matched
    state "Skipped-LowFit" as Skipped
    state "Resume Built" as Built
    state "Resume Failed" as Failed
    state "Pending Review" as Pending
    state "Needs Attention" as Needs
    state "Submitted" as Submitted
    state "Deleted" as Deleted

    [*] --> Found: find-jobs or add-job
    Found --> Matched: prep-jobs, 6 or more on a role yaml
    Found --> Built: prep-jobs, a ready-made PDF matched
    Found --> Skipped: prep-jobs, junk, low score, agency or dealbreaker
    Matched --> Built: the resume worker built it
    Matched --> Failed: the resume worker could not finish
    Matched --> Deleted: the resume worker rejected the posting
    Failed --> Built: build-job-resumes, after you fixed the yaml
    Built --> Pending: apply-jobs parked the form
    Built --> Needs: apply-jobs was blocked
    Built --> Deleted: apply-jobs found the job closed
    Needs --> Pending: continue-applying
    Needs --> Needs: continue-applying, still blocked
    Pending --> Submitted: reconcile, after you sent it
    Needs --> Submitted: reconcile
    Pending --> Deleted: reconcile, folder deleted
    Skipped --> [*]
    Submitted --> [*]
    Deleted --> [*]
```

Any job can be dropped at any time by deleting its folder; the next `reconcile` records that row as `Deleted`.

## Stage 4 — Reconcile the round

Matching is by the leading job id of each folder name against the id of the row's Job URL. For every open row (`Found`, `Matched`, `Resume Built`, `Resume Failed`, `Pending Review`, `Needs Attention`):

| Where the folder is | Result |
|---|---|
| `Pending-Review/` or `Needs-Attention/` | Row becomes `Submitted`; folder moves to `Submitted/`. |
| `Submitted/` | Row becomes `Submitted` (nothing moves). This is how a job the candidate applied to entirely by hand is closed: they move its folder from `Applications/` to `Submitted/` before reconcile. |
| `Applications/` | Left alone; the job is not reviewed yet. `Applications/` is never touched. |
| nowhere | Row becomes `Deleted`. |

`Deleted`, `Skipped-LowFit` and `Submitted` rows are never rewritten (a `Submitted` row only gets a stray folder moved), but all rows take part in matching and in the invariant. A row whose id is shared by several rows, or whose folder is claimed twice, is left untouched and reported. Folders without a leading id are ignored.

Invariant: the number of `Submitted` rows equals the number of folders in `Submitted/` after the planned changes. If it fails, nothing is written (exit 2).

```mermaid
flowchart TD
    A["Load the tracker"] --> B["Scan the four folder roots"]
    B --> C{"Leading id in the folder name matches exactly one row?"}
    C -- no --> U["Unresolved: reported, left untouched"]
    C -- yes --> D["Folder resolved to its row"]
    D --> E{"For each open row"}
    E -- "folder in Pending-Review or Needs-Attention" --> F["Submitted, folder moved"]
    E -- "folder in Submitted" --> G["Submitted"]
    E -- "folder in Applications" --> H["Left alone"]
    E -- "no folder" --> I["Deleted"]
    F --> J{"Invariant holds?"}
    G --> J
    H --> J
    I --> J
    J -- no --> K["Report, exit 2, nothing written"]
    J -- yes --> L{"--apply?"}
    L -- no --> M["Dry run: report and plan digest"]
    L -- yes --> N["Move folders, save the tracker once, report"]
```

Exit codes: 0 clean, 1 done but some folder unresolved, 2 invariant failed (nothing written), 3 fatal. The dry run prints a plan digest; `--apply --expect <digest>` refuses when the plan changed since.

## Token usage

`end` of the prep and apply runs prints one table per agent type and model (agents, API calls, input, cache write, cache read, output tokens), summed from the transcripts Claude Code writes for the master and every worker it started, plus the Haiku checks. The text may be dropped on its way to the user, so it is also saved to `.ohmyjob/last_report.txt`, and the `report` hook shows it if the user has not seen it.
