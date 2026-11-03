# Contracts

The names, formats and conventions that scripts, hooks and tests share. Change one only together with all its callers.

## workspace.py (`import workspace as W`, standard library only)
Paths are fixed at import (outside a workspace they point into the cwd); the functions look the workspace up when called.
- Layout (setup creates it): the user starts Claude in the project folder; the workspace (`W.ROOT`) is its subfolder
  `ohmyjob/`, holding `ohmyjob.json`, `Profile.md`, `Job_Tracker.xlsx`, `CLAUDE.md`, `Default_Resume_Files/`, the job
  folders and `.ohmyjob/`.
- Constants: `ROOT CONFIG_FILE PROFILE TRACKER` (`Job_Tracker.xlsx`) `DEFAULT_RESUMES APPLICATIONS PENDING_REVIEW
  NEEDS_ATTENTION SUBMITTED CLAUDE_MD SETTINGS_FILE STATE_DIR` (`.ohmyjob`) `ENV_FILE RUN_PREP RUN_APPLY BUILD_DIR
  CATALOG_DIR` (`.ohmyjob/catalog`) `QUESTIONS_FILE REPORT_FILE PLUGIN_ROOT ATS_DIR TEMPLATES_DIR`, and `CONFIG_NAME`
  (`ohmyjob.json`) `WORKSPACE_DIR` (`ohmyjob`) `DEFAULTS SOURCES COUNTRIES EMPLOYMENT_TYPES LANGUAGES TEMPLATE_MARKER
  TEMPLATE_KEY`. There is no `BASE_YAML`: every resume is one full yaml (see "Resume yaml").
- Discovery, ONE rule for `find_root()`, hook.sh, bin/ohmyjob, hooks.py and pj_hooks.py:
  `find_workspace(start) -> Path | None` walks up from `start` (symlinks resolved); at each folder D the workspace is D
  when `D/ohmyjob.json` exists, else `D/ohmyjob` when `D/ohmyjob/ohmyjob.json` exists. So the project folder, a shell
  inside `ohmyjob/` and any folder below either find the same workspace. None for no match, an empty start or a
  relative one. Start folders: `find_root()`: `$OHMYJOB_WORKSPACE` as given (no check), else `$CLAUDE_PROJECT_DIR`,
  else the cwd; it raises `WorkspaceError` (with the `/ohmyjob:setup` hint). hook.sh: `$CLAUDE_PROJECT_DIR`, else
  every stdin `cwd`; hooks.py and pj_hooks.py: the parsed top-level `cwd`, else `$CLAUDE_PROJECT_DIR`; bin/ohmyjob:
  the shell cwd only (the Bash tool sets no `CLAUDE_PROJECT_DIR`).
- `load_config(root=None)`, `validate_config(raw)`: defaults filled in, `ConfigError` names the bad key. Unknown keys are
  errors: a new setting needs `DEFAULTS`, `CHECKS` and `templates/ohmyjob.json`. The one other top-level key accepted
  is `_template` (`TEMPLATE_KEY`, a string): it is not in the returned config, and `template_files()` reports it.
  Validation also rejects what the tools would fail on later: `languages` must be in `W.LANGUAGES` (simplemma's
  tuple, pinned by test; `ja`, `zh`, `xx` are refused), and `country` is required while `indeed` or `glassdoor` is in
  `sources` (JobSpy would search the USA). `DEFAULTS` has `sources: ["linkedin"]`, so `{}` loads.
- `templates/ohmyjob.json` is the mock template (test-pinned): `_template` as its FIRST key (deleting that line leaves
  valid JSON), then `DEFAULTS` with the mock search values `sources ["linkedin", "indeed"]`, `search_query ["Operations
  Analyst"]`, `location "Berlin, Germany"`, `country "germany"`. It loads as it is.
- Template marker: `TEMPLATE_MARKER = "OHMYJOB-TEMPLATE"`. `has_template_marker(path) -> bool`: a raw text search of the
  whole file, nothing parsed; False when the file is missing (an unreadable file raises `OSError`).
  `template_files(root=None) -> list[Path]`: those of `ohmyjob.json` and `Profile.md` (in that order) that still hold it.
