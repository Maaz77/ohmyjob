---
name: setup
description: Prepare the ohmyjob workspace in the current project folder - check Claude Code, the plugin and Claude in Chrome, install the Python environment and LaTeX into the plugin's data folder, create the ohmyjob/ folder with mock templates and a Template.pdf, then print what to fill in. Never asks about you and never writes your data. Safe to run again. Use only when the user types /ohmyjob:setup.
disable-model-invocation: true
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh" *), Bash(claude plugin list*), Bash(ohmyjob *), Read, mcp__claude-in-chrome__tabs_context_mcp
---

# Set up the job-search workspace

The project folder is the folder Claude was started in. Setup creates `ohmyjob/` in it (the workspace), merges `.claude/settings.json`, and installs tools into the plugin's data folder. It never interviews the user and never writes the user's real data: the files it creates hold mock data (the persona "Alex Example") for the user to replace. It is safe to repeat: it never overwrites a file that exists and rewrites only `ohmyjob/.ohmyjob/env.sh`.

Rule for every command below: a line of output that starts with `ohmyjob: ` or `FATAL` is an error. Show that line as it is, say what it asks for, and stop. Do not retry in a loop and do not work around it.

## 1. Check the folder
Look at the current working directory. If it looks like a home folder or a code repository and has no `ohmyjob/` folder, ask the user to confirm that this is the project folder for the job search; if not, tell them to create one (`mkdir -p /path/to/job-search && cd /path/to/job-search`, with a path of their own), install the plugin there and start Claude there, then stop.

## 2. Checks (warn, then continue unless stated)
- **Claude Code version:** bootstrap checks it (2.1.306 or newer) and stops with an `ohmyjob: ` line when it is older: tell the user to run `claude update`.
- **Plugin scope:** run `claude plugin list` and look for `ohmyjob@ohmyjob`. It must be installed with scope project or local and enabled. If it is only in user scope, or missing, tell the user to run in this folder `claude plugin marketplace add Maaz77/ohmyjob --scope project` and `claude plugin install ohmyjob@ohmyjob --scope project`, start Claude again here and run `/ohmyjob:setup` again, and stop (the hooks that keep Claude from pressing Submit only protect a project where the plugin is enabled).
- **Claude in Chrome:** call the Chrome tool `tabs_context_mcp` (it only lists tabs). If it fails or is not available, WARN and continue: only `/ohmyjob:apply-jobs` and `/ohmyjob:continue-applying` need Chrome. Give the steps to turn it on: a direct Anthropic plan (Pro, Max, Team or Enterprise) signed in with `/login` (an API key turns it off); Chrome, Edge or Brave (not WSL); the Claude extension installed from the Chrome Web Store and signed in; Claude started with `--chrome`; then run `/ohmyjob:setup` again to confirm.

## 3. Ask before installing anything
Tell the user, in short lines, what setup will do, and ask with AskUserQuestion whether to go ahead:
- Python packages and uv's own Python go into the plugin's data folder `${CLAUDE_PLUGIN_DATA}` (venv, python, uv-cache), downloaded with uv. Nothing goes to your PATH, shell profile or home folder.
- If `uv` is missing, it is installed from the official script into `${CLAUDE_PLUGIN_DATA}/bin` (asked separately below).
- LaTeX: the template resume is built with the LaTeX on this computer. Only when that fails is TinyTeX offered (asked separately below), into `${CLAUDE_PLUGIN_DATA}/tinytex`. No system folder is touched.
- This folder gets `ohmyjob/` with `ohmyjob.json`, `Profile.md`, `CLAUDE.md`, `.gitignore`, `Job_Tracker.xlsx`, the job folders and `Default_Resume_Files/Template.yaml` + `Template.pdf` and `Template-CV.yaml` + `Template-CV.pdf` (each only when missing; mock data), and `.claude/settings.json` (merged: your own keys stay).
Stop here if the answer is no.

## 4. Run bootstrap
Run exactly:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh" --data "${CLAUDE_PLUGIN_DATA}"`
Run it with a long timeout (the first run downloads packages).

If it ends with an `ohmyjob: ` line:
- `uv is not installed`: ask the user whether uv may be installed with its official installer (it downloads and runs a script from astral.sh). On yes, run the same command again with `--install-uv` added. On no, stop.
- `no working lualatex`: ask whether TinyTeX may be installed (a small TeX distribution from the TinyTeX project, about 250 MB, into the data folder). On yes, run again with `--install-tinytex` added (add `--install-uv` too if you used it). On no, stop and say the resumes cannot be built without it.
- Any other `ohmyjob: ` line: stop.
Success ends with `bootstrap: OK`; the lines `workspace:`, `extension:` and `lualatex:` give the folders and the LaTeX in use.

