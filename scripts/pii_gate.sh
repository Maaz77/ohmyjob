#!/usr/bin/env bash
# pii_gate.sh <terms-file>: search the whole repo, binaries included, for personal terms.
# The terms file holds one regex per line (the owner's names, handles, address, employers, ...). It stays outside
# the repo: never commit it. Prints every hit; exit 1 when there are hits, 0 when clean, 2 when the gate cannot run.
# Searched: file contents and file names. Skipped: the root's .git, .venv and tmp/ (private handoff notes, gitignored; a
# nested tmp/ is searched) and the caches (__pycache__, .pytest_cache, *.pyc, .DS_Store), which hold absolute home paths
# and are never published. Nothing else is skipped, whatever .gitignore says. Needs ripgrep: without rg it exits 2.
set -u

terms="${1:-}"
if [ -z "$terms" ] || [ ! -f "$terms" ]; then
  echo "usage: pii_gate.sh <terms-file>" >&2
  exit 2
fi
cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 2

patterns="$(mktemp)"
trap 'rm -f "$patterns"' EXIT
grep -v '^[[:space:]]*$' "$terms" > "$patterns"  # a blank line would match every line
[ -s "$patterns" ] || { echo "pii_gate.sh: $terms holds no terms" >&2; exit 2; }

command -v rg >/dev/null 2>&1 || { echo "pii_gate.sh: ripgrep (rg) is required, install it first" >&2; exit 2; }
excludes=(-g '!/.git' -g '!/.venv/' -g '!/tmp/' -g '!__pycache__' -g '!.pytest_cache' -g '!*.pyc' -g '!.DS_Store')

# File names count too: a file called <Owner>_Resume.yaml is a leak whatever it holds. Same regex dialect (rg's) as the
# content search; a failure of either step ends the gate with 2, never with a pass.
files="$(rg --files --hidden --no-ignore "${excludes[@]}" . 2>&1)" || { echo "pii_gate.sh: listing the files failed: $files" >&2; exit 2; }
name_hits="$(printf '%s\n' "$files" | rg -i -f "$patterns" 2>&1)"
name_status=$?
[ "$name_status" -le 1 ] || { echo "pii_gate.sh: matching the file names failed: $name_hits" >&2; exit 2; }
hits="$(rg -a -i -n --hidden --no-ignore "${excludes[@]}" -f "$patterns" . 2>&1)"
status=$?

if [ "$status" -eq 0 ] || [ "$name_status" -eq 0 ]; then
  [ "$name_status" -ne 0 ] || echo "path: ${name_hits//$'\n'/$'\n'path: }"
  [ "$status" -ne 0 ] || echo "$hits"
  exit 1
elif [ "$status" -eq 1 ]; then
  exit 0
fi
echo "pii_gate.sh: the search failed: $hits" >&2
exit 2
