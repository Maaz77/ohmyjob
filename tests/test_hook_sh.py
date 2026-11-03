"""scripts/hook.sh: the one entry of every hook. It must end calls that no ohmyjob rule covers without starting
Python, route the rest by agent_type, and keep the main session usable when the venv is missing."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHROME = "mcp__claude-in-chrome__computer"
NOT_SET_UP = "ohmyjob: environment missing or stale, run /ohmyjob:setup"

# Stands in for pj_hooks.py and hooks.py: reports how it was called.
STUB_SCRIPT = (
    "import json, os, sys\n"
    "print(json.dumps({'script': os.path.basename(__file__), 'argv': sys.argv[1:], 'stdin': sys.stdin.read(),\n"
    "                  'workspace': os.environ.get('OHMYJOB_WORKSPACE')}))\n"
    "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n"
)


@pytest.fixture
def plugin(tmp_path):
    """A copy of hook.sh with stub scripts next to it, like <plugin>/scripts/."""
    scripts = tmp_path / "plugin" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "hook.sh", scripts / "hook.sh")
    for name in ("pj_hooks.py", "hooks.py"):
        (scripts / name).write_text(STUB_SCRIPT)
    return scripts / "hook.sh"


@pytest.fixture
def data(tmp_path):
    """A CLAUDE_PLUGIN_DATA whose venv python is the test interpreter."""
    python = tmp_path / "data" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return tmp_path / "data"


def call(hook, event, payload, project, data=None, **env_extra):
    env = {"PATH": "/usr/bin:/bin", **env_extra}
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    if data is not None:
        env["CLAUDE_PLUGIN_DATA"] = str(data)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(["bash", str(hook), event], input=stdin, capture_output=True, text=True, env=env)


def hook_input(agent=None, tool="Bash", **tool_input):
    payload = {"session_id": "s1", "cwd": "/w", "hook_event_name": "PreToolUse", "tool_name": tool,
               "tool_input": tool_input or {"command": "ls"}}
    if agent is not None:
        payload = {"agent_id": "a1", "agent_type": agent, **payload}
    return payload


def routed(proc):
    """The stub's report, or None when hook.sh did not start Python."""
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


# --------------------------------------------------------------------------- no-ops


@pytest.mark.parametrize("event", ["guard", "handback", "snapshot", "report", "capture"])
def test_without_the_marker_it_does_nothing(plugin, data, tmp_path, event):
    (tmp_path / "plain").mkdir()
    for project in (tmp_path / "plain", tmp_path / "does-not-exist", None):
        proc = call(plugin, event, hook_input("ohmyjob:prep-master"), project, data)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


@pytest.mark.parametrize("agent", ["general-purpose", "Explore", "other-plugin:worker", "ohmyjob", "ohmyjob:setup-helper"])
@pytest.mark.parametrize("event", ["guard", "handback", "capture", "snapshot"])
def test_an_agent_that_is_not_an_ohmyjob_agent_is_left_alone(plugin, workspace, tmp_path, agent, event):
    for data_dir in (None, tmp_path / "no-venv"):  # with or without a venv: no message either way
        proc = call(plugin, event, hook_input(agent, tool=CHROME), workspace, data_dir)
        assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


@pytest.mark.parametrize("tool", ["Read", "Edit", "Write", "Bash", "Agent", "Skill"])
def test_the_main_session_is_left_alone_except_for_chrome_tools(plugin, workspace, data, tool):
    assert routed(call(plugin, "guard", hook_input(None, tool=tool), workspace, data)) is None
    proc = call(plugin, "guard", hook_input(None, tool=tool), workspace, None)  # no venv: still silent
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


# --------------------------------------------------------------------------- routing


@pytest.mark.parametrize("event, agent, tool, script", [
    ("guard", "ohmyjob:prep-master", "Bash", "pj_hooks.py"),
    ("guard", "ohmyjob:screen-worker", "Read", "pj_hooks.py"),
    ("guard", "ohmyjob:resume-worker", "Edit", "pj_hooks.py"),
    ("guard", "ohmyjob:application-master", "Bash", "hooks.py"),
    ("guard", "ohmyjob:application-worker", CHROME, "hooks.py"),
    ("guard", None, CHROME, "hooks.py"),
    ("guard", None, "mcp__claude-in-chrome__javascript_tool", "hooks.py"),
    ("handback", "ohmyjob:screen-worker", "SubagentHandback", "pj_hooks.py"),
    ("handback", "ohmyjob:resume-worker", "SubagentHandback", "pj_hooks.py"),
    ("handback", "ohmyjob:application-worker", "SubagentHandback", "hooks.py"),
    ("capture", "ohmyjob:resume-worker", "", "pj_hooks.py"),
    ("capture", "ohmyjob:application-worker", "", "hooks.py"),
    ("snapshot", "ohmyjob:application-worker", CHROME, "hooks.py"),
])
def test_a_call_is_routed_by_agent_type(plugin, workspace, data, event, agent, tool, script):
    payload = hook_input(agent, tool=tool)
    report = routed(call(plugin, event, payload, workspace, data))
    assert report["script"] == script
    assert report["argv"] == [event]
    assert json.loads(report["stdin"]) == payload  # Python gets the JSON that bash read