- `slots(config=None) -> int`: `min(max_parallel_workers, subagent cap - 1)`; call it late, not at import.
  `file_prefix(root=None) -> str`: ASCII slug of the first word of the `- Name: <First> <Last>` line (also
  `- **Name:** ...`) under the level-1 heading `# Candidate` of `Profile.md`, else `Resume`; tailored files are
  `<prefix>_<Company>.pdf/.yaml`. No resume yaml is read.
- Agents (`ohmyjob:<name>`): `PREP_MASTER SCREEN_WORKER RESUME_WORKER APPLICATION_MASTER APPLICATION_WORKER`; sets
  `PREP_AGENTS PREP_WORKERS APPLICATION_AGENTS`.
- `MASTER_WORKERS`: the subagent types each master may start. `tools: Agent(...)` restricts nothing for plugin agents, so
  every guard checks `tool_input.subagent_type` against it.
- Bash patterns, matched on the stripped command, anything else denied: `PREP_MASTER_CMD_RE`, `RESUME_BUILD_CMD_RE` (group 1
  = job key), `APPLICATION_MASTER_CMD_RE`. `QUESTIONS_BLOCKER`: blocker text of a worker that stops on questions.

## Templates and the mock guard
- Setup writes the templates with the mock persona Alex Example, each with `TEMPLATE_MARKER` on a line the user deletes
  after editing: the first line of `Profile.md` (`<!-- OHMYJOB-TEMPLATE: ... -->`) and of
  `Default_Resume_Files/Template.yaml` (`# OHMYJOB-TEMPLATE: ...`), and the `_template` key of `ohmyjob.json`.
- Mock guard: a command refuses with ONE line that names the file (`ohmyjob: ...` from bin/ohmyjob, `FATAL: ...` from a
  script) and tells the user to replace the mock data and delete the marker line. `scrape` (find-jobs) refuses while
  `W.template_files()` is not empty; `prep` refuses while `Profile.md` holds the marker, never uses `Template.*` or a
  resume file that holds it, and refuses when no usable resume is left; `apply` (also `--continue`) refuses while
  `Profile.md` holds it. Check with `has_template_marker()` / `template_files()` only, never with your own parsing.
- `Profile.md` sections: `# Candidate` (`- Name: ...`), `# Fit Preferences`, `# Resume` (the master pool, optional
  `### HELD IN RESERVE`), `# Follow Up Questions`.

## Resume yaml (one full file per resume)
- Each resume is ONE complete yaml, never merged with another file: `Default_Resume_Files/Template.yaml`, the user's
  `<Field>.yaml` (named by field, e.g. `Data-Analyst.yaml`), and the tailored `<prefix>_<Company>.yaml` in a job folder.
- Keys (`?` = optional): `name{first, last}`, `address`, `phone`, `email`, `homepage?`, `github?`, `linkedin?`, `extra?`,
  `photo?`, `date_of_birth?`, `accent_color?`, `sections?{summary, experience, projects, education, skills}` (heading
  overrides), `title`, `introduction`, `experience[]` (plain `{role, company, location, dates, bullets[]}` or grouped
  `{employer, location, dates, roles[]{role, dates, bullets[]}}`), `projects[]{title, repo?, bullets[]}` (may be `[]`),
  `education[]{degree, school, location, dates, details[]}`, `education_details: yes|no` (print the details),
  `skills[]{label, items}`, `interests?` (text) and `show_interests: yes|no` (print it; the old role flag
  `interests: yes|no` is renamed).
- Text is plain text plus `**bold**` (see resume_build.py). The resume-worker tailors the content keys only: the
  header/contact keys, `sections`, `accent_color` and `education` are frozen (WS-B).
- `ohmyjob resume build-all` (WS-A; `/ohmyjob:build-resumes`): builds every `*.yaml` in `Default_Resume_Files/`
  (`Template.yaml` too) into a PDF with the same name next to it; one line per file (`OK`, `OVERFLOW <N> pages` or
  `FAIL <reason>`), and a warning for a file that still holds the marker.
- Screening catalog (WS-B): the usable resumes are every `<name>.yaml` with a `<name>.pdf` next to it (tailorable) and
  every `<name>.pdf` without a yaml (ready-made, used as is) in `Default_Resume_Files/`; never `Template.*`, never a
  file that holds the marker. `prep start` writes their text to `W.CATALOG_DIR` (yaml: title, introduction, skills; PDF:
  its text, via pypdf), and the screener reads that text, not the PDFs.
