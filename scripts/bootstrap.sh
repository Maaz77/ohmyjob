#!/usr/bin/env bash
# bootstrap.sh --data <dir> [--workspace <dir>] [--install-uv] [--install-tinytex]
#
# Sets up everything /ohmyjob:setup needs. The skill asks the user first; the flags carry the answers:
#   --install-uv       may download and run the official uv installer when uv is missing
#   --install-tinytex  may download and run the TinyTeX installer when the system lualatex cannot build the sample resume
# Without a flag, a missing piece is an error line ("ohmyjob: ...", exit 3) that names the flag.
#
# Steps: Claude Code version gate, uv, venv (uv sync --frozen --no-dev into <data>/venv), LaTeX warm-up build,
# Chrome extension copy, .ohmyjob/env.sh, workspace .claude/settings.json (merged), scaffold (tracker, folders,
# ohmyjob.json, CLAUDE.md, .gitignore). Idempotent: a second run rewrites only env.sh. Nothing existing is overwritten.
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$PLUGIN_ROOT/scripts"
TEMPLATES="$PLUGIN_ROOT/templates"
MIN_CLAUDE="2.1.306"
# What the Awesome-CV class needs on top of a bare TinyTeX-1: one package per line (the CI job installs the same list)
TEX_PACKAGES_FILE="$SCRIPTS/tex-packages.txt"

fatal() { echo "ohmyjob: $*"; exit 3; }
say() { echo "bootstrap: $*"; }

DATA=""
WORKSPACE="$PWD"
INSTALL_UV=0
INSTALL_TINYTEX=0
while [ $# -gt 0 ]; do
  case "$1" in
    --data) [ $# -ge 2 ] || fatal "--data needs a folder"; DATA="$2"; shift 2 ;;
    --workspace) [ $# -ge 2 ] || fatal "--workspace needs a folder"; WORKSPACE="$2"; shift 2 ;;
    --install-uv) INSTALL_UV=1; shift ;;
    --install-tinytex) INSTALL_TINYTEX=1; shift ;;
    -h|--help) sed -n 2,9p "$0"; exit 0 ;;
    *) fatal "unknown option $1 (usage: bootstrap.sh --data <dir> [--workspace <dir>] [--install-uv] [--install-tinytex])" ;;
  esac