@pytest.mark.parametrize("event, agent, tool", [
    ("handback", None, "SubagentHandback"),
    ("capture", None, ""),
    ("snapshot", None, CHROME),
    ("snapshot", "ohmyjob:screen-worker", CHROME),
    ("snapshot", "ohmyjob:application-master", CHROME),
    ("bogus", "ohmyjob:application-worker", CHROME),
    ("", "ohmyjob:application-worker", CHROME),
])
def test_other_calls_do_not_start_python(plugin, workspace, data, event, agent, tool):
    assert routed(call(plugin, event, hook_input(agent, tool=tool), workspace, data)) is None


ALL_AGENTS = [
    None, "ohmyjob:prep-master", "ohmyjob:screen-worker", "ohmyjob:resume-worker", "ohmyjob:application-master",
    "ohmyjob:application-worker", "Explore", "other:thing",
]


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_capture_is_routed_exactly_like_handback(plugin, workspace, data, agent):
    """Workers report through SubagentHandback only in auto mode; in Manual and -p mode the report is the last message
    that SubagentStop carries. Both reach the same script."""
    handback = routed(call(plugin, "handback", hook_input(agent, tool="SubagentHandback"), workspace, data))
    capture = routed(call(plugin, "capture", hook_input(agent, tool=""), workspace, data))
    assert (handback and handback["script"]) == (capture and capture["script"])
    assert (capture is None) == (agent not in ALL_AGENTS[1:6])


@pytest.mark.parametrize("project", ["/", "//", "relative", ".", "a/b", "/nonexistent/deep/folder/"])
def test_a_project_dir_outside_any_workspace_ends_quietly(plugin, data, project):
    proc = call(plugin, "guard", hook_input("ohmyjob:prep-master"), project, data)
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")


def test_report_starts_python_only_when_a_report_is_waiting(plugin, workspace, data):
    assert routed(call(plugin, "report", {"hook_event_name": "Stop"}, workspace, data)) is None
    (workspace / ".ohmyjob").mkdir()
    (workspace / ".ohmyjob" / "last_report.txt").write_text("table")
    report = routed(call(plugin, "report", {"hook_event_name": "Stop"}, workspace, data))
    assert (report["script"], report["argv"]) == ("hooks.py", ["report"])


def test_claude_started_in_a_subfolder_of_the_workspace_still_finds_it(plugin, workspace, data):
    inner = workspace / "Applications" / "4399000001_Lumenfield-Logistics_Operations-Analyst"
    report = routed(call(plugin, "guard", hook_input("ohmyjob:application-worker", tool=CHROME), inner, data))
    assert (report["script"], report["workspace"]) == ("hooks.py", str(workspace))
    (workspace / ".ohmyjob").mkdir()
    (workspace / ".ohmyjob" / "last_report.txt").write_text("table")
    assert routed(call(plugin, "report", {"hook_event_name": "Stop"}, inner, data))["script"] == "hooks.py"
    proc = call(plugin, "guard", hook_input("ohmyjob:prep-master"), inner, None)  # and it is denied without a venv
    assert proc.returncode == 2


def test_the_workspace_found_by_bash_is_the_one_python_gets(plugin, workspace, data):
    report = routed(call(plugin, "guard", hook_input("ohmyjob:prep-master"), workspace, data, OHMYJOB_WORKSPACE="/stale"))
    assert report["workspace"] == str(workspace)


def test_the_exit_code_of_python_is_the_exit_code_of_the_hook(plugin, workspace, data):
    proc = call(plugin, "guard", hook_input("ohmyjob:prep-master"), workspace, data, STUB_EXIT="2")
    assert proc.returncode == 2