- PDF-only path (WS-B): a match on a ready-made PDF needs no resume-worker; prep copies it into the job folder as
  `<prefix>_<Company>.pdf` and sets `Resume Built`, so such a job folder has a PDF and no yaml. A match on a yaml goes
  to the resume-worker as before (`<prefix>_<Company>.pdf/.yaml`).

## tracker.py (openpyxl)
- `Tracker(path)`, `Tracker.create(path)` (also `tracker.create`), `rows()`, `rows_for_key(key)`, `set_cell(index, column,
  value)`, `set_status(index, status, note_line)`, `add_row(dict) -> index`, `save()`; `TrackerError`, `HEADERS`, `STATUSES`;
  `.col`: header -> 0-based column. An unknown status or column, or an `add_row` without values, raises.
- A row dict has the nine headers plus `_index` (header row = 0, sheet row = `_index + 1`) and `_key` (`job_key` of its Job
  URL). `save()` is atomic (temp file + `os.replace`) and raises while `~$Job_Tracker.xlsx` or `.~lock.Job_Tracker.xlsx#`
  exists.
- Stale workbook: a `Tracker` remembers the file's stamp (`st_ino, st_size, st_mtime_ns`) from its load (and from its own
  last save). `save()` writes the temp file, checks the stamp again, then replaces, and raises
  `TrackerError` ("... changed on disk since this run loaded it ... Nothing was saved; run the command again.") when the
  file changed meanwhile, so a long run cannot revert statuses another command wrote. Callers load, change and save in one
  short step, and treat that error as a stop (print `FATAL ...`), never as a reason to overwrite.
- Cell text: every string written (`set_cell`, `set_status` notes, `add_row`) is stored as text (a scraped `=...` is never a
  formula), loses control characters xlsx cannot hold (also lone surrogates and U+FFFE/U+FFFF), and is clipped to 32767 characters (`Notes` keeps its newest lines).

