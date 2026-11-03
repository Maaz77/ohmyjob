#!/usr/bin/env bash
# One entry for every ohmyjob hook: hook.sh <guard|handback|snapshot|report|capture>, hook JSON on stdin.
# Hooks fire on every Read/Edit/Bash/Chrome call of every session, so a call that no ohmyjob rule covers
# must end here, in bash, without starting Python. The rest goes to the plugin's venv Python:
#   ohmyjob:prep-master|screen-worker|resume-worker -> pj_hooks.py
#   ohmyjob:application-*                           -> hooks.py
#   main session (no agent_type), Chrome tools only -> hooks.py
#   anything else                                   -> exit 0
# handback (SubagentHandback, auto mode only) and capture (SubagentStop, where a worker's last message is its report
# in Manual and -p mode) are routed exactly like guard: by agent_type, never assuming that handback fires.
set -u

event="${1:-}"

# The workspace is the nearest folder with ohmyjob.json at or above CLAUDE_PROJECT_DIR (the folder Claude was started
# in, which can be a subfolder of it), up to /. None: not an ohmyjob workspace, do nothing. No forks: this runs on
# every call. (A project dir that is not an absolute path is no workspace.)
root="${CLAUDE_PROJECT_DIR:-}"
while [ -n "$root" ] && [ ! -f "$root/ohmyjob.json" ]; do
  case "$root" in /) root="" ;; /*/*) root="${root%/*}" ;; /*) root="/" ;; *) root="" ;; esac
done
[ -n "$root" ] || exit 0

input="$(cat)"  # stdin can be read once; the Python script gets it again below

# The hook JSON carries agent_type and tool_name as plain top-level keys. A copy of them inside a string value
# is escaped (\"agent_type\"), so this pattern cannot match it. Python re-reads the JSON and decides for itself.
agent="" tool=""
re='"agent_type"[[:space:]]*:[[:space:]]*"([^"]*)"'
[[ $input =~ $re ]] && agent="${BASH_REMATCH[1]}"
re='"tool_name"[[:space:]]*:[[:space:]]*"([^"]*)"'
[[ $input =~ $re ]] && tool="${BASH_REMATCH[1]}"

script=""
case "$event" in
  guard|handback|capture)
    case "$agent" in
      ohmyjob:prep-master|ohmyjob:screen-worker|ohmyjob:resume-worker) script="pj_hooks.py" ;;
      ohmyjob:application-*) script="hooks.py" ;;
      "") # the main session: only its Chrome calls are checked (javascript_tool, final-button clicks)
        if [ "$event" = "guard" ]; then
          case "$tool" in mcp__claude-in-chrome__*) script="hooks.py" ;; esac
        fi ;;
    esac ;;
  snapshot) [ "$agent" = "ohmyjob:application-worker" ] && script="hooks.py" ;;
  report) [ -f "$root/.ohmyjob/last_report.txt" ] && script="hooks.py" ;;
esac
[ -n "$script" ] || exit 0

py="${CLAUDE_PLUGIN_DATA:-}/venv/bin/python"  # never `uv run`: this runs on every call
if [ ! -x "$py" ]; then
  # Keep the main session usable, so that /ohmyjob:setup can run; stop the agents.
  message="ohmyjob: environment missing or stale, run /ohmyjob:setup"
  case "$agent:$event" in
    ohmyjob:*:guard|ohmyjob:*:handback) echo "$message" >&2; exit 2 ;;
  esac
  printf '{"systemMessage":"%s"}\n' "$message"
  exit 0
fi

export OHMYJOB_WORKSPACE="$root"  # Python finds the same workspace
exec "$py" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$script" "$event" <<<"$input"
