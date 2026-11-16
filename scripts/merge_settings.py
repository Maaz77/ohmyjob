import json, sys
from pathlib import Path

template = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
target = Path(sys.argv[2])
cap = "50"  # the subagent cap; W.slots() is this minus one

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