done
[ -n "$DATA" ] || fatal "--data <dir> is required (setup passes \${CLAUDE_PLUGIN_DATA})"
case "$DATA" in /*) ;; *) fatal "--data must be an absolute path" ;; esac
mkdir -p "$DATA" "$WORKSPACE"
DATA="$(cd "$DATA" && pwd)"
WORKSPACE="$(cd "$WORKSPACE" && pwd)"
[ "$WORKSPACE" != "$PLUGIN_ROOT" ] || fatal "the workspace cannot be the plugin folder; start Claude in your job-search folder"
case "$DATA$WORKSPACE" in *\'*) fatal "a folder name with a single quote is not supported" ;; esac

sha256_of() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }

# --- 9. Claude Code version -------------------------------------------------------------------------------------------
version_gate() {
  local out ver
  if ! out="$(claude --version 2>/dev/null)"; then
    say "warning: could not run 'claude --version'; make sure Claude Code is $MIN_CLAUDE or newer"
    return 0
  fi
  ver="$(printf '%s' "$out" | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
  [ -n "$ver" ] || { say "warning: unreadable Claude Code version '$out'"; return 0; }
  if [ "$(printf '%s\n%s\n' "$MIN_CLAUDE" "$ver" | sort -V | head -n1)" != "$MIN_CLAUDE" ]; then
    fatal "Claude Code $ver is too old, ohmyjob needs $MIN_CLAUDE or newer (run: claude update)"
  fi
  say "Claude Code $ver"
}
version_gate

# --- 2. uv ------------------------------------------------------------------------------------------------------------
UV=""
for cand in "$(command -v uv 2>/dev/null || true)" "$DATA/bin/uv" "$HOME/.local/bin/uv"; do
  if [ -n "$cand" ] && [ -x "$cand" ]; then UV="$cand"; break; fi
done
if [ -z "$UV" ]; then
  [ "$INSTALL_UV" = 1 ] || fatal "uv is not installed. Ask the user, then run bootstrap again with --install-uv"
  say "installing uv into $DATA/bin"
  mkdir -p "$DATA/bin"
  curl -LsSf https://astral.sh/uv/install.sh | UV_UNMANAGED_INSTALL="$DATA/bin" sh >&2 \
    || fatal "the uv installer failed"
  UV="$DATA/bin/uv"
  [ -x "$UV" ] || fatal "the uv installer did not create $UV"
fi
# env.sh keeps the basename uv: resolve to an absolute path but never through a symlink name
case "$UV" in /*) ;; *) UV="$(command -v "$UV")" ;; esac
say "uv: $UV"

# --- 3. venv ----------------------------------------------------------------------------------------------------------
VENV="$DATA/venv"
PY="$VENV/bin/python"
want="$(sha256_of "$PLUGIN_ROOT/uv.lock")"
if [ -x "$PY" ] && [ "$(cat "$VENV/uv.lock.sha256" 2>/dev/null || true)" = "$want" ]; then
  say "venv is current"
else
  say "uv sync into $VENV"
  UV_PROJECT_ENVIRONMENT="$VENV" "$UV" sync --frozen --no-dev --quiet --project "$PLUGIN_ROOT" >&2 \
    || fatal "uv sync failed"
  [ -x "$PY" ] || fatal "uv sync did not create $PY"
  echo "$want" > "$VENV/uv.lock.sha256"
fi

# --- workspace marker first: the warm-up build and the tracker need a workspace --------------------------------------
mkdir -p "$WORKSPACE/.ohmyjob"
if [ ! -e "$WORKSPACE/ohmyjob.json" ]; then
  cp "$TEMPLATES/ohmyjob.json" "$WORKSPACE/ohmyjob.json"
  say "created ohmyjob.json"
fi

# --- 4. LaTeX ---------------------------------------------------------------------------------------------------------
# warmup <lualatex path or empty>: builds the sample resume (templates/base.yaml + role.yaml) in a throwaway
# workspace. The build also fills the luaotfload font cache, so parallel builds start warm.
warmup() {
  local lua="$1" tmp out rc
  tmp="$(mktemp -d "$WORKSPACE/.ohmyjob/w.XXXXXX")" || return 1
  mkdir -p "$tmp/Default_Resume_Files"
  cp "$TEMPLATES/ohmyjob.json" "$tmp/ohmyjob.json"
  cp "$TEMPLATES/base.yaml" "$tmp/Default_Resume_Files/base.yaml"
  cp "$TEMPLATES/role.yaml" "$tmp/Default_Resume_Files/Warmup.yaml"
  rc=0
  out="$(OHMYJOB_WORKSPACE="$tmp" OHMYJOB_LUALATEX="$lua" "$PY" "$SCRIPTS/resume_build.py" build \
         "$tmp/Default_Resume_Files/Warmup.yaml" --out "$tmp/warmup.pdf" 2>&1)" || rc=$?
  rm -rf "$tmp"
  case "$out" in "OK "*) return 0 ;; esac
  say "warm-up build with ${lua:-lualatex}: $(printf '%s' "$out" | tail -n 3 | tr '\n' ' ')"
  return 1
}

# the lualatex inside <data>/tinytex (macOS: TinyTeX/, Linux: .TinyTeX/), if installed
tinytex_bin() {
  local d f
  for d in "$DATA/tinytex/.TinyTeX/bin" "$DATA/tinytex/TinyTeX/bin"; do
    for f in "$d"/*/lualatex; do
      if [ -x "$f" ]; then dirname "$f"; return 0; fi
    done
  done
  return 1
}

LUALATEX=""
system_lua="$(command -v lualatex 2>/dev/null || true)"
if [ -n "$system_lua" ] && warmup ""; then
  LUALATEX="$system_lua"
  say "LaTeX: system lualatex works ($LUALATEX)"