## job_key.py
- `job_key(url)`: a LinkedIn `/jobs/view/<id>` keeps its id; any other http(s) address gives `9` + 14 hash digits (utm_*
  ignored). A key in use never changes. `strip_tracking(url)` drops utm_* and `VOLATILE_PARAMS` before an address is stored
  (add a parameter there when a board's scrape shows it changing between runs); `LINKEDIN_PARAMS` (`trk`, `refid`, ...)
  are dropped on LinkedIn hosts only. Also `is_linkedin_url`, `LINKEDIN_ID_RE`.

## resume_build.py
- `build(role_yaml, out_pdf, *, max_pages=None, paper=None) -> (status, pages, detail)`, status `OK`, `OVERFLOW` or `FAIL`.
  `None` reads `pipeline.resume_max_pages` / `resume_paper` from the config; `max_pages=0` means no page limit. It reads
  ONE full resume yaml (see "Resume yaml"; nothing is merged in), builds in its own dir under `W.BUILD_DIR` (class
  copied in, dir removed after), runs `$OHMYJOB_LUALATEX`, else `lualatex` on PATH, with `-interaction=nonstopmode
  -halt-on-error` and a timeout, and counts pages from the log. It deletes `out_pdf` first, so OVERFLOW and FAIL leave
  no PDF.
- CLI `ohmyjob resume build <yaml> [--out <pdf>]` prints exactly one line: `OK <pdf>`, `OVERFLOW <N> pages` or
  `FAIL <reason>`.
- `BULLET_BUDGET`: rendered characters of ONE bullet line. `SKILLS_BUDGET`: rendered characters of the items of ONE skills
  row (the label is not counted). Both are checked before compiling, with `rendered_len(text)` (text as printed);
  `render(role_yaml, *, paper=None, photo="")` returns the LaTeX source.
- Markup: yaml text is plain text plus `**bold**`. Everything else, backslashes included, prints literally. Links accept
  http(s) only; a bare address gets `https://`.

## bin/ohmyjob
- `ohmyjob prep|apply|scrape|reconcile|resume <args>` runs `scripts/pj.py|aa.py|scrape.py|reconcile.py|resume_build.py` with
  `$OHMYJOB_PY`, finds the workspace (`$OHMYJOB_WORKSPACE`, else the discovery rule from the shell cwd) and exports
  `OHMYJOB_WORKSPACE OHMYJOB_PY OHMYJOB_UV OHMYJOB_DATA OHMYJOB_LUALATEX`. No workspace: `ohmyjob: no ohmyjob.json in
  <cwd> or above, ...` (exit 3). `.ohmyjob/env.sh` (setup rewrites it on every run) sets the last four.
- `env.sh` is parsed, never sourced (agents can write it and `Bash(ohmyjob *)` is pre-allowed). Allowed lines: blank, `#`
  comments, and `NAME='value'` or `NAME=value` for `OHMYJOB_PY OHMYJOB_UV OHMYJOB_DATA OHMYJOB_LUALATEX`. A quoted value
  is taken literally (spaces and `$` are fine, no single quote inside); an unquoted one holds no quote, `$`, backtick,
  backslash or whitespace. Any other line, name or value is an `ohmyjob: ...` error (exit 3) naming env.sh. Setup writes
  single-quoted values, and an empty `OHMYJOB_LUALATEX=''` is fine. A value that is syntactically fine is still refused
  unless `OHMYJOB_PY` is exactly `$OHMYJOB_DATA/venv/bin/python` (else the stale-environment line), a non-empty
  `OHMYJOB_UV` has basename `uv` and a non-empty `OHMYJOB_LUALATEX` basename `lualatex`: `ohmyjob prep <args>` must never
  run a program the file picks. (A caller's own `OHMYJOB_PY`, as in tests, skips env.sh.)
- Errors are one stdout line starting `ohmyjob: `, exit 3; no `env.sh` or no executable Python gives exactly `ohmyjob:
  environment missing or stale, run /ohmyjob:setup`. The scripts print their own `FATAL: ...` lines.
- Only `prep start` and `apply start` re-sync, when sha256(`uv.lock`) differs from `$OHMYJOB_DATA/venv/uv.lock.sha256`:
  `UV_PROJECT_ENVIRONMENT=$OHMYJOB_DATA/venv UV_PYTHON_INSTALL_DIR=$OHMYJOB_DATA/python UV_CACHE_DIR=$OHMYJOB_DATA/uv-cache uv sync --frozen --no-dev --quiet --project <plugin>` (every install stays in the data dir) (bootstrap writes the
  stamp too).

## Hooks: `hooks/hooks.json` -> `scripts/hook.sh <event>` -> `pj_hooks.py` or `hooks.py <event>`
- Events: `guard` (PreToolUse), `handback` (PreToolUse of `SubagentHandback`), `snapshot` (PostToolUse of Chrome tools),
  `capture` (SubagentStop), `report` (Stop).
- hook.sh stays in bash, without Python, unless (tested in this order): `guard` of ANY Chrome tool
  (`mcp__claude-in-chrome__*`), whoever calls (main session, Explore, a prep agent, any agent) -> `hooks.py`, whatever the
  agent_type text says; any other call with a `ohmyjob:prep-master|screen-worker|resume-worker` agent_type -> `pj_hooks.py`;
  `ohmyjob:application-*` -> `hooks.py`; `snapshot` of every Chrome call (hooks.py records each caller's page refs for the never-submit check, compacts pages only for `application-worker`, and reads the master's own tabs in preflight); `report` only if
  `.ohmyjob/last_report.txt` exists. `handback` and `capture` route like `guard`, for ohmyjob agents only. Everything else
  (the main session's non-Chrome calls, other agents' non-Chrome calls, every call outside a workspace): exit 0, no output,
  no Python. A `guard` call inside a workspace whose input has no readable `tool_name` (empty stdin, a path with a quote or backslash
  that defeats the text match) is denied: exit 2, one line on stderr. Without `$CLAUDE_PLUGIN_DATA/venv/bin/python` (an unset or empty `CLAUDE_PLUGIN_DATA` counts as missing),
  `guard` and `handback` of an `ohmyjob:*` agent, and `guard` of a Chrome call by any agent, are denied (the message above);
  the main session's Chrome call and the other events print `{"systemMessage": ...}` and exit 0.
- Why Chrome is routed whatever the agent: the never-submit rule cannot hang on `agent_type`. Explore and other agents have
  every tool, Chrome included, and the settings template carries no `Agent(...)` deny rules (they would block the owner's
  own agents). **`hooks.py guard` applies the main-session Chrome rules (deny `javascript_tool`, deny final-button clicks)
  to every caller that is not an `ohmyjob:*` agent**, including non-ohmyjob subagents.
- Spoofing rule (WS-B, WS-C): the model controls `tool_input`, so it can put any text there, even an object holding
  `"agent_type"`, `"tool_name"` or `"cwd"` keys. hook.sh's text matches are a PREFILTER: it takes every match, not the
  first, and a false match may start Python once but never changes a decision. So **`pj_hooks.py` and `hooks.py` must
  parse the JSON and decide only from the top-level `agent_type`, `tool_name` and `cwd`**, never from `tool_input` text.
  The choice between the two scripts rests on a text test, so each re-checks the parsed `agent_type`: `pj_hooks.py` reached
  with a parsed `agent_type` that is not a prep agent runs `hooks.py`'s rules on the same input, and **`hooks.py` reached
  with a parsed prep agent (always the case for a prep agent's Chrome call) runs `pj_hooks.py`'s rules** (import and call
  its `main`). The workspace hook.sh exports is a candidate too: Python takes
  `W.find_workspace()` of the parsed top-level `cwd`, else of `$CLAUDE_PROJECT_DIR`, and exits 0 with no output when
  there is none. It imports `workspace` for that while `OHMYJOB_WORKSPACE` still names the candidate, so it reloads the
  module (`importlib.reload`) after setting `OHMYJOB_WORKSPACE` to the workspace it found, before importing anything
  that reads `W` paths.
- Python gets the hook JSON on stdin, the event as `argv[1]`, and `OHMYJOB_WORKSPACE`: the workspace hook.sh found with
  the discovery rule from `$CLAUDE_PROJECT_DIR` (the project folder, or a folder in it), else from every stdin `cwd`
  (Claude may start outside the workspace and `cd` in; `CLAUDE_PROJECT_DIR` is unset for some callers). `find_root()`
  returns it as given.
- stdin: `session_id transcript_path cwd hook_event_name tool_name tool_input`; PostToolUse adds `tool_response`; a
  subagent's calls add `agent_id`, `agent_type` (scoped, e.g. `ohmyjob:resume-worker`; both absent in the main session);
  SubagentStop and Stop add `last_assistant_message` (may be null). `tool_name` is `Agent` for a spawn.
- Deny: reason on stderr, exit 2 (a tool error for the agent). The guard has `onFailure: block`: a crash blocks the call too.
- Allow every validated call of an `ohmyjob:*` agent (a background agent cannot answer a permission prompt; deny and ask
  rules still win): print `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow"}}`, exit 0. To
  rewrite the call add `"updatedInput": {<whole new tool_input>}` beside it. Exit 0 with no output means no decision.
- Replace a tool's output (PostToolUse): `{"hookSpecificOutput":{"hookEventName":"PostToolUse","updatedToolOutput":<shaped
  like tool_response>}}`; not the older, MCP-only `updatedMCPToolOutput`.
- A worker's report arrives by two equal paths: `handback` (`tool_input.message`; auto mode only) and `capture`
  (`last_assistant_message`; the only path in Manual and `-p` mode). Both feed one report processor. Never assume `handback`
  fires; let `capture` see a null message. `report` runs after every turn (2 to 3 times per run), so it must be idempotent.

## Tests
- `tests/fixtures/workspace/` (fictional persona Alex Example, an edited workspace: no template marker): `ohmyjob.json`,
  `Profile.md` (`# Candidate` with `- Name: Alex Example`, then Fit Preferences, Resume, Follow Up Questions),
  `Default_Resume_Files/{Operations-Analyst,Project-Coordinator}.yaml` (full resumes: the first with a grouped employer,
  `show_interests: yes`, `education_details: yes`; the second with `projects: []` and both flags `no`),
  `Applications/4399000001_Lumenfield-Logistics_Operations-Analyst/job.md` (LinkedIn id, `- Key: value`) and
  `Applications/969845171025937_Quillon-Systems_Project-Coordinator/job.md` (another board, `- **Key:** value`, Apply URL).
  No PDFs, no tracker, no `base.yaml`.
- The `workspace` fixture (`tests/conftest.py`) returns a tmp copy (with empty `Pending-Review`, `Needs-Attention`,
  `Submitted`) and sets `OHMYJOB_WORKSPACE`; scripts read their paths at import, so run them in a subprocess and import the
  module as `W`. For the v2 layout, rename the copy to `<tmp>/project/ohmyjob` (the `project` fixtures of the P1 tests).
