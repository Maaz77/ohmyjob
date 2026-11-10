"""The master's tabs_context_mcp result, from hook.sh (PostToolUse snapshot) to the file that `aa.py preflight` reads.

Live failure: `ohmyjob apply preflight --tab <id>` printed "RETRY call tabs_context_mcp alone ..." three times although the
master had called tabs_context_mcp alone each time (home tab on linkedin.com/robots.txt#aa-popups-as-tabs, the helper
extension loaded, a queued job that is not a LinkedIn job). The hook runs through the real scripts/hook.sh, in the v2
layout (CLAUDE_PROJECT_DIR is the project folder; the workspace is its ohmyjob/ subfolder).
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import test_aa as T
from test_aa import C, M, REPO

HOOK_SH = REPO / "scripts" / "hook.sh"


class TestMasterTabsThroughHookSh(T.Base):
    jobs = (T.INDEED_JOB,)  # the one queued job is not a LinkedIn job: the home tab shows robots.txt

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        project = cls.root.parent / "project"
        project.mkdir()
        moved = project / "ohmyjob"
        shutil.move(str(cls.root), str(moved))
        cls.root = moved
        cls.project = project
        cls.env = {**cls.env, "OHMYJOB_WORKSPACE": str(moved)}
        data = cls.root.parent.parent / "data"
        python = data / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        cls.data = data

    def hook_sh(self, event: str, payload: dict) -> subprocess.CompletedProcess:
        """What Claude Code does: no OHMYJOB_WORKSPACE, CLAUDE_PROJECT_DIR and CLAUDE_PLUGIN_DATA set."""
        env = {k: v for k, v in self.env.items() if k != "OHMYJOB_WORKSPACE"}
        env.update(CLAUDE_PROJECT_DIR=str(self.project), CLAUDE_PLUGIN_DATA=str(self.data))
        payload = {"session_id": "s1", "cwd": str(self.project), **payload}
        return subprocess.run(["bash", str(HOOK_SH), event], input=json.dumps(payload), capture_output=True, text=True,
                              env=env, cwd=self.project)

    def master_post(self, tool: str, tool_input: dict, text: str):
        proc = self.hook_sh("snapshot", {"hook_event_name": "PostToolUse", "tool_name": C + tool, "tool_input": tool_input,
                                         "tool_response": [{"type": "text", "text": text}],
                                         "agent_type": M, "agent_id": "M1"})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_the_masters_tabs_context_reaches_preflight(self):
        start = self.aa_json("start")
        self.assertEqual(start["home_url"], T.MARKER_ROBOTS)
        self.master_post("tabs_context_mcp", {"createIfEmpty": True},
                         '{"availableTabs":[{"tabId":49,"title":"New Tab","url":"chrome://newtab/"}],"tabGroupId":7}\n')
        # the title of a plain-text page: the helper's tag, then the page's own title (the URL when it has none)
        for title in (f"{T.ON} www.linkedin.com/robots.txt", f"{T.ON} ", T.ON):
            self.master_post("tabs_context_mcp", {},
                             '{"availableTabs":[{"tabId":49,"title":' + json.dumps(title) + ',"url":"' + T.MARKER_ROBOTS
                             + '"}],"tabGroupId":7}\n')
            self.assertEqual(self.state()["tab_titles"]["49"], title)
            line = self.aa("preflight", "--tab", "49")
            self.assertTrue(line.startswith("OK"), f"title {title!r}: {line}")
            self.assertIn("pop-up helper: on", line)
