---
name: setup
description: Set up or repair the ohmyjob workspace in the current folder - install the Python environment and LaTeX, create the tracker, folders and settings, give the Chrome extension steps, then run the profile interview and build the resumes. Safe to run again as a health check. Use only when the user types /ohmyjob:setup.
disable-model-invocation: true
allowed-tools: Bash(bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh" *), Bash(claude plugin list*), Bash(ohmyjob *), Read
---

# Set up the job-search workspace

The workspace is the folder Claude was started in. Setup is safe to repeat: it never overwrites your files, and it rewrites only `.ohmyjob/env.sh` when nothing changed.

Rule for every command below: a line of output that starts with `ohmyjob: ` or `FATAL` is an error. Show that line as it is, say what it asks for, and stop. Do not retry in a loop and do not work around it.

## 1. Check the folder
Look at the current working directory. If it has no `ohmyjob.json` and looks like a home folder or a code repository, ask the user to confirm that this is the folder for the job search; if not, tell them to create one (`mkdir ~/JobSearch && cd ~/JobSearch`), install the plugin there and start Claude there, then stop.

## 2. Ask before installing anything
Tell the user, in short lines, what setup will do, and ask with AskUserQuestion whether to go ahead:
- Python packages go into the plugin's data folder `${CLAUDE_PLUGIN_DATA}/venv` (downloaded with uv, no admin rights).
- If `uv` is missing, it is installed from the official script into `${CLAUDE_PLUGIN_DATA}/bin` (asked separately below).
- LaTeX: a sample resume is built with the LaTeX on this computer. Only when that fails is TinyTeX offered (asked separately below), into `${CLAUDE_PLUGIN_DATA}/tinytex`. No system folder is touched.
- This folder gets `Job_Tracker.xlsx`, the job folders, `ohmyjob.json`, `CLAUDE.md`, `.gitignore` (each only when missing) and `.claude/settings.json` (merged: your own keys stay).
Stop here if the answer is no.

## 3. Run bootstrap
Run exactly:
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.sh" --data "${CLAUDE_PLUGIN_DATA}"`
Run it with a long timeout (the first run downloads packages).

If it ends with an `ohmyjob: ` line:
- `uv is not installed`: ask the user whether uv may be installed with its official installer (it downloads and runs a script from astral.sh). On yes, run the same command again with `--install-uv` added. On no, stop.
- `no working lualatex`: ask whether TinyTeX may be installed (a small TeX distribution from the TinyTeX project, about 250 MB, into the data folder). On yes, run again with `--install-tinytex` added (add `--install-uv` too if you used it). On no, stop and say the resumes cannot be built without it.
- Any other `ohmyjob: ` line (for example Claude Code too old: tell the user to run `claude update`): stop.
The command is safe to repeat. Success ends with `bootstrap: OK`; the lines `extension:` and `lualatex:` give the extension folder and the LaTeX in use.

## 4. Check the plugin is enabled in this project
Run `claude plugin list` and look for `ohmyjob@ohmyjob`. It must be installed with scope project or local and enabled. If it is only in user scope, or missing, tell the user to run in this folder:
`claude plugin marketplace add Maaz77/ohmyjob --scope project` and `claude plugin install ohmyjob@ohmyjob --scope project`, then start Claude again here and run `/ohmyjob:setup` again. Stop in that case (hooks that keep Claude from pressing Submit only protect a project where the plugin is enabled).

## 5. Chrome extension and site permissions
Print this for the user, with the real path from the `extension:` line:
1. Claude in Chrome needs a direct Anthropic plan (Pro, Max, Team or Enterprise) signed in with `/login` (an API key turns it off), and Chrome, Edge or Brave (not WSL).
2. Open `chrome://extensions`, switch on Developer mode, click "Load unpacked" and choose `<extension path>`. Keep that folder: it is a stable copy that survives plugin updates.
3. Open the Claude extension's settings in Chrome and allow it on the sites you will apply on (the job boards and the company career sites). Per-site permissions live in the extension; nothing here can set them.
4. Sign in to LinkedIn (and any board you use) in that same Chrome profile.

## 6. Profile and resumes
- If `Profile.md` or `Default_Resume_Files/base.yaml` is missing, call the Skill tool with skill `ohmyjob:profile` and wait for it to finish.
- Then, if `Default_Resume_Files/` holds no `.pdf`, call the Skill tool with skill `ohmyjob:resumes`.
- When both exist, setup is complete.

## 7. Launch line (say this last)
Tell the user: setup is complete. Close this session, then start Claude in the workspace root (this folder; project settings and hooks load only from the folder Claude starts in) with:
`claude --setting-sources project,local --strict-mcp-config --chrome`
Then the daily loop is `/ohmyjob:find-jobs`, `/ohmyjob:prep-jobs`, `/ohmyjob:apply-jobs`, fix the tabs in "Needs Attention", `/ohmyjob:continue-applying`, submit by hand, `/ohmyjob:reconcile`.
