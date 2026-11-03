#!/usr/bin/env python3
"""Hook entry for /ohmyjob:prep-jobs (called by hook.sh, which passes the hook JSON on stdin and the event as argv[1]).

  pj_hooks.py guard      PreToolUse    Read, Edit, Write, Bash, Agent, Chrome   each prep agent stays inside its own job
  pj_hooks.py handback   PreToolUse    SubagentHandback                         checks and saves a worker report
  pj_hooks.py capture    SubagentStop  screen-worker, resume-worker             the same, when the report is final text

hook.sh picks this script by a text match, which is only a prefilter. The decision rests on the PARSED top-level
agent_type, tool_name and cwd (never on text in tool_input): a call whose parsed agent is not a prep agent runs the
rules of hooks.py instead. The guard has onFailure "block", so only the prep agents import the heavy modules.
"""
import io
import json
import os
import sys
from pathlib import Path
from typing import Optional

PREP_AGENTS = {"ohmyjob:prep-master", "ohmyjob:screen-worker", "ohmyjob:resume-worker"}
WORKERS = {"ohmyjob:screen-worker", "ohmyjob:resume-worker"}


def workspace_for(inp: dict) -> Optional[Path]:
    """The nearest folder with ohmyjob.json at or above the parsed cwd, else $CLAUDE_PROJECT_DIR, else the folder
    hook.sh exported."""
    for start in (inp.get("cwd"), os.environ.get("CLAUDE_PROJECT_DIR"), os.environ.get("OHMYJOB_WORKSPACE")):
        if isinstance(start, str) and os.path.isabs(start):
            here = Path(start)
            for folder in (here, *here.parents):
                if (folder / "ohmyjob.json").is_file():
                    return folder
    return None


def delegate(raw: str, command: str) -> None:
    """Hand the same input to hooks.py (the rules for every caller that is not a prep agent)."""
    import hooks

    sys.argv = [str(Path(hooks.__file__)), command]
    sys.stdin = io.StringIO(raw)
    hooks.main()
    sys.exit(0)


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = sys.stdin.read()
    try:
        inp = json.loads(raw or "{}")
    except json.JSONDecodeError:
        inp = {}
    if not isinstance(inp, dict):
        inp = {}
    root = workspace_for(inp)
    if root is None:
        sys.exit(0)
    os.environ["OHMYJOB_WORKSPACE"] = str(root)
    if "workspace" in sys.modules:  # imported earlier (by hooks.py) for another candidate folder
        import importlib

        importlib.reload(sys.modules["workspace"])
    agent = inp.get("agent_type")
    if agent not in PREP_AGENTS:
        delegate(raw, command)
    if command not in ("guard", "handback", "capture") or (command != "guard" and agent not in WORKERS):
        sys.exit(0)
    import pj  # the heavy imports, only for the prep agents

    {"guard": pj.hook_guard, "handback": pj.hook_handback, "capture": pj.hook_capture}[command](inp)
    sys.exit(0)


if __name__ == "__main__":
    main()
