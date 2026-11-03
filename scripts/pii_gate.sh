#!/usr/bin/env bash
# pii_gate.sh <terms-file>: search the whole repo, binaries included, for personal terms.
# The terms file holds one regex per line (the owner's names, handles, address, employers, ...). It stays outside
# the repo: never commit it. Prints every hit; exit 1 when there are hits, 0 when clean, 2 when the gate cannot run.
# Skipped: .git, .venv, tmp/ (private handoff notes, gitignored) and the caches (__pycache__, .pytest_cache, *.pyc, .DS_Store), which hold absolute home paths
# and are never published. Nothing else is skipped, whatever .gitignore says; both search tools use this one list.
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

if command -v rg >/dev/null 2>&1; then
  hits="$(rg -a -i -n --hidden --no-ignore -g '!.git' -g '!.venv' -g '!tmp' -g '!__pycache__' -g '!.pytest_cache' \
    -g '!*.pyc' -g '!.DS_Store' -f "$patterns" . 2>&1)"
  status=$?
else
  hits="$(grep -rEian -f "$patterns" --exclude-dir=.git --exclude-dir=.venv --exclude-dir=tmp --exclude-dir=__pycache__ \
    --exclude-dir=.pytest_cache --exclude='*.pyc' --exclude=.DS_Store . 2>&1)"
  status=$?
fi

if [ "$status" -eq 0 ]; then
  echo "$hits"
  exit 1
elif [ "$status" -eq 1 ]; then
  exit 0
fi
echo "pii_gate.sh: the search failed: $hits" >&2
exit 2
