#!/usr/bin/env bash
# One entry for every ohmyjob hook: hook.sh <guard|handback|snapshot|report|capture>, hook JSON on stdin.
# Hooks fire on every Read/Edit/Bash/Chrome call of every session, so a call that no ohmyjob rule covers must end
# here, in bash, without starting Python. The rest goes to the plugin's venv Python:
#   ohmyjob:prep-master|screen-worker|resume-worker -> pj_hooks.py
#   ohmyjob:application-*                           -> hooks.py
#   guard of ANY Chrome tool (mcp__claude-in-chrome__*), whoever calls (main session, Explore, any agent) -> hooks.py
#     (decided first; hooks.py hands a PARSED prep agent_type to pj_hooks.py's rules)
#   anything else                                   -> exit 0
# handback (SubagentHandback, auto mode only) and capture (SubagentStop, where a worker's last message is its report
# in Manual and -p mode) are routed like guard for ohmyjob agents, never assuming that handback fires.
#
# The text tests below are a PREFILTER only. A model controls tool_input and can put any text in it, so bash never
# decides a rule from text: it collects EVERY match (not the first) of agent_type, tool_name and cwd, and a false match
# costs one Python start, never a different decision. The rules come from the parsed top-level JSON in Python.
set -u

event="${1:-}"
input="$(cat)"  # stdin can be read once; the Python script gets it again below

# Every value of a plain `"key": "value"` in the JSON text. A copy inside a string value is escaped (\"key\"), so it
# cannot match; a copy inside a nested object can, which is why all matches are kept.
find_all() {  # find_all <key> -> fills the array `found`
  local text="$input" re="\"$1\"[[:space:]]*:[[:space:]]*\"([^\"]*)\""
  found=()
  while [[ $text =~ $re ]]; do
    found+=("${BASH_REMATCH[1]}")
    text="${text#*"${BASH_REMATCH[0]}"}"
  done
}

# The workspace is the nearest folder with ohmyjob.json at or above CLAUDE_PROJECT_DIR (the folder Claude was started
# in, a subfolder of the workspace at most), else at or above the stdin cwd (Claude may have been started elsewhere and
# then moved in; CLAUDE_PROJECT_DIR is not set for every caller), up to /. None: not an ohmyjob workspace, do nothing.
# No forks: this runs on every call. (A path that is not absolute is no workspace.)
find_workspace() {  # find_workspace <dir> -> sets `root`
  root="$1"
  while [ -n "$root" ] && [ ! -f "$root/ohmyjob.json" ]; do
    case "$root" in /) root="" ;; /*/*) root="${root%/*}" ;; /*) root="/" ;; *) root="" ;; esac
  done
}
find_workspace "${CLAUDE_PROJECT_DIR:-}"
if [ -z "$root" ]; then
  find_all cwd
  for dir in ${found[@]+"${found[@]}"}; do
    find_workspace "$dir"
    [ -z "$root" ] || break
  done
fi
[ -n "$root" ] || exit 0

prep=0 app=0 ohmyjob_agent=0 any_agent=0 chrome=0 app_worker=0
find_all agent_type
for agent in ${found[@]+"${found[@]}"}; do
  [ -n "$agent" ] && any_agent=1
  case "$agent" in
    ohmyjob:prep-master|ohmyjob:screen-worker|ohmyjob:resume-worker) prep=1 ohmyjob_agent=1 ;;
    ohmyjob:application-worker) app=1 app_worker=1 ohmyjob_agent=1 ;;
    ohmyjob:application-*) app=1 ohmyjob_agent=1 ;;
  esac
done
find_all tool_name
for tool in ${found[@]+"${found[@]}"}; do
  case "$tool" in mcp__claude-in-chrome__*) chrome=1 ;; esac
done
if [ "$event" = "guard" ] && [ ${#found[@]} -eq 0 ]; then
  # Inside a workspace but no readable tool_name (empty stdin, a path with a quote or backslash, other format): the
  # guard cannot tell a Chrome call, so it does not let the call through.
  echo "ohmyjob: the hook input has no readable tool_name, the call is blocked" >&2
  exit 2
fi

script=""
case "$event" in
  guard|handback|capture)
    if [ "$event" = "guard" ] && [ "$chrome" = 1 ]; then script="hooks.py"  # first: no agent_type text can move it
    elif [ "$prep" = 1 ]; then script="pj_hooks.py"
    elif [ "$app" = 1 ]; then script="hooks.py"
    fi ;;
  snapshot) [ "$app_worker" = 1 ] && script="hooks.py" ;;
  report) [ -f "$root/.ohmyjob/last_report.txt" ] && script="hooks.py" ;;
esac
[ -n "$script" ] || exit 0

py="${CLAUDE_PLUGIN_DATA:+$CLAUDE_PLUGIN_DATA/venv/bin/python}"  # never `uv run`: this runs on every call
if [ -z "$py" ] || [ ! -x "$py" ]; then
  # Keep the main session usable, so that /ohmyjob:setup can run; stop every agent (an agent's Chrome call too).
  message="ohmyjob: environment missing or stale, run /ohmyjob:setup"
  if [ "$event" = "guard" ] || [ "$event" = "handback" ]; then
    if [ "$ohmyjob_agent" = 1 ] || { [ "$event" = "guard" ] && [ "$any_agent" = 1 ]; }; then
      echo "$message" >&2; exit 2
    fi
  fi
  printf '{"systemMessage":"%s"}\n' "$message"
  exit 0
fi

export OHMYJOB_WORKSPACE="$root"  # a candidate: Python re-derives the workspace from the parsed top-level cwd
exec "$py" "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$script" "$event" <<<"$input"