## 5. Set up Chrome
Print all five steps for the user, with the real path from the `extension:` line (Chrome is needed only for
`/ohmyjob:apply-jobs` and `/ohmyjob:continue-applying`, but each of these steps blocks a run on its own):
1. Install the Claude in Chrome extension from the Chrome Web Store in Chrome, Edge or Brave, and sign in to it with the same Anthropic account as Claude Code.
2. Load the ohmyjob helper extension: open `chrome://extensions`, switch on Developer mode, click "Load unpacked" and choose `<extension path>`. Keep that folder: it is a stable copy that survives plugin updates. It opens page pop-ups (a sign-in window, for example) as tabs the workers can reach, and groups the finished tabs.
3. Open the Claude extension's settings in Chrome and allow it on the sites you will apply on (the job boards and the company career sites). Per-site permissions live in the extension; nothing here can set them.
4. Sign in to LinkedIn (and any board you use) in that same Chrome profile. The workers use the session already in the browser; they never type a password.
5. Sign in to Google in that same Chrome profile, with the account whose email is in `Profile.md`. "Continue with Google" is the one sign-in route a worker can take by itself; without it, every site with a wall in front of its form ends at "Needs Attention".

## 6. Next steps (print this, then go to step 7)
Tell the user, with the real `workspace:` path:
1. In `ohmyjob/Default_Resume_Files/`, copy `Template.yaml` (a resume) or `Template-CV.yaml` (a CV) to one file per role the user applies for, named by field (for example `Data-Analyst.yaml`), replace the mock data and delete the marker line in each. Ready-made PDF resumes can be dropped in instead, also named by field (for example `Nurse-ICU.pdf`), with no yaml beside them: they are sent as they are and never tailored.
2. Run `/ohmyjob:build-resumes` to build a PDF next to every resume yaml (never `Template*.yaml`; `/ohmyjob:build-resumes <file.yaml>` builds one file). A tailored resume later gets exactly the page count of its source PDF, so build each resume at the length you want. A yaml without a built PDF cannot be used.
3. Run `/ohmyjob:master-resume`: it drafts the `# Resume` section of `Profile.md` from those resume files (yamls and PDFs; the old `Profile.md` is saved first).
4. Open `ohmyjob/Profile.md`: revise the drafted `# Resume` section by hand (tailored resumes use it as their only source of facts), then write `# Fit Preferences` (three fields: seniority / experience level, what you are looking for, dealbreakers; the screening judges every posting against them first) and `# Follow Up Questions` (a numbered list of the questions forms ask, with your answers, work authorization included). Then delete the first line, the `OHMYJOB-TEMPLATE` marker line.
5. Open `ohmyjob/ohmyjob.json` and set your job boards, search titles, location and country (a country is required while `indeed` or `glassdoor` is a source). Every field, its default and its valid values are in the README table. Then delete the `"_template"` line. `/ohmyjob:help` prints every field.
6. Close this session, then start Claude in the project folder (the folder that holds `.claude/` and `ohmyjob/`; project settings and hooks load only from the folder Claude starts in) with:
`claude --setting-sources project,local --strict-mcp-config --chrome`
7. The four stages of a round: `/ohmyjob:find-jobs`, `/ohmyjob:prep-jobs`, `/ohmyjob:apply-jobs` (fix the tabs in "Needs Attention", then `/ohmyjob:continue-applying`, then submit each parked form by hand), `/ohmyjob:reconcile`.
Until the marker lines are deleted, the commands refuse to run: that is intended.

## 7. Explain `ohmyjob.json` (last, after the list above)
Tell the user that `/ohmyjob:help` prints the commands, the config table and the job status life cycle, and that `ohmyjob.json` is plain JSON without comments (the README table is the guide to every field). Then explain how to edit it safely:
- Edit only the values; keep the quotes, the commas between entries and no comma after the last entry of a block. A list is `["a", "b"]`, a flag is `true` or `false` without quotes, a number has no quotes.
- No comments are allowed in the file. Delete the whole `"_template": ...` line (with its comma) when the values are yours. A wrong value or a typo in a key is refused with a message that names the key; fix it and run the command again.