else
  bin=""
  bin="$(tinytex_bin || true)"
  if [ -n "$bin" ] && warmup "$bin/lualatex"; then
    LUALATEX="$bin/lualatex"
    say "LaTeX: TinyTeX in $DATA/tinytex works"
  else
    [ "$INSTALL_TINYTEX" = 1 ] || fatal "no working lualatex for the resume. Ask the user, then run bootstrap again with --install-tinytex (installs TinyTeX into $DATA/tinytex, no admin rights)"
    if [ -z "$bin" ]; then
      # the installer uses $TINYTEX_DIR unquoted (rm -rf $TEXDIR)
      case "$DATA" in *[[:space:]\'\"\\\$\`]*) fatal "the TinyTeX installer cannot use a data folder whose path has a space or quote ($DATA); install TeX Live with lualatex yourself, or use a plugin data folder without spaces" ;; esac
      say "installing TinyTeX into $DATA/tinytex"
      mkdir -p "$DATA/tinytex"
      curl -fsSL https://yihui.org/tinytex/install-bin-unix.sh \
        | TINYTEX_DIR="$DATA/tinytex" sh -s - "" --no-path >&2 || fatal "the TinyTeX installer failed"
      bin="$(tinytex_bin || true)"
      [ -n "$bin" ] || fatal "TinyTeX was installed but no lualatex was found under $DATA/tinytex"
    fi
    # tlmgr of OUR TinyTeX only (never a system tree)
    case "$bin" in "$DATA"/tinytex/*) ;; *) fatal "refusing to run tlmgr outside $DATA/tinytex" ;; esac
    [ -x "$bin/tlmgr" ] || fatal "no tlmgr in $bin"
    pkgs="$(grep -v '^[[:space:]]*\(#\|$\)' "$TEX_PACKAGES_FILE" | tr -d '\r' | tr '\n' ' ')"
    [ -n "$pkgs" ] || fatal "$TEX_PACKAGES_FILE is empty"
    say "tlmgr install $pkgs"
    # shellcheck disable=SC2086
    PATH="$bin:$PATH" "$bin/tlmgr" install $pkgs >&2 || fatal "tlmgr install failed"
    warmup "$bin/lualatex" || fatal "the sample resume does not build with TinyTeX either; see the warm-up line above"
    LUALATEX="$bin/lualatex"
    say "LaTeX: TinyTeX ready ($LUALATEX)"
  fi
fi

# --- 5. Chrome extension ----------------------------------------------------------------------------------------------
EXT="$DATA/chrome-extension"
if [ -d "$EXT" ] && diff -rq "$PLUGIN_ROOT/chrome-extension" "$EXT" >/dev/null 2>&1; then
  say "extension is current"
else
  rm -rf "$EXT.new"
  cp -R "$PLUGIN_ROOT/chrome-extension" "$EXT.new"
  rm -rf "$EXT"
  mv "$EXT.new" "$EXT"
  say "copied the Chrome extension"
fi

# --- 6. env.sh (rewritten on every run) -------------------------------------------------------------------------------
ENV_FILE="$WORKSPACE/.ohmyjob/env.sh"
for value in "$PY" "$UV" "$DATA" "$LUALATEX"; do
  case "$value" in
    *\'*|*$'\n'*|*$'\r'*) fatal "a path with a quote or a line break cannot go into env.sh: $value" ;;
  esac
done
{
  echo "# Written by /ohmyjob:setup on every run. Parsed by bin/ohmyjob, never sourced. Do not edit."
  echo "OHMYJOB_PY='$PY'"
  echo "OHMYJOB_UV='$UV'"
  echo "OHMYJOB_DATA='$DATA'"
  echo "OHMYJOB_LUALATEX='$LUALATEX'"
} > "$ENV_FILE.tmp"
mv "$ENV_FILE.tmp" "$ENV_FILE"
say "wrote $ENV_FILE"

# --- 7. workspace .claude/settings.json (merge) -----------------------------------------------------------------------
mkdir -p "$WORKSPACE/.claude"
"$PY" -I - "$TEMPLATES/workspace-settings.json" "$WORKSPACE/.claude/settings.json" "$WORKSPACE/ohmyjob.json" <<'PYEOF' \
  || fatal "could not merge .claude/settings.json (the file is left as it was)"
import json, sys
from pathlib import Path

template = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = Path(sys.argv[2])
try:
    workers = int(json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")).get("pipeline", {}).get("max_parallel_workers", 5))
except (OSError, ValueError, AttributeError):
    workers = 5
cap = str(max(20, workers + 1))

if target.exists():
    existing = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise SystemExit("settings.json is not a JSON object")
else:
    existing = {}


def merge(mine, tmpl):
    """The user's values win; lists gain the template's missing entries; dicts merge key by key."""
    for key, value in tmpl.items():
        if key not in mine:
            mine[key] = value
        elif isinstance(mine[key], dict) and isinstance(value, dict):
            merge(mine[key], value)
        elif isinstance(mine[key], list) and isinstance(value, list):
            mine[key] += [item for item in value if item not in mine[key]]
    return mine


merged = merge(json.loads(json.dumps(existing)), template)
env = merged.setdefault("env", {})
# a number: raise it when too low, keep a higher one the user chose
for name, floor in (("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", int(cap)), ("MAX_MCP_OUTPUT_TOKENS", 60000)):
    try:
        current = int(str(env.get(name, "")).strip())
    except ValueError:
        current = 0
    env[name] = str(max(current, floor))
if merged != existing or not target.exists():
    target.write_text(json.dumps(merged, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("bootstrap: updated .claude/settings.json")
else:
    print("bootstrap: .claude/settings.json is current")
PYEOF

# --- 8. scaffold (never overwrite) ------------------------------------------------------------------------------------
for d in Default_Resume_Files Applications Pending-Review Needs-Attention Submitted; do
  mkdir -p "$WORKSPACE/$d"
done
if [ ! -e "$WORKSPACE/Job_Tracker.xlsx" ]; then
  OHMYJOB_WORKSPACE="$WORKSPACE" "$PY" -I -c '
import sys
sys.path.insert(0, sys.argv[1])
import tracker
tracker.create(sys.argv[2])' "$SCRIPTS" "$WORKSPACE/Job_Tracker.xlsx" || fatal "could not create Job_Tracker.xlsx"
  say "created Job_Tracker.xlsx"
fi
if [ ! -e "$WORKSPACE/CLAUDE.md" ]; then
  cp "$TEMPLATES/CLAUDE.md" "$WORKSPACE/CLAUDE.md"
  say "created CLAUDE.md"
fi
if [ ! -e "$WORKSPACE/.gitignore" ]; then
  cp "$TEMPLATES/gitignore" "$WORKSPACE/.gitignore"
  say "created .gitignore"
fi

echo "extension: $EXT"
echo "lualatex: $LUALATEX"
echo "bootstrap: OK"
