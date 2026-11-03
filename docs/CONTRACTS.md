# Contracts

The names, formats and conventions that scripts, hooks and tests share. Change one only together with all its callers.

## workspace.py (`import workspace as W`, standard library only)
Paths are fixed at import (outside a workspace they point into the cwd); the functions look the workspace up when called.
- Constants: `ROOT CONFIG_FILE PROFILE TRACKER` (`Job_Tracker.xlsx`) `DEFAULT_RESUMES BASE_YAML APPLICATIONS PENDING_REVIEW
  NEEDS_ATTENTION SUBMITTED CLAUDE_MD SETTINGS_FILE STATE_DIR` (`.ohmyjob`) `ENV_FILE RUN_PREP RUN_APPLY BUILD_DIR
  QUESTIONS_FILE REPORT_FILE PLUGIN_ROOT ATS_DIR TEMPLATES_DIR`, and `DEFAULTS SOURCES COUNTRIES EMPLOYMENT_TYPES`.
- `find_root() -> Path`: `$OHMYJOB_WORKSPACE` as given, else the nearest folder with `ohmyjob.json` at or above
  `$CLAUDE_PROJECT_DIR`, else at or above the cwd; raises `WorkspaceError`.
- `load_config(root=None)`, `validate_config(raw)`: defaults filled in, `ConfigError` names the bad key. Unknown keys are
  errors: a new setting needs `DEFAULTS`, `CHECKS` and `templates/ohmyjob.json` (equal to `DEFAULTS` by test).
- `slots(config=None) -> int`: `min(max_parallel_workers, subagent cap - 1)`; call it late, not at import.
  `file_prefix(root=None) -> str`: ASCII slug of `name.first` in `base.yaml`, else `Resume`; tailored files are
  `<prefix>_<Company>.pdf/.yaml`.
- Agents (`ohmyjob:<name>`): `PREP_MASTER SCREEN_WORKER RESUME_WORKER APPLICATION_MASTER APPLICATION_WORKER`; sets
  `PREP_AGENTS PREP_WORKERS APPLICATION_AGENTS`.
- `MASTER_WORKERS`: the subagent types each master may start. `tools: Agent(...)` restricts nothing for plugin agents, so
  every guard checks `tool_input.subagent_type` against it.
- Bash patterns, matched on the stripped command, anything else denied: `PREP_MASTER_CMD_RE`, `RESUME_BUILD_CMD_RE` (group 1
  = job key), `APPLICATION_MASTER_CMD_RE`. `QUESTIONS_BLOCKER`: blocker text of a worker that stops on questions.

## tracker.py (openpyxl)
- `Tracker(path)`, `Tracker.create(path)` (also `tracker.create`), `rows()`, `rows_for_key(key)`, `set_cell(index, column,
  value)`, `set_status(index, status, note_line)`, `add_row(dict) -> index`, `save()`; `TrackerError`, `HEADERS`, `STATUSES`;
  `.col`: header -> 0-based column. An unknown status or column, or an `add_row` without values, raises.
- A row dict has the nine headers plus `_index` (header row = 0, sheet row = `_index + 1`) and `_key` (`job_key` of its Job
  URL). `save()` is atomic (temp file + `os.replace`) and raises while `~$Job_Tracker.xlsx` or `.~lock.Job_Tracker.xlsx#`
  exists.

## job_key.py
- `job_key(url)`: a LinkedIn `/jobs/view/<id>` keeps its id; any other http(s) address gives `9` + 14 hash digits (utm_*
  ignored). A key in use never changes. `strip_tracking(url)` drops utm_* and `VOLATILE_PARAMS` before an address is stored
  (add a parameter there when a board's scrape shows it changing between runs). Also `is_linkedin_url`, `LINKEDIN_ID_RE`.

## bin/ohmyjob
- `ohmyjob prep|apply|scrape|reconcile|resume <args>` runs `scripts/pj.py|aa.py|scrape.py|reconcile.py|resume_build.py` with
  `$OHMYJOB_PY`, finds the workspace like `find_root()` and exports `OHMYJOB_WORKSPACE OHMYJOB_PY OHMYJOB_UV OHMYJOB_DATA
  OHMYJOB_LUALATEX`. `.ohmyjob/env.sh` (plain `NAME='/abs/path'` lines; setup rewrites it on every run) sets the last four.
- Errors are one stdout line starting `ohmyjob: `, exit 3; no `env.sh` or no executable Python gives exactly `ohmyjob:
  environment missing or stale, run /ohmyjob:setup`. The scripts print their own `FATAL: ...` lines.
- Only `prep start` and `apply start` re-sync, when sha256(`uv.lock`) differs from `$OHMYJOB_DATA/venv/uv.lock.sha256`:
  `UV_PROJECT_ENVIRONMENT=$OHMYJOB_DATA/venv uv sync --frozen --no-dev --quiet --project <plugin>` (bootstrap writes the
  stamp too).

## Hooks: `hooks/hooks.json` -> `scripts/hook.sh <event>` -> `pj_hooks.py` or `hooks.py <event>`
- Events: `guard` (PreToolUse), `handback` (PreToolUse of `SubagentHandback`), `snapshot` (PostToolUse of Chrome tools),
  `capture` (SubagentStop), `report` (Stop).
- hook.sh stays in bash, without Python, unless: `ohmyjob:prep-master|screen-worker|resume-worker` -> `pj_hooks.py`;
  `ohmyjob:application-*` -> `hooks.py`; main session -> `hooks.py guard` for Chrome tools only; `snapshot` only for
  `application-worker`; `report` only if `.ohmyjob/last_report.txt` exists. `handback` and `capture` route like `guard`.
  Other callers: exit 0, no output. Without `$CLAUDE_PLUGIN_DATA/venv/bin/python`, `guard` and `handback` of an `ohmyjob:*`
  agent are denied (the message above); the rest prints `{"systemMessage": ...}` and exits 0.
- Python gets the hook JSON on stdin, the event as `argv[1]`, and `OHMYJOB_WORKSPACE`: the workspace hook.sh found by walking
  up from `$CLAUDE_PROJECT_DIR` (Claude may start in a subfolder). `find_root()` returns it as given.
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
- `tests/fixtures/workspace/` (fictional persona Alex Example): `ohmyjob.json`, `Profile.md`,
  `Default_Resume_Files/{base,Operations-Analyst,Project-Coordinator}.yaml` (a grouped employer; `projects: []`),
  `Applications/4399000001_Lumenfield-Logistics_Operations-Analyst/job.md` (LinkedIn id, `- Key: value`) and
  `Applications/969845171025937_Quillon-Systems_Project-Coordinator/job.md` (another board, `- **Key:** value`, Apply URL).
  No PDFs or tracker.
- The `workspace` fixture (`tests/conftest.py`) returns a tmp copy (with empty `Pending-Review`, `Needs-Attention`,
  `Submitted`) and sets `OHMYJOB_WORKSPACE`; scripts read their paths at import, so run them in a subprocess and import the
  module as `W`.
