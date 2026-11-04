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
Look at the current working directory. If it looks like a home folder or a code repository and has no `ohmyjob/` folder, ask the user to confirm that this is the project folder for the job search; if not, tell them to create one (`mkdir ~/JobSearch && cd ~/JobSearch`), install the plugin there and start Claude there, then stop.

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

## 5. The Chrome helper extension
Print for the user, with the real path from the `extension:` line (only needed for `/ohmyjob:apply-jobs` and `/ohmyjob:continue-applying`):
1. Open `chrome://extensions`, switch on Developer mode, click "Load unpacked" and choose `<extension path>`. Keep that folder: it is a stable copy that survives plugin updates.
2. Open the Claude extension's settings in Chrome and allow it on the sites you will apply on (the job boards and the company career sites). Per-site permissions live in the extension; nothing here can set them.
3. Sign in to LinkedIn (and any board you use) in that same Chrome profile.

## 6. Next steps (print this, then go to step 7)
Tell the user, with the real `workspace:` path:
1. Open `ohmyjob/Profile.md` and replace the mock data with yours: `# Fit Preferences` (three fields: seniority / experience level, what you are looking for, dealbreakers; the screening judges every posting against them first), `# Resume` (the guidance lines only say what goes where; step 5 drafts it for you) and `# Follow Up Questions` (a numbered list of the questions forms ask, with your answers, work authorization included). Then delete the first line, the `OHMYJOB-TEMPLATE` marker line.
2. Open `ohmyjob/ohmyjob.json` and set your job boards, search titles, location and country (a country is required while `indeed` or `glassdoor` is a source). Every field, its default and its valid values are in the README table. Then delete the `"_template"` line. `/ohmyjob:help` prints every field.
3. In `ohmyjob/Default_Resume_Files/`, copy `Template.yaml` (or `Template-CV.yaml`) to one file per role you apply for, named by field (for example `Data-Analyst.yaml`), replace the mock data and delete the marker line in each. Optionally add ready-made PDF resumes there, also named by field (for example `Nurse-ICU.pdf`); they are used as they are.
4. Run `/ohmyjob:build-resumes` to build a PDF next to every resume yaml (never `Template*.yaml`; `/ohmyjob:build-resumes <file.yaml>` builds one file). A tailored resume later gets exactly the page count of its source PDF, so build each resume at the length you want.
5. Run `/ohmyjob:master-resume`: it drafts the `# Resume` section of `Profile.md` from your resume files (yamls and PDFs; the old `Profile.md` is saved first). Then revise that section by hand: tailored resumes use it as their only source of facts.
6. Close this session, then start Claude in the project folder (the folder that holds `.claude/` and `ohmyjob/`; project settings and hooks load only from the folder Claude starts in) with:
`claude --setting-sources project,local --strict-mcp-config --chrome`
7. The daily loop: `/ohmyjob:find-jobs`, `/ohmyjob:prep-jobs`, `/ohmyjob:apply-jobs`, fix the tabs in "Needs Attention", `/ohmyjob:continue-applying`, submit by hand, `/ohmyjob:reconcile`.
Until the marker lines are deleted, the commands refuse to run: that is intended.

## 7. Explain `ohmyjob.json` (last, after the list above)
Tell the user that `/ohmyjob:help` prints the commands, the config table and the job status life cycle, and that `ohmyjob.json` is plain JSON without comments (the README table is the guide to every field). Then explain how to edit it safely:
- Edit only the values; keep the quotes, the commas between entries and no comma after the last entry of a block. A list is `["a", "b"]`, a flag is `true` or `false` without quotes, a number has no quotes.
- No comments are allowed in the file. Delete the whole `"_template": ...` line (with its comma) when the values are yours. A wrong value or a typo in a key is refused with a message that names the key; fix it and run the command again.