def test_formatting_of_the_json_does_not_matter(plugin, workspace, data):
    pretty = json.dumps(hook_input("ohmyjob:resume-worker"), indent=4).replace('"agent_type":', '"agent_type"  :\n  ')
    assert routed(call(plugin, "guard", pretty, workspace, data))["script"] == "pj_hooks.py"


def test_agent_type_or_tool_name_inside_a_string_value_cannot_steer_the_routing(plugin, workspace, data):
    forged = hook_input(None, tool="Read", file_path="x", note='"agent_type":"ohmyjob:prep-master"')
    assert routed(call(plugin, "guard", forged, workspace, data)) is None
    forged = hook_input(None, tool="Bash", command=f'echo "tool_name":"{CHROME}"')
    assert routed(call(plugin, "guard", forged, workspace, data)) is None


def test_a_big_page_snapshot_arrives_whole(plugin, workspace, data):
    payload = hook_input("ohmyjob:application-worker", tool=CHROME)
    payload["tool_response"] = "line of page text\n" * 30000  # about 500 KB
    report = routed(call(plugin, "snapshot", payload, workspace, data))
    assert json.loads(report["stdin"]) == payload


# --------------------------------------------------------------------------- no venv


@pytest.mark.parametrize("agent", [
    "ohmyjob:prep-master", "ohmyjob:screen-worker", "ohmyjob:resume-worker",
    "ohmyjob:application-master", "ohmyjob:application-worker",
])
@pytest.mark.parametrize("data_dir", ["unset", "empty"])
def test_without_a_venv_an_ohmyjob_agent_is_denied(plugin, workspace, tmp_path, agent, data_dir):
    data = None if data_dir == "unset" else tmp_path / "empty-data"
    for event in ("guard", "handback"):
        proc = call(plugin, event, hook_input(agent), workspace, data)
        assert proc.returncode == 2
        assert proc.stderr.strip() == NOT_SET_UP
        assert proc.stdout == ""


def test_without_a_venv_the_main_session_gets_a_message_for_chrome_calls_and_is_not_blocked(plugin, workspace):
    proc = call(plugin, "guard", hook_input(None, tool=CHROME), workspace, None)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"systemMessage": NOT_SET_UP}


@pytest.mark.parametrize("event, agent", [("snapshot", "ohmyjob:application-worker"), ("capture", "ohmyjob:resume-worker")])
def test_without_a_venv_the_other_events_only_warn(plugin, workspace, event, agent):
    proc = call(plugin, event, hook_input(agent, tool=CHROME), workspace, None)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"systemMessage": NOT_SET_UP}


def test_a_venv_python_that_is_not_executable_counts_as_missing(plugin, workspace, data):
    (data / "venv" / "bin" / "python").chmod(0o644)
    assert call(plugin, "guard", hook_input("ohmyjob:prep-master"), workspace, data).returncode == 2


# --------------------------------------------------------------------------- the shipped files


@pytest.mark.parametrize("path", ["bin/ohmyjob", "scripts/hook.sh", "scripts/pii_gate.sh"])
def test_entry_points_are_executable(path):
    assert os.access(REPO / path, os.X_OK)


def test_hooks_json_runs_hook_sh_for_each_event():
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text())["hooks"]
    assert set(hooks) == {"PreToolUse", "PostToolUse", "Stop", "SubagentStop"}
    events = []
    for groups in hooks.values():
        for group in groups:
            for hook in group["hooks"]:
                assert hook["command"] == "bash"
                assert hook["args"][0] == "${CLAUDE_PLUGIN_ROOT}/scripts/hook.sh"
                events.append(hook["args"][1])
    assert sorted(events) == ["capture", "guard", "handback", "report", "snapshot"]
    guard = hooks["PreToolUse"][0]
    assert guard["hooks"][0]["onFailure"] == "block"
    for tool in (CHROME, "Read", "Edit", "Write", "Bash", "Agent"):
        assert re.search(guard["matcher"], tool), tool
    for tool in ("Grep", "Skill", "ToolSearch", "SubagentHandback"):
        assert not re.search(guard["matcher"], tool), tool
    stop = hooks["SubagentStop"][0]["matcher"]
    for agent in ("ohmyjob:application-worker", "ohmyjob:screen-worker", "ohmyjob:resume-worker"):
        assert re.search(stop, agent)
    for agent in ("ohmyjob:application-master", "ohmyjob:prep-master", "application-worker", "x-ohmyjob:resume-worker"):
        assert not re.search(stop, agent)
