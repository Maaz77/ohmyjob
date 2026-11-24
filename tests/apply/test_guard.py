"""Rules of hooks.py and aa.py that the ohmyjob plugin added to the old application assistant: explicit allows, the
master's Bash, who is a Chrome caller, the spoofing rule, the prep hand-off, the workspace lookup, the preflight for
runs without a LinkedIn job, the run-folder removal and the tracker stop."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import test_aa as T
import aa_lib as L  # noqa: E402  (test_aa puts scripts/ on the path)
from test_aa import ATS, C, HOOKS, INDEED_JOB, LINKEDIN_JOB, M, SCRIPTS, W, sample

PREP_MASTER = "ohmyjob:prep-master"
RESUME_WORKER = "ohmyjob:resume-worker"


class TestMasterBash(T.Base):
    def bash(self, command, agent_type=M):
        return self.guard("Bash", {"command": command}, agent_type, "M1")

    def test_only_the_listed_apply_commands(self):
        for command in ("ohmyjob apply start", "ohmyjob apply start --continue", "ohmyjob apply preflight --tab 49",
                        "ohmyjob apply preflight --tab 49 --home-only", "ohmyjob apply assign --tabs 101,102",
                        "ohmyjob apply assign --continue", "ohmyjob apply next", "ohmyjob apply retry-plan",
                        "ohmyjob apply finish", "ohmyjob apply group-plan", "ohmyjob apply cleanup-plan",
                        "ohmyjob apply end", "ohmyjob apply adopt-plan", "ohmyjob apply adopted", "  ohmyjob apply next\n"):
            self.assertExplicitAllow(self.bash(command))
        for command in ("ohmyjob apply next; rm -rf /", "ohmyjob apply next\nls", "ohmyjob apply next && ls",
                        "ohmyjob apply questions", "ohmyjob apply answer --json '[]'", "ohmyjob apply gaps",
                        "ohmyjob apply authorize --json '[]'", "ohmyjob prep start",
                        "ohmyjob apply assign --tabs 1,x", "bash -c 'ohmyjob apply next'", "ls", "$(ohmyjob apply next)",
                        "ohmyjob apply next | cat", ""):
            self.assertDenied(self.bash(command), "ohmyjob apply")
        self.assertDenied(self.guard("Bash", {}, M, "M1"))

    def test_the_worker_has_no_bash_and_a_spoofed_command_is_not_enough(self):
        self.assertDenied(self.bash("ohmyjob apply next", W))
        # the main session and other agents: no decision (their own permission flow)
        self.assertNoDecision(self.guard("Bash", {"command": "ls"}))
        self.assertNoDecision(self.guard("Bash", {"command": "ls"}, "Explore", "E1"))


class TestReadsEditsAgents(T.Base):
    def test_reads(self):
        self.aa_json("start")
        (folder, _tab, brief), _other = self.assign(101, 102)
        self.assertExplicitAllow(self.guard("Read", {"file_path": brief}, W, "A1"))
        for note in ("workday.md", "greenhouse.md", "linkedin-easy-apply.md", "phenom.md"):
            self.assertExplicitAllow(self.guard("Read", {"file_path": str(ATS / note)}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(ATS / ".." / "scripts" / "aa.py")}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(self.root / "Profile.md")}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": brief}, M, "M1"), "reads no files")

    def test_edits_and_writes_are_denied_for_application_agents(self):
        for agent in (W, M):
            self.assertDenied(self.guard("Edit", {"file_path": str(self.root / "Profile.md")}, agent, "X1"))
            self.assertDenied(self.guard("Write", {"file_path": str(self.root / "x.txt")}, agent, "X1"))
        self.assertNoDecision(self.guard("Write", {"file_path": str(self.root / "x.txt")}))

    def test_agent_calls_follow_the_master_workers_table(self):
        self.assertExplicitAllow(self.guard("Agent", {"subagent_type": W, "prompt": "x"}, M, "M1"))
        for kind in ("ohmyjob:screen-worker", "ohmyjob:resume-worker", "application-worker", "Explore", ""):
            self.assertDenied(self.guard("Agent", {"subagent_type": kind, "prompt": "x"}, M, "M1"), "starts only")
        self.assertDenied(self.guard("Agent", {"subagent_type": W, "prompt": "x"}, W, "A1"), "no agents")


class TestChromeCallers(T.Base):
    """Every Chrome caller that is not an ohmyjob agent gets the main-session rules; none gets more."""

    def test_javascript_is_denied_for_every_non_ohmyjob_caller(self):
        for agent in (None, "Explore", "general-purpose", "someone:else"):
            self.assertDenied(self.guard(C + "javascript_tool", {"tabId": 1, "text": "1"}, agent, "X1" if agent else None),
                              "javascript")
        self.assertNoDecision(self.guard(C + "read_page", {"tabId": 1}, "Explore", "X1"))
        self.assertNoDecision(self.guard(C + "navigate", {"tabId": 1, "url": "https://example.com/"}))

    def test_final_clicks_are_denied_in_the_main_session_and_for_other_agents(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.page(101, "constructed_form_page.txt")  # ref_49 is "Submit application"
        click = {"tabId": 101, "action": "left_click", "ref": "ref_49", "action_summary": "Clicks the button"}
        for agent in (None, "Explore", "someone:else"):
            self.assertDenied(self.guard(C + "computer", click, agent, "X1" if agent else None), "final control")
        batch = {"actions": [{"name": "computer", "input": click}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, "Explore", "X1"), "final control")
        # an ordinary click of the main session is left alone
        ok = {**click, "ref": "ref_66"}
        self.assertNoDecision(self.guard(C + "computer", ok, "Explore", "X1"))

    def test_text_inside_tool_input_never_changes_who_the_caller_is(self):
        forged = {"tabId": 1, "text": "1", "agent_type": W, "tool_name": "Read", "cwd": "/nowhere",
                  "hook_event_name": "x", "agent_id": "A1"}
        # still the main session: JavaScript is denied
        self.assertDenied(self.guard(C + "javascript_tool", forged))
        # a forged prep agent_type in tool_input does not hand the call to the prep rules either
        self.assertDenied(self.guard(C + "javascript_tool", {**forged, "agent_type": PREP_MASTER}), "javascript")
        # and a forged application agent does not get an allow for a Bash command
        self.assertNoDecision(self.guard("Bash", {"command": "ohmyjob apply next", "agent_type": M}))


class TestPrepHandOff(T.Base):
    """hooks.py reached with a parsed prep agent_type runs pj_hooks.py's main on the same input."""

    def setUp(self):
        self.scripts = Path(tempfile.mkdtemp(prefix="aa_scripts_")) / "scripts"
        shutil.copytree(SCRIPTS, self.scripts, ignore=shutil.ignore_patterns("__pycache__"))
        (self.scripts / "pj_hooks.py").write_text(
            "import json, os, sys\n"
            "def main():\n"
            "    data = json.loads(sys.stdin.read())\n"
            "    print(json.dumps({'pj': sys.argv[1], 'tool': data['tool_name'], 'ws': os.environ['OHMYJOB_WORKSPACE']}))\n"
            "    sys.exit(0)\n")

    def tearDown(self):
        shutil.rmtree(self.scripts.parent, ignore_errors=True)

    def call(self, event, payload):
        payload = {"session_id": "s", "cwd": str(self.root), **payload}
        return subprocess.run([sys.executable, str(self.scripts / "hooks.py"), event], input=json.dumps(payload),
                              capture_output=True, text=True, env=self.env, cwd=self.root)

    def test_prep_agents_go_to_pj_hooks(self):
        for agent in (PREP_MASTER, RESUME_WORKER, "ohmyjob:screen-worker"):
            for event, tool in (("guard", C + "computer"), ("guard", "Bash"), ("handback", "SubagentHandback"),
                                ("capture", "x")):
                proc = self.call(event, {"hook_event_name": "x", "tool_name": tool, "tool_input": {}, "agent_type": agent,
                                         "agent_id": "P1"})
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = json.loads(proc.stdout)
                self.assertEqual((out["pj"], out["tool"], out["ws"]), (event, tool, str(self.root.resolve())))

    def test_other_callers_do_not(self):
        proc = self.call("guard", {"tool_name": C + "javascript_tool", "tool_input": {"agent_type": PREP_MASTER}})
        self.assertDenied(proc, "javascript")
        proc = self.call("guard", {"tool_name": "Bash", "tool_input": {"command": "ohmyjob apply next"}, "agent_type": M})
        self.assertExplicitAllow(proc)
        self.assertNotIn('"pj"', proc.stdout)
        # snapshot and report of a prep agent do nothing here
        proc = self.call("snapshot", {"tool_name": C + "read_page", "tool_input": {}, "agent_type": PREP_MASTER,
                                      "tool_response": "x"})
        self.assertNoDecision(proc)


class TestWorkspaceLookup(T.Base):
    def test_cwd_decides_and_no_workspace_means_no_output(self):
        outside = Path(tempfile.mkdtemp(prefix="aa_outside_"))
        try:
            env = {k: v for k, v in self.env.items() if k != "OHMYJOB_WORKSPACE"}
            payload = {"hook_event_name": "PreToolUse", "tool_name": C + "javascript_tool", "tool_input": {},
                       "cwd": str(outside)}
            proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps(payload), capture_output=True,
                                  text=True, env=env, cwd=outside)
            self.assertNoDecision(proc)
            # a subfolder of the workspace finds it; a cwd outside with an exported workspace only as a last candidate
            sub = self.root / "Applications"
            proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps({**payload, "cwd": str(sub)}),
                                  capture_output=True, text=True, env=env, cwd=outside)
            self.assertDenied(proc, "javascript")
            # the workspace that hook.sh exports is no fallback: a cwd outside any workspace means "not a workspace"
            proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps({**payload, "cwd": str(outside)}),
                                  capture_output=True, text=True, env=self.env, cwd=outside)
            self.assertNoDecision(proc)
            # nor does a "cwd" inside tool_input find one (hook.sh's text match can export it)
            forged = {k: v for k, v in payload.items() if k != "cwd"}
            forged["tool_input"] = {"cwd": str(self.root)}
            proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps(forged), capture_output=True,
                                  text=True, env={**self.env, "OHMYJOB_WORKSPACE": str(self.root)}, cwd=outside)
            self.assertNoDecision(proc)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    def test_unreadable_input(self):
        proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input="not json", capture_output=True, text=True,
                              env=self.env, cwd=self.root)
        self.assertEqual(proc.returncode, 2)
        proc = subprocess.run([sys.executable, str(HOOKS), "snapshot"], input="not json", capture_output=True, text=True,
                              env=self.env, cwd=self.root)
        self.assertNoDecision(proc)
        proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps({"cwd": str(self.root)}),
                              capture_output=True, text=True, env=self.env, cwd=self.root)
        self.assertEqual(proc.returncode, 2)


class TestSnapshotOutput(T.Base):
    def test_compact_page_uses_updated_tool_output_and_only_for_application_agents(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        page = "\n".join(['combobox "Country" [ref_2]'] + [f' option "Country {i}" [ref_{100 + i}]' for i in range(60)])
        text = page + "\n\nTab Context:\n- tab 101"
        proc = self.post("read_page", {"tabId": 101, "filter": "all"}, text, W, "A1")
        out = json.loads(proc.stdout)["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "PostToolUse")
        self.assertNotIn("updatedMCPToolOutput", out)
        self.assertIn("more options", out["updatedToolOutput"][0]["text"])
        # the master's page reads are not compacted; another caller's are not even saved
        proc = self.post("read_page", {"tabId": 101, "filter": "all"}, text, M, "M1")
        self.assertEqual(proc.stdout.strip(), "")
        before = self.state()
        proc = self.hook("snapshot", {"tool_name": C + "tabs_context_mcp", "tool_input": {}, "agent_type": "Explore",
                                      "agent_id": "E1", "tool_response": [{"type": "text", "text":
                                      '{"availableTabs":[{"tabId":999,"title":"x","url":"https://x.example/"}]}'}]})
        self.assertNoDecision(proc)
        self.assertEqual(self.state().get("tab_urls"), before.get("tab_urls"))
        proc = self.hook("snapshot", {"tool_name": C + "tabs_context_mcp", "tool_input": {"agent_type": W},
                                      "tool_response": "x"})  # a forged agent_type inside tool_input
        self.assertNoDecision(proc)


class TestBriefAndOverview(T.Base):
    def test_the_brief_names_the_apply_url_and_the_job_url(self):
        start = self.aa_json("start")
        self.assertEqual(start["count"], 2)
        jobs = self.state()["jobs"]
        self.assertEqual(jobs[INDEED_JOB]["url"], "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268")
        self.assertEqual(jobs[INDEED_JOB]["apply_url"], "https://careers.quillon.example/jobs/project-coordinator-48213")
        self.assertEqual(jobs[LINKEDIN_JOB]["apply_url"], "")
        by_folder = {f: Path(b).read_text() for f, _t, b in self.assign(101, 102)}
        indeed = by_folder[INDEED_JOB]
        self.assertIn("- Job URL: https://de.indeed.com/viewjob?jk=4be1a09c37d5f268", indeed)
        self.assertIn("- Apply URL (the employer's own application address: navigate to it first", indeed)
        self.assertIn("https://careers.quillon.example/jobs/project-coordinator-48213", indeed)
        self.assertNotIn("Apply URL", by_folder[LINKEDIN_JOB])
        self.assertIn("- Job URL: https://www.linkedin.com/jobs/view/4399000001", by_folder[LINKEDIN_JOB])
        self.assertIn(str(ATS / "workday.md"), indeed)
        self.assertIn("```yaml", indeed)

    def test_a_job_row_without_a_folder_overview_url_uses_the_tracker_url(self):
        job_md = self.root / "Applications" / INDEED_JOB / "job.md"
        text = job_md.read_text(encoding="utf-8")
        job_md.write_text("\n".join(l for l in text.splitlines() if "Job URL" not in l and "Apply URL" not in l) + "\n",
                          encoding="utf-8")
        try:
            self.aa_json("start")
            job = self.state()["jobs"][INDEED_JOB]
            self.assertEqual((job["url"], job["apply_url"]), ("https://de.indeed.com/viewjob?jk=4be1a09c37d5f268", ""))
        finally:
            job_md.write_text(text, encoding="utf-8")


class TestPreflightWithoutLinkedIn(T.Base):
    jobs = (INDEED_JOB,)

    def test_a_run_without_a_linkedin_job_uses_the_robots_page_and_skips_the_sign_in_check(self):
        start = self.aa_json("start")
        self.assertEqual((start["home_url"], start["linkedin_sign_in_check"]), (T.MARKER_ROBOTS, False))
        line = self.master_setup(T.MARKER_ROBOTS)
        self.assertIn("LinkedIn sign-in check skipped", line)
        self.assertIn("pop-up helper: on", line)
        # the master may load that page; a worker may not
        self.assertExplicitAllow(self.guard(C + "navigate", {"tabId": 49, "url": T.MARKER_ROBOTS}, M, "M1"))
        self.assign(101)
        self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": T.MARKER_ROBOTS}, W, "A1"), "master only")

    def test_signed_out_is_not_checked(self):
        self.aa_json("start")
        self.assertExplicitAllow(self.guard(C + "navigate", {"tabId": 49, "url": T.MARKER_ROBOTS}, M, "M1"))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"' + T.ON + ' x","url":"https://www.linkedin.com/authwall"}]}\n', M, "M1")
        self.assertTrue(self.aa("preflight", "--tab", "49").startswith("OK"))


class TestPreflightWithLinkedIn(T.Base):
    def test_home_url_is_the_feed_and_signed_out_stops(self):
        start = self.aa_json("start")
        self.assertEqual((start["home_url"], start["linkedin_sign_in_check"]), (T.MARKER_FEED, True))
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"x","url":"https://www.linkedin.com/authwall?trk=x"}]}\n', M, "M1")
        self.assertTrue(self.aa("preflight", "--tab", "49").startswith("SIGNED_OUT"))

    def test_extension_version_mismatch_warns(self):
        self.aa_json("start")
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA on 0.0.1] Feed","url":"' + T.MARKER_FEED + '"}]}\n', M, "M1")
        line = self.aa("preflight", "--tab", "49")
        self.assertTrue(line.startswith("OK"), line)
        self.assertIn(f"WARN pop-up helper: on, but version 0.0.1 while the plugin has {T.VERSION}", line)
        self.assertIn("reload", line)
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"[AA on] Feed","url":"' + T.MARKER_FEED + '"}]}\n', M, "M1")
        self.assertIn("WARN pop-up helper: on, but version older", self.aa("preflight", "--tab", "49"))


class TestRunFolders(T.Base):
    def test_only_our_run_folders_are_removed(self):
        base = self.root / ".ohmyjob" / "run" / "apply"
        ours = base / "2026-10-17_abcdef"
        ours.mkdir(parents=True)
        (ours / "state.json").write_text(json.dumps({"run_id": ours.name}))
        crashed = base / "2026-10-16_fedcba"
        crashed.mkdir()
        (crashed / ".lock").write_text("")
        foreign = {"2026-10-15_zzzzzz": "name matches no run id", "notes": "not a run id", "2026-10-14_abcdef": "no run state"}
        for name in foreign:
            (base / name).mkdir()
            (base / name / "keep.txt").write_text("mine")
        wrong_id = base / "2026-10-13_abcdef"
        wrong_id.mkdir()
        (wrong_id / "state.json").write_text(json.dumps({"run_id": "other"}))
        (base / "stray.txt").write_text("mine")
        outside = Path(tempfile.mkdtemp(prefix="aa_keep_"))
        (outside / "keep.txt").write_text("mine")
        (base / "2026-10-12_abcdef").symlink_to(outside)
        (base / "CURRENT").write_text("2026-10-17_abcdef")
        try:
            self.aa_json("start")
            self.assertFalse(ours.exists() or crashed.exists())
            for name in foreign:
                self.assertTrue((base / name / "keep.txt").exists(), name)
            self.assertTrue((wrong_id / "state.json").exists())
            self.assertTrue((base / "stray.txt").exists())
            self.assertTrue((outside / "keep.txt").exists())
            self.assertTrue((base / "2026-10-12_abcdef").is_symlink())
            current = (base / "CURRENT").read_text().strip()
            self.assertNotEqual(current, "2026-10-17_abcdef")
            self.assertTrue((base / current / "state.json").exists())
            self.aa("end")
            self.assertTrue((base / "stray.txt").exists() and (outside / "keep.txt").exists())
            self.assertFalse((base / current).exists())
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestTrackerStop(T.Base):
    def test_finish_stops_with_fatal_when_the_tracker_cannot_be_saved_and_retries(self):
        self.aa_json("start")
        (folder, t1, _b1), (folder2, t2, _b2) = self.assign(101, 102)
        for agent, tab, fold in (("F1", t1, folder), ("F2", t2, folder2)):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://www.linkedin.com/jobs/view/1"}, W, agent))
            report = {"folder": fold, "tab": tab, "status": "blocked", "where": "job page", "final_button": "",
                      "url": "https://www.linkedin.com/jobs/view/1", "filled": [], "uploaded": None, "drafted": [],
                      "open": [], "declarations": [], "blocker": "ACCOUNT_WALL: the site wants an account"}
            self.assertAllowed(self.handback(json.dumps(report), agent_id=agent))
        lock = self.root / "~$Job_Tracker.xlsx"
        lock.write_text("")
        proc = self.run_aa("finish")
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("NOT RECORDED", proc.stdout)
        self.assertIn("FATAL", proc.stdout)
        self.assertTrue((self.root / "Applications" / folder / "job.md").exists())
        self.assertFalse((self.root / "Needs-Attention" / folder).exists())
        self.assertNotIn("# Application Assistant", (self.root / "Applications" / folder / "job.md").read_text())
        lock.unlink()
        summary = self.aa("finish")
        self.assertIn("Needs-Attention: 2", summary)
        self.assertTrue((self.root / "Needs-Attention" / folder / "job.md").exists())


class TestQuestionsFile(T.Base):
    def test_waiting_questions_live_in_the_state_folder(self):
        self.assertEqual(self.aa_json("questions"), {"questions": []})
        code = "import aa; aa.add_questions(['Years of Rust?'], 'Acme | Dev')"
        subprocess.run([sys.executable, "-c", code], env=self.env, cwd=SCRIPTS, check=True)
        self.assertEqual(json.loads((self.root / ".ohmyjob" / "questions.json").read_text())[0]["q"], "Years of Rust?")
        self.assertEqual(self.aa_json("answer", "--json", json.dumps([{"q": "Years of Rust?", "a": "None."}]))["saved"], 1)
        self.assertIn("11. Years of Rust? None.", (self.root / "Profile.md").read_text())


class TestJobMdWins(T.Base):
    def test_job_md_url_wins_over_the_tracker_url(self):
        from tracker import Tracker

        tracker = Tracker(self.root / "Job_Tracker.xlsx")
        row = tracker.rows_for_key(LINKEDIN_JOB.split("_")[0])[0]  # a LinkedIn address keeps its key with a query added
        tracker.set_cell(row["_index"], "Job URL", row["Job URL"] + "?from=tracker")
        tracker.save()
        self.aa_json("start")
        job = self.state()["jobs"][LINKEDIN_JOB]
        self.assertEqual(job["url"], "https://www.linkedin.com/jobs/view/4399000001")  # job.md, not the tracker cell


class TestLinkedInJobWithExternalApplyUrl(T.Base):
    jobs = (LINKEDIN_JOB,)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        md = cls.root / "Applications" / LINKEDIN_JOB / "job.md"
        md.write_text(md.read_text(encoding="utf-8").replace(
            "- Source: linkedin", "- Apply URL: https://careers.example.org/jobs/ops-analyst\n- Source: linkedin"), encoding="utf-8")

    def test_no_sign_in_check_when_the_job_starts_on_the_employers_site(self):
        start = self.aa_json("start")
        self.assertEqual((start["home_url"], start["linkedin_sign_in_check"]), (T.MARKER_ROBOTS, False))
        self.assertEqual(self.state()["jobs"][LINKEDIN_JOB]["apply_url"], "https://careers.example.org/jobs/ops-analyst")
        line = self.master_setup(T.MARKER_ROBOTS)
        self.assertIn("LinkedIn sign-in check skipped", line)


class TestSignInNeedsTheFeed(T.Base):
    def test_robots_page_is_not_enough_when_a_job_is_on_linkedin(self):
        self.aa_json("start")
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"' + T.ON + ' x","url":"' + T.MARKER_ROBOTS + '"}]}\n', M, "M1")
        for _ in range(4):  # RETRY while the page can still change, then UNKNOWN
            line = self.aa("preflight", "--tab", "49")
        self.assertTrue(line.startswith("UNKNOWN"), line)
        self.assertIn("feed", line)
        self.post("tabs_context_mcp", {}, '{"availableTabs":[{"tabId":49,"title":"' + T.ON + ' x","url":"' + T.MARKER_FEED + '"}]}\n', M, "M1")
        self.assertTrue(self.aa("preflight", "--tab", "49").startswith("OK"))


class TestBadConfig(T.Base):
    def test_a_bad_config_is_a_fatal_line_in_start(self):
        config = self.root / "ohmyjob.json"
        good = config.read_text()
        try:
            config.write_text(json.dumps({"search": {"sources": ["indeed"], "country": ""}}))
            proc = self.run_aa("start")
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
            self.assertTrue(proc.stdout.startswith("FATAL: "), proc.stdout)
            self.assertIn("search.country", proc.stdout)
            self.assertNotIn("Traceback", proc.stderr)
            config.write_text("{not json")
            self.assertTrue(self.run_aa("start").stdout.startswith("FATAL: "))
        finally:
            config.write_text(good)


class TestRunReport(unittest.TestCase):
    def test_cost_per_unit_of_work_and_an_unknown_model(self):
        import collections

        import aa_lib

        tokens = collections.Counter({"calls": 1, "input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000,
                                      "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000})
        self.assertAlmostEqual(aa_lib.usage_cost(tokens, "claude-haiku-5-5"), 0.10 + 0.125 + 0.01 + 0.50)
        self.assertIsNone(aa_lib.usage_cost(tokens, "gpt-x"))
        report = aa_lib.run_report(None, None, {"ohmyjob:application-worker": 2},
                                   {("ohmyjob:application-worker", "claude-haiku-5-5"): tokens, ("Haiku check", "haiku"): tokens,
                                    ("other", "gpt-x"): tokens})
        self.assertIn("Run cost ≈ $1.47 ", report)  # two haiku rows, the unknown model adds nothing
        self.assertIn("applications    2 jobs", report)
        self.assertIn("$0.73 per application", report)
        self.assertIn("overhead", report)
        self.assertIn("cost unknown for model gpt-x", report)


class TestSkillText(unittest.TestCase):
    def test_the_apostrophe_escape_is_written_out(self):
        for name in ("apply-jobs", "continue-applying"):
            text = (T.REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("apostrophe inside the JSON as " + chr(92) + "u0027", text)


class TestBriefClaim(T.Base):
    """A worker's brief: the claim, the hint in a refused Read, and the brief of a second worker (4.12)."""

    def read(self, path, agent="A1"):
        return self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Read",
                                   "tool_input": {"file_path": str(path)}, "agent_type": W, "agent_id": agent})

    def test_a_refused_read_names_the_brief_this_worker_claimed(self):
        self.aa_json("start")
        (folder, _tab, brief), (folder2, _t2, brief2) = self.assign(101, 102)
        self.assertExplicitAllow(self.read(brief))
        # another job's brief, and a file that is no brief at all: refused, with the path of its own brief
        for path in (brief2, self.root / "Profile.md"):
            proc = self.read(path)
            self.assertDenied(proc, "may read only")
            self.assertTrue(proc.stderr.strip().endswith(f"Your brief is {brief}"), proc.stderr)
        # a worker with no brief yet gets no hint
        proc = self.read(self.root / "Profile.md", agent="A9")
        self.assertDenied(proc, "may read only")
        self.assertNotIn("Your brief is", proc.stderr)

    def test_a_second_worker_of_a_retried_job_claims_the_brief_again(self):
        self.aa_json("start")
        (folder, tab, brief), *_rest = self.assign(101, 102)
        self.assertExplicitAllow(self.read(brief))
        self.assertDenied(self.read(brief, agent="B1"), "may read only")  # claimed by the first worker
        # the job goes to a new worker: 'next' clears the claim, and the new worker takes it
        self.set_job(folder, brief_agent=None, started_at=T.time.time())
        self.assertExplicitAllow(self.read(brief, agent="B1"))
        self.assertEqual(self.state()["jobs"][folder]["brief_agent"], "B1")


class TestJudgeErrorReported(T.Base):
    """Spec section 6, bug 1. Its own workspace: the test runs a whole apply run."""

    def test_a_failed_haiku_check_reaches_the_summary(self):
        """Bug 1: the judge error must be saved under the lock, else `finish` cannot report it."""
        self.aa_json("start")
        (folder, tab, _brief), *_rest = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1"))
        # a sign-up name is one of the dangerous words: a failed check keeps refusing it (an ordinary name is let through)
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  f'button "Register fallisci" [ref_7]\n\nTab Context:\n- tab {tab}', W, "A1")
        self.assertDenied(self.click(tab, "ref_7", "A1", "Goes on"), "check")
        self.assertIn("Register fallisci", self.state()["jobs"][folder]["judge_error"])
        self.assertIn("WARN Haiku check failed for", self.aa("finish"))

class TestTrackerSaveFailure(T.Base):
    """Spec section 6, bug 3. Its own workspace: it damages the tracker."""

    def test_a_tracker_save_failure_is_always_a_fatal_line(self):
        """Bug 3: `record_all` must return the error text for any save failure, not only a TrackerError."""
        self.aa_json("start")
        (folder, tab, _brief), *_rest = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1"))
        backup = (self.root / "Job_Tracker.xlsx").read_bytes()
        (self.root / "Job_Tracker.xlsx").write_bytes(b"not a workbook")  # the load or the save raises
        try:
            proc = self.run_aa("finish")
        finally:
            (self.root / "Job_Tracker.xlsx").write_bytes(backup)
        self.assertEqual(proc.returncode, 3, proc.stdout)
        self.assertIn("FATAL", proc.stdout)

class TestDamagedQuestionsFile(T.Base):
    """Spec section 6, bug 5. Its own workspace: it damages questions.json."""

    def test_a_damaged_questions_file_names_the_file(self):
        """Bug 5: a file the candidate can edit must never raise a traceback."""
        self.aa_json("start")
        (self.root / ".ohmyjob" / "questions.json").write_text("{not json", encoding="utf-8")
        proc = self.run_aa("questions")
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertIn("questions.json", proc.stdout)
        self.assertIn("FATAL", proc.stdout)


class TestWorkerBypasses(T.Base):
    """The worker's own rules, one bypass each (keys, typed text, addresses, click fields, page writes, reads)."""

    def start(self):
        self.aa_json("start")
        (self.f1, self.t1, self.b1), (self.f2, self.t2, self.b2) = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 102, "url": "https://careers.example.com/a"}, W, "A2"))

    def key(self, text, agent="A1", tab=101):
        return self.guard(C + "computer", {"tabId": tab, "action": "key", "text": text}, W, agent)

    def test_keys_are_an_allowlist(self):
        self.start()
        for keys in ["NumpadEnter", "KP_Return", "KP_Enter", "ISO_Enter", "\r", "\n", "Return", "space", "cmd+Enter", "F5"]:
            self.assertDenied(self.key(keys), "press only")
            self.clear_watchdog()  # the same denial repeated would stop the worker (4.7)
        for keys in ["Tab", "shift+Tab", "Escape", "ctrl+a"]:
            self.assertExplicitAllow(self.key(keys))

    def test_the_key_refusal_names_the_dropdown_route(self):
        """A worker that pressed Enter on a search list is told the route that works, not only what is refused."""
        self.start()
        for part in ("read_page alone", "click the option by its ref", "cannot be answered"):
            self.assertDenied(self.key("Enter"), part)
            self.clear_watchdog()  # the same denial repeated would stop the worker (4.7)

    def test_typed_text_has_no_control_characters(self):
        self.start()
        self.page(101, "constructed_form_page.txt")
        self.assertAllowed(self.click(101, "ref_41", "A1", "Focuses the First name field"))
        for text in ["x\t ", "x\ty", "a\rb", "a\nb", "a\x1bb", "a\x7fb"]:
            self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": text}, W, "A1"),
                              "control characters")
            self.clear_watchdog()  # the same denial repeated would stop the worker (4.7)
        self.assertExplicitAllow(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "Alex Example"}, W, "A1"))

    def test_addresses_with_control_characters_are_refused(self):
        self.start()
        for url in ["java\tscript:alert(1)", "javascript\n:alert(1)", "\x01javascript:alert(1)", "javascript:alert(1)",
                    "https://example.com/\x00x", "ht\ntps://example.com"]:
            self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": url}, W, "A1"), "http")
            self.clear_watchdog()  # the same denial repeated would stop the worker (4.7)
        batch = {"actions": [{"name": "navigate", "input": {"tabId": 101, "url": "java\tscript:alert(1)"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "item 1")
        self.assertExplicitAllow(self.guard(C + "navigate", {"tabId": 101, "url": "https://example.com/ok"}, W, "A1"))

    def test_a_click_passes_only_the_checked_fields(self):
        self.start()
        self.page(101, "constructed_form_page.txt")
        proc = self.guard(C + "computer", {"tabId": 101, "action": "left_click", "ref": "ref_66", "action_summary": "Goes to the next step",
                                           "coordinate": [10, 20], "text": "ctrl", "modifiers": "shift"}, W, "A1")
        self.assertExplicitAllow(proc)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"],
                         {"tabId": 101, "action": "left_click", "ref": "ref_66", "action_summary": "Goes to the next step"})
        self.page(101, "constructed_form_page.txt")
        proc = self.click(101, "ref_66", "A1", "Goes to the next step")
        self.assertNotIn("updatedInput", proc.stdout)  # a clean click is not rewritten

    def test_a_lone_surrogate_in_a_page_does_not_freeze_the_refs(self):
        self.start()
        self.post("read_page", {"tabId": 101}, 'button "Next" [ref_5] type="button"\n\nTab Context:\n- tab 101', W, "A1")
        self.assertAllowed(self.click(101, "ref_5", "A1", "Goes to the next step"))
        # the next page has a lone surrogate in a title and shows Submit under the same ref
        text = ('generic "Job \\ud800 title" [ref_1]\nbutton "Submit application" [ref_5] type="submit"\n\n'
                'Tab Context:\n- tab 101')
        proc = self.hook("snapshot", {"hook_event_name": "PostToolUse", "tool_name": C + "read_page",
                                      "tool_input": {"tabId": 101, "filter": "interactive"}, "agent_type": W, "agent_id": "A1",
                                      "tool_response": [{"type": "text", "text": text.replace("\\ud800", "\ud800")}]})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        refs = json.loads((self.run_dir() / "snap" / "tab_101.refs.json").read_text(encoding="utf-8"))
        self.assertEqual(refs["ref_5"]["name"], "Submit application")
        self.assertDenied(self.click(101, "ref_5", "A1", "Clicks the button"), "final control")

    def test_a_full_read_that_cannot_be_written_leaves_no_old_refs(self):
        self.start()
        self.post("read_page", {"tabId": 101}, 'button "Next" [ref_5] type="button"\n\nTab Context:\n- tab 101', W, "A1")
        refs_file = self.run_dir() / "snap" / "tab_101.refs.json"
        self.assertTrue(refs_file.exists())
        snap = self.run_dir() / "snap" / "tab_101.txt"
        snap.unlink()
        snap.mkdir()  # the page file cannot be written: the write fails
        proc = self.hook("snapshot", {"hook_event_name": "PostToolUse", "tool_name": C + "read_page",
                                      "tool_input": {"tabId": 101}, "agent_type": W, "agent_id": "A1",
                                      "tool_response": 'button "Submit application" [ref_5]\n\nTab Context:\n- tab 101'})
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(refs_file.exists())  # the old refs of the old page must not judge clicks on the new page
        self.assertDenied(self.click(101, "ref_5", "A1", "Goes to the next step"), "unknown ref")

    def test_reads_are_the_workers_own_brief_and_the_ats_notes_by_absolute_path(self):
        self.start()
        for note in (ATS / "workday.md", ATS / "greenhouse.md"):
            self.assertExplicitAllow(self.guard("Read", {"file_path": str(note)}, W, "A1"))
        self.assertExplicitAllow(self.guard("Read", {"file_path": self.b1}, W, "A1"))
        self.assertExplicitAllow(self.guard("Read", {"file_path": self.b1}, W, "A1"))  # again: the same worker
        self.assertDenied(self.guard("Read", {"file_path": self.b2}, W, "A1"), "own brief")
        self.assertExplicitAllow(self.guard("Read", {"file_path": self.b2}, W, "A2"))
        self.assertDenied(self.guard("Read", {"file_path": self.b1}, W, "A2"), "own brief")
        self.assertDenied(self.guard("Read", {"file_path": self.b1}, W, "A3"), "own brief")
        jobs = self.run_dir() / "jobs"
        (jobs / self.f1 / "result.json").write_text("{}")
        self.assertDenied(self.guard("Read", {"file_path": str(jobs / self.f1 / "result.json")}, W, "A1"), "own brief")
        self.assertDenied(self.guard("Read", {"file_path": str(self.run_dir() / "state.json")}, W, "A1"))
        # relative paths are refused, not resolved; so are ~ and .. out of the notes folder
        self.assertDenied(self.guard("Read", {"file_path": f"jobs/{self.f1}/brief.md"}, W, "A1"), "absolute")
        self.assertDenied(self.guard("Read", {"file_path": "~/brief.md"}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(ATS / ".." / "scripts" / "aa.py")}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(ATS / "workday.md") + "\n"}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": None}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": ["a"]}, W, "A1"))
        # the workspace's own notes (learned by Dream) are readable too, by an absolute path
        user_ats = self.root / "ats"
        user_ats.mkdir(exist_ok=True)
        (user_ats / "workday.md").write_text("# Workday\nhosts: *.myworkdayjobs.com\n", encoding="utf-8")
        self.assertExplicitAllow(self.guard("Read", {"file_path": str(user_ats / "workday.md")}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": str(user_ats / ".." / "Profile.md")}, W, "A1"))
        # a new worker for the same job (UPLOAD_HANG retry, or a dropped worker printed again) may claim the brief again
        (jobs / self.f1 / "result.json").unlink()
        state = self.state()
        state["tabs"]["101"]["agent_id"] = None  # what retry-plan does for a job it starts again
        state["jobs"][self.f1].update(started_at=time.time() - 120, spawned=False)
        (self.run_dir() / "state.json").write_text(json.dumps(state))
        self.aa_json("next")
        self.assertExplicitAllow(self.guard("Read", {"file_path": self.b1}, W, "A9"))


class TestForeignCallersEveryRunState(T.Base):
    """The main session and other agents are checked with no run, with the refs of the pages THEY read."""

    def read(self, text, tab=301, tool="read_page", agent=None, **ti):
        payload = {"hook_event_name": "PostToolUse", "tool_name": C + tool, "tool_input": {"tabId": tab, **ti},
                   "tool_response": [{"type": "text", "text": text}]}
        if agent:
            payload.update(agent_type=agent, agent_id="X1")
        proc = self.hook("snapshot", payload)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc

    def foreign_click(self, ref, tab=301, agent=None, **extra):
        return self.guard(C + "computer", {"tabId": tab, "action": "left_click", **({"ref": ref} if ref else {}), **extra},
                          agent, "X1" if agent else None)

    PAGE = 'button "Next" [ref_1] type="button"\nbutton "Submit application" [ref_2] type="submit"\ncheckbox "I confirm" [ref_3]\n'

    def test_no_run_is_active(self):
        self.assertFalse((self.root / ".ohmyjob" / "run" / "apply" / "CURRENT").exists())
        for agent in (None, "Explore", "general-purpose"):
            # a click needs a ref that a read showed
            self.assertDenied(self.foreign_click("ref_2", agent=agent), "read the page")
            self.assertDenied(self.foreign_click(None, agent=agent, coordinate=[10, 10]), "coordinates")
            self.read(self.PAGE, agent=agent)
            self.assertDenied(self.foreign_click("ref_2", agent=agent), "final control")
            self.assertDenied(self.foreign_click("ref_9", agent=agent), "unknown ref")
            self.assertDenied(self.foreign_click("ref_2", tab=302, agent=agent), "unknown ref")  # another tab's page
            self.assertNoDecision(self.foreign_click("ref_3", agent=agent))  # a checkbox "I confirm" sends nothing
            self.assertDenied(self.foreign_click("ref_1", agent=agent), "unknown ref")  # but it voided the refs
            self.read(self.PAGE, agent=agent)
            self.assertNoDecision(self.foreign_click("ref_1", agent=agent))
            # a click may change the page: after its result the refs are forgotten until the next read
            self.hook("snapshot", {"tool_name": C + "computer", "tool_response": "Clicked",
                                   "tool_input": {"tabId": 301, "action": "left_click", "ref": "ref_1"},
                                   **({"agent_type": agent, "agent_id": "X1"} if agent else {})})
            self.assertDenied(self.foreign_click("ref_1", agent=agent), "unknown ref")

    def test_enter_and_typed_line_breaks(self):
        for agent in (None, "Explore"):
            for keys in ["Return", "Enter", "NumpadEnter", "KP_Return", "ISO_Enter", "\r", "\n", "space", "shift+Enter"]:
                self.assertDenied(self.guard(C + "computer", {"tabId": 301, "action": "key", "text": keys}, agent,
                                             "X1" if agent else None), "press")
            self.assertDenied(self.guard(C + "computer", {"tabId": 301, "action": "type", "text": "x\n"}, agent,
                                         "X1" if agent else None), "control characters")
            for keys in ["Tab", "Escape", "ctrl+a"]:
                self.assertNoDecision(self.guard(C + "computer", {"tabId": 301, "action": "key", "text": keys}, agent,
                                                 "X1" if agent else None))
        batch = {"actions": [{"name": "computer", "input": {"tabId": 301, "action": "key", "text": "Return"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch), "press")

    def test_find_results_count_and_a_navigation_forgets_the_page(self):
        self.read('button "Submit application" [ref_7] type="submit"\n', tool="find", agent="Explore")
        self.assertDenied(self.foreign_click("ref_7", agent="Explore"), "final control")
        self.read('button "Next" [ref_8] type="button"\n', tool="find")
        self.assertNoDecision(self.foreign_click("ref_8"))  # find added ref_8 (a click forgets the page...)
        self.read(self.PAGE)
        self.hook("snapshot", {"tool_name": C + "navigate", "tool_input": {"tabId": 301, "url": "https://example.com/"},
                               "tool_response": "ok"})
        self.assertDenied(self.foreign_click("ref_1"), "unknown ref")

    def test_javascript_addresses_and_calls(self):
        for url in ["javascript:alert(1)", "java\tscript:alert(1)", "\x01JavaScript:alert(1)", "vbscript:x"]:
            self.assertDenied(self.guard(C + "navigate", {"tabId": 301, "url": url}, "Explore", "X1"), "javascript")
        self.assertNoDecision(self.guard(C + "navigate", {"tabId": 301, "url": "https://example.com/"}))
        self.assertDenied(self.guard(C + "javascript_tool", {"tabId": 301, "text": "1"}), "javascript")

    def test_the_output_of_other_callers_is_never_rewritten_and_prep_agents_are_ignored(self):
        page = "\n".join(['combobox "Country" [ref_2]'] + [f' option "Country {i}" [ref_{100 + i}]' for i in range(60)])
        proc = self.read(page + "\n\nTab Context:\n- tab 301", agent="Explore", filter="all")
        self.assertEqual(proc.stdout.strip(), "")
        proc = self.read(page, agent="Explore", filter="all")
        self.assertEqual(proc.stdout.strip(), "")
        store = self.root / ".ohmyjob" / "run" / "chrome"
        self.assertTrue((store / "tab_301.refs.json").exists())
        (store / "tab_301.refs.json").unlink()
        for agent in ("ohmyjob:prep-master", "ohmyjob:resume-worker"):
            self.read(self.PAGE, agent=agent)
        self.assertFalse((store / "tab_301.refs.json").exists())

    def test_worker_pages_count_for_other_callers_too(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.page(101, "constructed_form_page.txt")
        self.assertDenied(self.foreign_click("ref_49", tab=101), "final control")  # "Submit application" of the worker's page
        self.assertDenied(self.foreign_click("ref_49", tab=101, agent="Explore"), "final control")


class TestForeignOneClickPerRead(T.Base):
    """One rule for every caller that is not an ohmyjob agent: a click needs a fresh read; a read allows one pressable
    click; that click, a key, typed text or a navigation voids the tab's refs. Only an allowlist of computer actions."""

    PAGE = ('button "Next" [ref_1] type="button"\nbutton "Save" [ref_4] type="button"\n'
            'button "Submit application" [ref_2] type="submit"\ncheckbox "I confirm" [ref_3]\ntextbox "Name" [ref_5]\n'
            'link "Terms" [ref_6] href="https://example.com/terms"\n')

    def read(self, text=None, tab=301, agent=None, **ti):
        payload = {"hook_event_name": "PostToolUse", "tool_name": C + "read_page", "tool_input": {"tabId": tab, **ti},
                   "tool_response": [{"type": "text", "text": text or self.PAGE}]}
        if agent:
            payload.update(agent_type=agent, agent_id="X1")
        self.assertEqual(self.hook("snapshot", payload).returncode, 0)

    def computer(self, agent=None, **ti):
        return self.guard(C + "computer", {"tabId": 301, **ti}, agent, "X1" if agent else None)

    def batch(self, *actions, agent=None):
        return self.guard(C + "browser_batch", {"actions": [{"name": n, "input": {"tabId": 301, **i}} for n, i in actions]},
                          agent, "X1" if agent else None)

    def click(self, ref, **extra):
        return {"action": "left_click", "ref": ref, **extra}

    def test_a_batch_cannot_click_twice_or_click_after_changing_the_page(self):
        for agent in (None, "Explore"):
            self.read(agent=agent)
            self.assertDenied(self.batch(("computer", self.click("ref_1")), ("computer", self.click("ref_1")), agent=agent),
                              "read the page again")
            self.assertDenied(self.batch(("computer", self.click("ref_1")), ("computer", self.click("ref_4")), agent=agent),
                              "read the page again")
            self.assertDenied(self.batch(("computer", {"action": "key", "text": "Tab"}), ("computer", self.click("ref_1")),
                                         agent=agent), "read the page again")
            self.assertDenied(self.batch(("navigate", {"url": "https://example.com/"}), ("computer", self.click("ref_1")),
                                         agent=agent), "read the page again")
            self.assertDenied(self.batch(("computer", self.click("ref_2")), agent=agent), "final control")
            self.assertDenied(self.batch(("read_page", {}), agent=agent), "inside browser_batch")
            self.assertDenied(self.batch(("javascript_tool", {"text": "1"}), agent=agent), "javascript")
            # a denied call changes nothing: the page can still be clicked once
            self.assertDenied(self.batch(("computer", self.click("ref_3")), ("computer", self.click("ref_1")), agent=agent),
                              "read the page again")  # a box click voids the refs too
            self.assertNoDecision(self.batch(("computer", self.click("ref_1")), agent=agent))
            # that batch voided the refs, so a click after it needs a new read
            self.assertDenied(self.computer(agent, **self.click("ref_4")), "read the page")
            self.read(agent=agent)
            self.assertNoDecision(self.computer(agent, **self.click("ref_4")))
            self.assertDenied(self.computer(agent, **self.click("ref_1")), "read the page")

    def test_only_allowlisted_computer_actions_and_never_a_coordinate(self):
        for agent in (None, "Explore"):
            self.read(agent=agent)
            for extra in ({"action": "left_click_drag", "start_coordinate": [5, 5], "coordinate": [5, 5]},
                          {"action": "left_click_drag", "ref": "ref_2"},
                          {"action": "right_click", "ref": "ref_1"}, {"action": "double_click", "ref": "ref_1"},
                          {"action": "triple_click", "ref": "ref_1"}, {"action": "left_mouse_down"},
                          {"action": "left_click", "ref": "ref_1", "coordinate": [10, 10]},
                          {"action": "left_click", "ref": "ref_2", "coordinate": [10, 10]},
                          {"action": "hover", "coordinate": [10, 10]}, {"action": "hover", "ref": "ref_1", "coordinate": [1, 1]},
                          {"action": "left_click", "coordinate": [10, 10]}, {"action": None}, {}):
                self.assertDenied(self.computer(agent, **extra), "")
            for extra in ({"action": "screenshot"}, {"action": "scroll", "coordinate": [10, 10], "scroll_direction": "down"},
                          {"action": "scroll_to", "ref": "ref_2"}, {"action": "wait", "duration": 1},
                          {"action": "hover", "ref": "ref_1"}, {"action": "zoom", "region": [0, 0, 5, 5]}):
                self.assertNoDecision(self.computer(agent, **extra))
            # none of that used up the read
            self.assertNoDecision(self.computer(agent, **self.click("ref_1")))

    def test_a_worker_snapshot_ref_can_be_clicked_once_per_foreign_read(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.page(101, "constructed_form_page.txt")
        for agent in (None, "Explore"):
            self.assertNoDecision(self.computer(agent, tabId=101, **self.click("ref_66")))
            self.assertDenied(self.computer(agent, tabId=101, **self.click("ref_66")), "one click per read")
            self.assertDenied(self.computer(agent, tabId=101, **self.click("ref_44")), "one click per read")
            self.read('button "Next" [ref_66] type="button"\n', tab=101, agent=agent)
            self.assertNoDecision(self.computer(agent, tabId=101, **self.click("ref_66")))
            self.read('button "Next" [ref_66] type="button"\n', tab=101, agent=agent)  # reset for the next caller

    def test_typed_text_needs_a_clicked_text_field_and_tab_moves_the_focus_away(self):
        for agent in (None, "Explore"):
            self.read(agent=agent)
            self.assertDenied(self.computer(agent, action="type", text="abc"), "text field")
            self.assertNoDecision(self.computer(agent, **self.click("ref_5")))
            self.assertNoDecision(self.computer(agent, action="type", text="abc"))
            self.assertNoDecision(self.computer(agent, action="type", text="def"))
            self.assertNoDecision(self.computer(agent, action="key", text="Tab"))
            for text in (" ", "\t", "x ", "\n"):
                self.assertDenied(self.computer(agent, action="type", text=text), "")
            # a pressable click and a navigation take the focus too
            self.read(agent=agent)
            self.assertNoDecision(self.computer(agent, **self.click("ref_5")))
            self.assertNoDecision(self.computer(agent, **self.click("ref_1")))
            self.assertDenied(self.computer(agent, action="type", text=" "), "text field")
            # the same holds in a batch
            self.read(agent=agent)
            self.assertDenied(self.batch(("computer", self.click("ref_5")), ("computer", {"action": "key", "text": "Tab"}),
                                         ("computer", {"action": "type", "text": " "}), agent=agent), "text field")

    def test_a_link_click_is_pressable(self):
        self.read()
        self.assertNoDecision(self.computer(None, **self.click("ref_6")))
        self.assertDenied(self.computer(None, **self.click("ref_1")), "read the page")


class TestFillLog(T.Base):
    """B1: every filled value is logged to guard.log (run folder only) with its label."""

    def fills(self):
        lines = (self.run_dir() / "guard.log").read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines if '"event": "fill"' in x]

    def test_form_input_and_type_log_a_fill(self):
        self.aa_json("start")
        (folder, tab, _brief), _other = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1"))
        self.page(tab, "constructed_form_page.txt")
        self.assertExplicitAllow(self.guard(C + "form_input", {"tabId": tab, "ref": "ref_41", "value": "Alex"}, W, "A1"))
        self.assertExplicitAllow(self.click(tab, "ref_42", "A1"))
        self.assertExplicitAllow(self.guard(C + "computer", {"tabId": tab, "action": "type", "text": "alex@example.com"}, W, "A1"))
        got = [(f["folder"], f["tab"], f["label"], f["value"]) for f in self.fills()]
        self.assertEqual(got, [(folder, tab, "First name", "Alex"), (folder, tab, "Email", "alex@example.com")])


class TestFillLabels(T.Base):
    """The label of a fill is the question text of the field, not its shared accessible name (placeholder)."""

    FULL = ('form [ref_1]\n label [ref_2]\n  generic "Are you authorized to work here?" [ref_3]\n status [ref_4]\n'
            ' combobox "Select" [ref_5] type="text" placeholder="Select"\n'
            ' label [ref_6]\n  generic "Gender" [ref_7]\n status [ref_8]\n'
            ' combobox "Select" [ref_9] type="text" placeholder="Select"\n'
            ' label [ref_10]\n  generic "Military service?" [ref_11]\n status [ref_12]\n'
            ' combobox "Select" [ref_13] type="text" placeholder="Select"\n'
            ' textbox "Nickname" [ref_14] type="text"\n')
    INTERACTIVE = ('combobox "Select" [ref_5] type="text" placeholder="Select"\ncombobox "Select" [ref_9] type="text" '
                   'placeholder="Select"\ncombobox "Select" [ref_13] type="text" placeholder="Select"\n'
                   'textbox "Nickname" [ref_14] type="text"\noption "No" [ref_20]\n')

    def setUp(self):
        super().setUp()
        self.aa_json("start")
        (self.folder, self.tab, _brief), _other = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": self.tab, "url": "https://careers.example.com/a"}, W, "A1"))

    def fill(self, ref, value):
        self.assertExplicitAllow(self.guard(C + "form_input", {"tabId": self.tab, "ref": ref, "value": value}, W, "A1"))

    def pairs(self):
        import aa
        return aa.job_fills(self.run_dir(), self.folder, self.tab)

    def test_shared_names_keep_their_questions_and_a_refill_replaces(self):
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.FULL, W, "A1")
        self.post("read_page", {"tabId": self.tab, "filter": "interactive"}, self.INTERACTIVE, W, "A1")  # no label lines
        for ref, value in (("ref_5", "Yes"), ("ref_9", "Prefer not to say"), ("ref_13", "No"), ("ref_5", "No")):
            self.fill(ref, value)
        self.assertEqual([p[:2] for p in self.pairs()], [("Are you authorized to work here?", "No"),
                                                         ("Gender", "Prefer not to say"), ("Military service?", "No")])
        self.assertFalse(any(p[2] for p in self.pairs()))

    def test_a_value_in_a_field_name_never_becomes_the_label(self):
        self.post("read_page", {"tabId": self.tab, "filter": "interactive"},
                  'textbox "[req] Nickname = Alexander" [ref_14] type="text"\n', W, "A1")
        self.fill("ref_14", "Alexander")
        self.assertEqual(self.pairs(), [("Nickname", "Alexander", True)])
        self.assertNotIn("Alexander", (self.run_dir() / "guard.log").read_text().replace('"value": "Alexander"', ""))

    def test_an_option_click_is_a_fill_of_the_clicked_dropdown(self):
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.FULL, W, "A1")
        self.post("read_page", {"tabId": self.tab, "filter": "interactive"}, self.INTERACTIVE, W, "A1")
        self.assertExplicitAllow(self.click(self.tab, "ref_9", "A1", "Opens the list"))
        self.assertExplicitAllow(self.click(self.tab, "ref_20", "A1", "Selects No"))
        self.assertEqual([p[:2] for p in self.pairs()], [("Gender", "No")])

    def test_a_field_without_question_text_falls_back_to_its_name(self):
        import aa
        from unittest import mock
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.FULL, W, "A1")
        self.fill("ref_14", "Al")
        self.assertEqual(self.pairs(), [("Nickname", "Al", True)])
        with mock.patch.object(aa.J, "ask", return_value=({"verdict": "ok", "unsupported": []}, "")):
            check = aa.fills_check(self.run_dir(), "p", self.pairs())
        self.assertEqual(check["unlabelled"], 1)


    def test_same_ref_with_another_label_is_another_field(self):
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.FULL, W, "A1")
        self.fill("ref_5", "Yes")
        page2 = 'form [ref_1]\n label [ref_2]\n  generic "Second page question?" [ref_3]\n status [ref_4]\n combobox "Select" [ref_5] type="text"\n'
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, page2, W, "A1")
        self.fill("ref_5", "No")
        self.assertEqual([p[:2] for p in self.pairs()], [("Are you authorized to work here?", "Yes"), ("Second page question?", "No")])

    def test_a_reused_ref_with_another_name_gets_no_old_label(self):
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.FULL, W, "A1")
        self.post("read_page", {"tabId": self.tab, "filter": "interactive"}, 'textbox "Phone" [ref_5] type="text"\n', W, "A1")
        self.fill("ref_5", "123")
        self.assertEqual(self.pairs(), [("Phone", "123", True)])


class TestForeignPageRules(T.Base):
    """The worker's page rules hold for the main session and other agents too: a nameless submit button, an Apply under a
    form, an Apply on a job board; form_input and file_upload on fields only; every non-text click voids the refs."""

    def page(self, body, host="careers.example.com", tab=301):
        text = (body + f'\n\nTab Context:\n- Executed on tabId: {tab}\n- Available tabs:\n'
                f'  \u2022 tabId {tab}: "Page" ("https://{host}/job")\n')
        proc = self.hook("snapshot", {"tool_name": C + "read_page", "tool_input": {"tabId": tab},
                                      "tool_response": [{"type": "text", "text": text}]})
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def act(self, agent=None, tab=301, **ti):
        return self.guard(C + "computer", {"tabId": tab, **ti}, agent, "X1" if agent else None)

    def test_nameless_submit_apply_after_form_and_board_listing(self):
        for agent in (None, "Explore"):
            self.page('button [ref_1] type="submit"')
            self.assertDenied(self.act(agent, action="left_click", ref="ref_1"), "no name")
            form = ('textbox "First name" [ref_1]\ntextbox "Last name" [ref_2]\ntextbox "Email" [ref_3]\n'
                    'button "Apply" [ref_4] type="button"\nbutton "Next" [ref_5] type="button"')
            self.page(form)
            self.assertDenied(self.act(agent, action="left_click", ref="ref_4"), "application form")
            self.assertNoDecision(self.act(agent, action="left_click", ref="ref_5"))
            listing = ('button "Apply" [ref_1] type="button"\nlink "Apply" [ref_2] href="#"\n'
                       'link "Apply on company site" [ref_3] href="https://example.org/x"\nbutton "Save" [ref_4] type="button"')
            for host in ("www.naukri.com", "www.ziprecruiter.com", "de.indeed.com"):
                self.page(listing, host=host)
                self.assertDenied(self.act(agent, action="left_click", ref="ref_1"), "job board")
                self.page(listing, host=host)
                self.assertDenied(self.act(agent, action="left_click", ref="ref_2"), "job board")
                self.page(listing, host=host)
                self.assertNoDecision(self.act(agent, action="left_click", ref="ref_3"))  # a link with an address
                self.page(listing, host=host)
                self.assertNoDecision(self.act(agent, action="left_click", ref="ref_4"))
            # the employer's own page: an Apply that opens the form is fine
            self.page(listing, host="careers.example.com")
            self.assertNoDecision(self.act(agent, action="left_click", ref="ref_1"))

    def test_form_input_and_file_upload_are_for_fields_and_take_the_focus(self):
        body = ('textbox "Name" [ref_5]\nbutton "Submit" [ref_6] type="submit"\nbutton "Next" [ref_7] type="button"\n'
                'textbox [ref_8] type="file"\nbutton "Upload" [ref_9] type="button"\ncheckbox "Agree" [ref_10]')
        for agent in (None, "Explore"):
            who = {"agent_type": agent, "agent_id": "X1"} if agent else {}
            self.page(body)
            self.assertNoDecision(self.act(agent, action="left_click", ref="ref_5"))
            self.assertNoDecision(self.act(agent, action="type", text="abc"))
            self.page(body)  # typed text voids the refs
            proc = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "form_input",
                                       "tool_input": {"tabId": 301, "ref": "ref_6", "value": "x"}, **who})
            self.assertDenied(proc, "fields")
            proc = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "form_input",
                                       "tool_input": {"tabId": 301, "ref": "ref_99", "value": "x"}, **who})
            self.assertDenied(proc, "unknown ref")
            for ref, ok in (("ref_9", False), ("ref_5", False), ("ref_8", True)):
                proc = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "file_upload",
                                           "tool_input": {"tabId": 301, "ref": ref, "paths": ["/tmp/x.pdf"]}, **who})
                (self.assertNoDecision if ok else self.assertDenied)(proc)
            # click a field, form_input the Submit control (refused), but a form_input on a field takes the focus away
            self.page(body)
            self.assertNoDecision(self.act(agent, action="left_click", ref="ref_5"))
            proc = self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "form_input",
                                       "tool_input": {"tabId": 301, "ref": "ref_10", "value": True}, **who})
            self.assertNoDecision(proc)
            self.assertDenied(self.act(agent, action="type", text=" "), "text field")
            # in a batch as well
            self.page(body)
            batch = {"tabId": 301, "actions": [{"name": "computer", "input": {"tabId": 301, "action": "left_click", "ref": "ref_5"}},
                                               {"name": "form_input", "input": {"tabId": 301, "ref": "ref_6", "value": "x"}}]}
            self.assertDenied(self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "browser_batch",
                                                  "tool_input": batch, **who}), "fields")

    def test_a_box_or_option_click_voids_the_refs_like_any_other_click(self):
        for agent in (None, "Explore"):
            self.page('button "Next" [ref_1] type="button"\nradio "Yes" [ref_2]\ncheckbox "Agree" [ref_3]')
            self.assertNoDecision(self.act(agent, action="left_click", ref="ref_2"))
            self.assertDenied(self.act(agent, action="left_click", ref="ref_1"), "read the page")
            self.page('button "Next" [ref_1] type="button"\nradio "Yes" [ref_2]\ncheckbox "Agree" [ref_3]')
            self.assertDenied(self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": C + "browser_batch", "tool_input": {
                "actions": [{"name": "computer", "input": {"tabId": 301, "action": "left_click", "ref": "ref_2"}},
                            {"name": "computer", "input": {"tabId": 301, "action": "left_click", "ref": "ref_1"}}]},
                **({"agent_type": agent, "agent_id": "X1"} if agent else {})}), "read the page")

    def test_navigate_is_http_or_https_only(self):
        for agent in (None, "Explore"):
            for url in ["data:text/html,<script>alert(1)</script>", "file:///etc/passwd", "chrome://settings", "about:blank",
                        "javascript:alert(1)", "blob:https://x/1", "ftp://example.com/x", "https://example.com/\x00"]:
                self.assertDenied(self.guard(C + "navigate", {"tabId": 301, "url": url}, agent, "X1" if agent else None),
                                  "javascript" if url.startswith("javascript") else "http")
            for url in ["https://example.com/", "http://example.com", "example.com/jobs", "back", "forward"]:
                self.assertNoDecision(self.guard(C + "navigate", {"tabId": 301, "url": url}, agent, "X1" if agent else None))
        batch = {"actions": [{"name": "navigate", "input": {"tabId": 301, "url": "data:text/html,x"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch), "http")


class TestHiddenFieldFills(T.Base):
    """Issue 25: an "all" read lists fields no person can see; filling a honeypot flags the application as spam."""
    PAGE = (
        'form [ref_1]\n'
        ' textbox "First Name" [ref_41] type="text"\n'
        ' textbox "[hidden] Website" [ref_287] type="text"\n'
        ' textbox "[hidden] [req] Vis Hidden" [ref_288] type="text"\n'
        ' textbox "[hidden] Offscreen Field" [ref_289] type="text"\n'
        ' textbox "[value redacted]" [ref_290] type="hidden"\n'
        ' checkbox "I agree to the privacy notice" [ref_291]\n'
        ' checkbox "[hidden] Faked" [ref_292]\n'
        ' button "File upload" [ref_293] type="file"\n'
    )

    def setUp(self):
        super().setUp()
        self.aa_json("start")
        (self.folder, self.tab, _brief), _other = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": self.tab, "url": "https://careers.example.com/a"}, W, "A1"))
        self.post("read_page", {"tabId": self.tab, "filter": "all"}, self.PAGE, W, "A1")

    def fill(self, ref, value="x"):
        return self.guard(C + "form_input", {"tabId": self.tab, "ref": ref, "value": value}, W, "A1")

    def test_parse_refs_strips_the_mark_and_records_it(self):
        refs = L.parse_refs(self.PAGE)
        self.assertEqual(refs["ref_288"]["name"], "Vis Hidden")  # [hidden] then [req], both stripped
        self.assertIs(refs["ref_287"]["hidden"], True)
        self.assertNotIn("hidden", refs["ref_41"])

    def test_parse_refs_strips_the_icon_mark_and_records_it(self):
        # "[icon] " is not "[AA ": the control is a real one of the page, so the ledger must keep it (no `marker`)
        refs = L.parse_refs('button "[icon] Add another" [ref_1] type="button"\nbutton "Add" [ref_2]\n')
        self.assertEqual((refs["ref_1"]["name"], refs["ref_1"].get("icon"), refs["ref_1"].get("marker")),
                         ("Add another", True, None))
        self.assertNotIn("icon", refs["ref_2"])

    def test_the_icon_mark_is_recorded_wherever_it_stands(self):
        # MARKS_RE strips the marks in any order and number, so the flag is read from the marks that were stripped,
        # never from the first one. A dropped flag would let a guessed name pass for a name the site wrote, the one
        # direction that fails open. nameGoogleButtons runs before markFields today, so no field mark precedes an
        # "[icon] " name; the flag must not rest on that call order.
        refs = L.parse_refs('button "[hidden] [icon] Add" [ref_1] type="button"\n')
        self.assertEqual((refs["ref_1"]["name"], refs["ref_1"].get("icon")), ("Add", True))

    def test_a_hidden_text_field_is_refused(self):
        for ref in ("ref_287", "ref_288", "ref_289"):
            self.assertDenied(self.fill(ref), "hidden on the page")

    def test_a_type_hidden_input_is_refused_without_the_extension(self):
        self.assertDenied(self.fill("ref_290"), "hidden on the page")

    def test_a_fill_in_a_browser_batch_is_refused_too(self):
        batch = {"tabId": self.tab, "actions": [
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_41", "value": "x"}},
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_289", "value": "x"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "hidden on the page")

    def test_a_stale_fill_is_dropped_and_the_rest_of_the_batch_runs(self):
        batch = {"tabId": self.tab, "actions": [
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_41", "value": "x"}},
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_9999", "value": "x"}}]}
        proc = self.guard(C + "browser_batch", batch, W, "A1")
        self.assertExplicitAllow(proc)
        kept = json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"]["actions"]
        self.assertEqual([a["input"]["ref"] for a in kept], ["ref_41"])

    def test_a_batch_of_only_stale_fills_is_refused(self):
        batch = {"tabId": self.tab, "actions": [
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_9998", "value": "x"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "no longer on the page")

    def test_a_stale_click_still_denies_the_whole_batch(self):
        """A click says something about the page, so the fills beside it must not run on the old refs."""
        batch = {"tabId": self.tab, "actions": [
            {"name": "form_input", "input": {"tabId": self.tab, "ref": "ref_41", "value": "x"}},
            {"name": "computer", "input": {"tabId": self.tab, "action": "left_click", "ref": "ref_9997",
                                            "action_summary": "Opens the next step"}}]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "unknown ref")

    def test_a_visible_field_and_ticks_and_uploads_are_unchanged(self):
        self.assertExplicitAllow(self.fill("ref_41"))
        self.assertExplicitAllow(self.fill("ref_291", True))
        self.assertExplicitAllow(self.fill("ref_292", True))  # a tick role is never refused as hidden
        pdf = self.state()["jobs"][self.folder]["pdf"]
        self.assertAllowed(self.guard(C + "file_upload", {"tabId": self.tab, "ref": "ref_293", "paths": [pdf]}, W, "A1"))

    def test_the_non_agent_path_refuses_it_too(self):
        text = self.PAGE + '\n\nTab Context:\n- Executed on tabId: 301\n- Available tabs:\n  \u2022 tabId 301: "Page" ("https://careers.example.com/job")\n'
        self.hook("snapshot", {"tool_name": C + "read_page", "tool_input": {"tabId": 301, "filter": "all"},
                               "tool_response": [{"type": "text", "text": text}]})
        for ref in ("ref_287", "ref_290"):
            self.assertDenied(self.guard(C + "form_input", {"tabId": 301, "ref": ref, "value": "x"}), "hidden on the page")
        self.assertNoDecision(self.guard(C + "form_input", {"tabId": 301, "ref": "ref_41", "value": "x"}))
        self.assertNoDecision(self.guard(C + "form_input", {"tabId": 301, "ref": "ref_292", "value": True}))


class TestWorkerChoiceClick(T.Base):
    def test_a_button_after_a_box_or_option_needs_a_new_read(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.linkedin.com/jobs/view/1"}, W, "A1"))
        self.page(101, "constructed_form_page.txt")
        self.assertAllowed(self.click(101, "ref_43", "A1", "Ticks the privacy notice box"))  # a checkbox
        self.assertDenied(self.click(101, "ref_66", "A1", "Goes to the next step"), "since the last read_page")
        self.assertAllowed(self.click(101, "ref_52", "A1", "Chooses No"))  # another option is fine
        self.page(101, "constructed_form_page.txt")
        self.assertAllowed(self.click(101, "ref_66", "A1", "Goes to the next step"))
        # a part read does not count as a new look at the page
        self.page(101, "constructed_form_page.txt")
        self.assertAllowed(self.click(101, "ref_51", "A1", "Chooses Yes"))
        self.post("read_page", {"tabId": 101, "ref_id": "ref_40"}, 'radio "Yes" [ref_51]', W, "A1")
        self.assertDenied(self.click(101, "ref_66", "A1", "Goes to the next step"), "since the last read_page")


class TestJudgeCacheIsPerHost(T.Base):
    def test_an_apply_allowed_on_one_board_is_judged_again_on_another(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.ziprecruiter.com/c/x/job/y"}, W, "A1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 102, "url": "https://www.naukri.com/job-listings-x"}, W, "A2"))
        listing = 'button "Apply" [ref_2] type="button"\n\nTab Context:\n- tab'
        self.post("read_page", {"tabId": 101}, listing, W, "A1")
        self.post("read_page", {"tabId": 102}, listing, W, "A2")
        before = len(self.judge_calls())
        self.assertAllowed(self.click(101, "ref_2", "A1", "Opens the application"))
        self.assertEqual(len(self.judge_calls()), before + 1)
        self.assertAllowed(self.click(102, "ref_2", "A2", "Opens the application"))
        self.assertEqual(len(self.judge_calls()), before + 2)  # another host: asked again


class TestOneBriefPerAgent(T.Base):
    def test_an_agent_claims_one_brief_only(self):
        self.aa_json("start")
        (f1, _t1, b1), (f2, _t2, b2) = self.assign(101, 102)
        self.assertExplicitAllow(self.guard("Read", {"file_path": b1}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": b2}, W, "A1"), "own brief")  # not a second one
        self.assertExplicitAllow(self.guard("Read", {"file_path": b2}, W, "A2"))  # the other worker still gets its own
        self.assertExplicitAllow(self.guard("Read", {"file_path": b1}, W, "A1"))
        self.assertDenied(self.guard("Read", {"file_path": b1}, W, "A2"), "own brief")

    def test_a_job_that_next_has_not_handed_out_has_no_reader(self):
        self.aa_json("start")
        self.assertEqual(self.aa_json("assign", "--tabs", "101,102")["queued"], 2)
        brief = next((self.run_dir() / "jobs").iterdir()) / "brief.md"
        self.assertDenied(self.guard("Read", {"file_path": str(brief)}, W, "A1"), "own brief")  # no 'next' yet


class TestJobBoardApply(T.Base):
    """ZipRecruiter's 1-Click Apply and Naukri's Apply send the application on the listing itself."""

    def test_one_click_apply_is_final_and_a_plain_apply_on_a_board_is_judged(self):
        self.aa_json("start")
        self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://www.ziprecruiter.com/c/x/job/y"}, W, "A1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 102, "url": "https://careers.example.com/a"}, W, "A2"))
        listing = ('button "1-Click Apply" [ref_1] type="button"\nbutton "Apply" [ref_2] type="button"\n'
                   'button "Quick Apply" [ref_3] type="button"\nlink "Apply on company site" [ref_4] href="https://example.org/x"\n'
                   'button "Save" [ref_5] type="button"\n\nTab Context:\n- tab')
        self.post("read_page", {"tabId": 101}, listing, W, "A1")
        self.post("read_page", {"tabId": 102}, listing, W, "A2")
        self.assertDenied(self.click(101, "ref_1", "A1"), "final control")
        self.assertDenied(self.click(101, "ref_3", "A1"), "final control")
        before = len(self.judge_calls())
        self.assertAllowed(self.click(101, "ref_2", "A1", "Opens the application"))  # the fake judge allows it...
        self.assertEqual(len(self.judge_calls()), before + 1)  # ...but it was asked (a board host)
        self.assertEqual(self.judge_calls()[-1]["name"], "Apply")
        self.assertAllowed(self.click(101, "ref_4", "A1", "Opens the employer site"))  # a link with an address is no action
        # on the employer's own site, an Apply that opens the form is not judged
        self.assertAllowed(self.click(102, "ref_2", "A2", "Opens the application"))
        self.assertEqual(len(self.judge_calls()), before + 1)
        self.assertDenied(self.click(102, "ref_1", "A2"), "final control")


MARKER_LINE = "<!-- OHMYJOB-TEMPLATE: replace the mock data with yours, then delete this line -->\n"


class TestMockGuard(T.Base):
    """The template Profile.md (marker line first, # Candidate section) is mock data: no apply run starts on it."""

    def with_marker(self):
        path = self.root / "Profile.md"
        original = path.read_text(encoding="utf-8")
        path.write_text(MARKER_LINE + original, encoding="utf-8")
        return path, original

    def test_start_refuses_while_profile_has_the_marker(self):
        path, original = self.with_marker()
        try:
            for args in (("start",), ("start", "--continue")):
                proc = self.run_aa(*args)
                self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
                lines = proc.stdout.strip().splitlines()
                self.assertEqual(len(lines), 1, proc.stdout)
                self.assertTrue(lines[0].startswith("FATAL: ") and "Profile.md" in lines[0], lines[0])
                self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse((self.root / ".ohmyjob" / "run" / "apply" / "CURRENT").exists())  # no run was made
        finally:
            path.write_text(original, encoding="utf-8")
        self.assertEqual(self.aa_json("start")["count"], 2)  # the marker line deleted: the run starts

    def test_questions_names_and_prefix_work_with_the_marker_and_candidate_section(self):
        path, original = self.with_marker()
        try:
            self.assertEqual(self.aa_json("questions"), {"questions": []})
            code = "import aa; aa.add_questions(['Years of Rust?', 'Notice period?'], 'Acme | Dev')"
            subprocess.run([sys.executable, "-c", code], env=self.env, cwd=SCRIPTS, check=True)
            waiting = self.aa_json("questions")["questions"]
            self.assertEqual([q["q"] for q in waiting], ["Years of Rust?"])  # "Notice period?" is answered in the file
            self.assertEqual(self.aa_json("answer", "--json", json.dumps([{"q": "Years of Rust?", "a": "None."}]))["saved"], 1)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith(MARKER_LINE))
            self.assertNotIn("# Candidate", text)
            section = text[text.index("# Follow Up Questions"):]
            self.assertIn("11. Years of Rust? None.", section)
            self.assertEqual(text.replace("11. Years of Rust? None.\n", ""), MARKER_LINE + original)
            # the first name from "# Resume" is the file prefix of the files pj.py writes; the PDF to upload is any PDF
            code = ("import aa_lib as L, workspace as W; from pathlib import Path\n"
                    "print(W.file_prefix()); print(L.job_pdf(Path(sys.argv[1])).suffix, len(L.tailored_yamls(Path(sys.argv[1]))))")
            out = subprocess.run([sys.executable, "-c", "import sys\n" + code, str(self.root / "Applications" / LINKEDIN_JOB)],
                                 env=self.env, cwd=SCRIPTS, capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(out, ["Alex", ".pdf", "1"])
        finally:
            path.write_text(original, encoding="utf-8")
            (self.root / ".ohmyjob" / "questions.json").unlink(missing_ok=True)


class TestSlots(T.Base):
    def test_slots_are_the_subagent_cap_minus_one_whatever_the_config_says(self):
        code = "import aa_lib as L; print(L.SLOTS)"
        run = lambda env: subprocess.run([sys.executable, "-c", code], env=env, cwd=SCRIPTS, capture_output=True,  # noqa: E731
                                         text=True).stdout.strip()
        self.assertEqual(run(self.env), "49")  # the default cap of 50, less the master
        self.assertEqual(run({**self.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2"}), "1")
        (self.root / "ohmyjob.json").write_text("{not json")  # the config plays no part
        self.assertEqual(run(self.env), "49")
        (self.root / "ohmyjob.json").write_text('{"search": {"sources": ["linkedin"]}}')


class TestJudgeCommand(unittest.TestCase):
    def test_model_is_an_alias_and_path_is_tried_first(self):
        import judge

        self.assertEqual(judge.MODEL, "haiku")
        with tempfile.TemporaryDirectory() as d:
            path_bin, other_bin = Path(d) / "path" / "claude", Path(d) / "other" / "claude"
            for b in (path_bin, other_bin):
                b.parent.mkdir()
                b.write_text("#!/bin/sh\n")
                b.chmod(0o755)
            import os

            old = dict(os.environ)
            try:
                os.environ.pop("AA_JUDGE_CMD", None)
                os.environ["PATH"] = str(path_bin.parent)
                os.environ["AA_CLAUDE_BIN"] = str(other_bin)
                self.assertEqual(judge.claude_command(), [str(path_bin)])
                os.environ["PATH"] = "/nonexistent"
                self.assertEqual(judge.claude_command(), [str(other_bin)])
            finally:
                os.environ.clear()
                os.environ.update(old)


TAIL = '\n\nViewport: 1920x825\n\nTab Context:\n- Executed on tabId: 101\n- Available tabs:\n  • tabId 101: "Apply" ("%s")\n'
PAGE_NAV = ('dialog "Step 2" [ref_40]\n button "Back" [ref_71] type="button"\n button "Next" [ref_66] type="button"\n'
            ' button "Help" [ref_72] type="button"\n radio "Yes" [ref_51] type="radio"\n')


class TestClickGuardHoles(T.Base):
    """Holes of the worker click guard: a final control behind a consent word, a nameless button, a repeated click,
    a form_input answer, back/forward, a www hop, a child text, an OAuth page, other roles, focus, a sibling tab."""

    def boot(self, url="https://jobs.example.com/apply", filled=False):
        self.aa_json("start")
        (self.f1, self.t1, self.b1), (self.f2, self.t2, self.b2) = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": url}, W, "A1"))
        if filled:
            self.edit_tab(filled=True, filled_host="jobs.example.com")

    def test_a_final_word_in_a_picker_goes_to_the_judge_and_the_forms_submit_does_not(self):
        self.boot(filled=True)
        page = ('dialog "Apply to Example Co" [ref_9]\n'
                ' listbox "Choose a date" [ref_3]\n'
                '  button "Done" [ref_1] type="button"\n'
                ' button "Submit application" [ref_2] type="submit"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(page, "ref_1"))
        self.assertEqual(len(self.judge_calls()), before + 1)
        self.assertEqual(self.judge_calls()[-1]["name"], "Done")
        self.clear_watchdog()
        self.assertDenied(self.click(101, "ref_2", "A1", "Goes on"), "final control")

    def test_a_job_slug_with_authorize_does_not_refuse_every_click(self):
        url = "https://jobs.example.com/careers/authorize-solutions-engineer/123"
        self.boot(url=url)
        page = 'button "Next" [ref_1] type="button"\n'
        self.assertAllowed(self.try_click(page, "ref_1", url=url))

    def test_an_apply_word_in_a_picker_goes_to_the_judge(self):
        self.boot(filled=True)
        page = ('listbox "Pick a date" [ref_9]\n'
                ' button "Apply filters" [ref_1] type="button"\n'
                'button "Apply now" [ref_2] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(page, "ref_1"))
        self.assertEqual(len(self.judge_calls()), before + 1)
        self.assertEqual(self.judge_calls()[-1]["name"], "Apply filters")
        self.clear_watchdog()
        self.assertDenied(self.click(101, "ref_2", "A1", "Goes on"), "can send the application")

    def test_a_step_word_beside_an_apply_word_goes_to_the_judge(self):
        self.boot(filled=True)
        # both phrases are the ones issue 23 reports, "fortsetzen" included: it was missing from SAFE beside
        # "weiter" and "fortfahren", so the step word could not stand the apply rule down
        page = ('button "Continua la candidatura" [ref_1] type="button"\n'
                'button "Bewerbung fortsetzen" [ref_2] type="button"\n'
                'button "Invia candidatura" [ref_3] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(page, "ref_1"))
        self.clear_watchdog()
        self.assertAllowed(self.click(101, "ref_2", "A1", "Goes on"))
        self.assertEqual(len(self.judge_calls()), before + 2)
        self.clear_watchdog()
        # the fake judge blocks this name, and it carries no step word either
        self.assertDenied(self.click(101, "ref_3", "A1", "Goes on"), "final control")

    def test_a_sign_up_name_that_also_asks_to_apply_is_still_checked(self):
        """Before a fill an Apply word lets a click pass with no check. A name carrying a sign-up word as well must
        not ride on that pass: the hard sign-up refusal used to catch it, and the Haiku check has to now."""
        self.boot()  # unfilled tab, and jobs.example.com is no job board
        page = 'button "Create account to apply" [ref_1] type="button"\n'
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(page, "ref_1"), "Haiku check blocked")
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_sign_up_name_goes_to_the_judge_and_an_alert_sign_up_passes(self):
        self.boot(filled=True)
        page = ('button "Register interest" [ref_1] type="button"\n'
                'button "Sign up for alerts" [ref_2] type="button"\n'
                'button "Create an account" [ref_3] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(page, "ref_1"))
        self.clear_watchdog()
        self.assertAllowed(self.click(101, "ref_2", "A1", "Goes on"))
        self.clear_watchdog()
        self.assertDenied(self.click(101, "ref_3", "A1", "Goes on"), "Haiku check blocked")
        self.assertEqual(len(self.judge_calls()), before + 3)

    def test_a_link_with_an_address_in_a_picker_is_still_checked(self):
        """The widget rule excuses the word, never the control: a link with an address used to fall out of the
        judge test (it is excluded by `navigable_href`) and would have been allowed on its name alone."""
        self.boot(filled=True)
        page = ('listbox "Pick a date" [ref_9]\n'
                ' link "envoyer" [ref_1] href="https://jobs.example.com/x"\n')
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(page, "ref_1"), "Haiku check blocked")  # the fake judge blocks this name
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_delete_word_goes_to_the_judge_and_is_decided_by_the_container_it_sits_in(self):
        """Issue 45: "Remove" used to pass on its name (SAFE) while "Delete" was refused. Both now ask the model, and the
        same name gets its verdict from the container it sits in, not from the lines beside it: an upload area allows, a
        flat page with no container and a sparse sign-in page block."""
        self.boot(filled=True)
        upload = 'group "Resume" [ref_9]\n link "Resume.pdf" [ref_8]\n button "Remove" [ref_1] type="button"\n'
        # the same uploaded file beside the button, with no container naming it: the window can show "Resume.pdf" but
        # cannot say the button sits inside the upload area, so there is no evidence of containment and it blocks
        flat_upload = 'link "Resume.pdf" [ref_8]\nbutton "Remove" [ref_1] type="button"\n'
        signin = 'button "Delete my data" [ref_1] type="button"\n'
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(upload))
        self.assertEqual(self.judge_calls()[-1]["name"], "Remove")
        self.assertEqual(self.judge_calls()[-1]["inside"], "Resume")
        self.assertDenied(self.try_click(flat_upload), "Haiku check blocked")
        self.assertEqual(self.judge_calls()[-1]["inside"], "")
        self.assertDenied(self.try_click(signin), "Haiku check blocked")
        # no verdict by name is kept: the second and third click were asked, not answered from the first
        self.assertEqual(len(self.judge_calls()), before + 3)

    def test_a_delete_beside_a_form_field_is_refused_with_no_judge_call(self):
        """Issue 45, defect 1: the prompt cannot judge containment from a window of lines, so the structure decides.
        A field in the same container (or, on a page with no container, in the window) means the control removes an
        answer. The flat page is the one the worker really sees: filter=interactive flattens plain div nesting, so the
        ARIA group that indents "Education 2" is the lucky case, not the normal one."""
        self.boot(filled=True)
        flat = ('textbox "School" [ref_7] type="text"\ntextbox "Degree" [ref_6] type="text"\n'
                'button "Delete" [ref_1] type="button"\nlink "Resume.pdf" [ref_5]\n'
                'button "Remove" [ref_4] type="button"\n')
        group = ('group "Education 2" [ref_9]\n textbox "School" [ref_8] type="text"\n'
                 ' button "Remove" [ref_1] type="button"\n')
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(flat), "sits beside")
        self.assertDenied(self.try_click(group), "sits beside")
        self.assertEqual(len(self.judge_calls()), before)

    def test_a_delete_in_a_form_entry_with_no_field_line_still_blocks(self):
        """The structural rule is the floor, not the whole rule: an entry whose fields were never named (an empty
        textbox in the interactive view has no name of its own) reaches the model, which blocks it on the container."""
        self.boot(filled=True)
        page = ('group "Education 2" [ref_9]\n button "Edit" [ref_8] type="button"\n'
                ' button "Remove" [ref_1] type="button"\n')
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(page), "Haiku check blocked")
        self.assertEqual(self.judge_calls()[-1]["inside"], "Education 2")
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_delete_in_an_upload_area_inside_the_form_is_still_allowed(self):
        """The allow keys on the container that DIRECTLY encloses the control, so "Inside" has to show where that one
        starts: an upload area nested in a form named "Application form" must not read as the application itself.
        "Remove" is also the control that drops an uploaded file so a better resume can be attached, and that flow
        works on main; a rule that parks it buys nothing."""
        self.boot(filled=True)
        page = ('form "Application form" [ref_9]\n group "Resume" [ref_8]\n'
                '  link "Resume.pdf" [ref_7]\n  button "Remove" [ref_1] type="button"\n')
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click(page))
        self.assertEqual(self.judge_calls()[-1]["inside"], "Application form | Resume")
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_an_unlisted_delete_name_is_judged_by_capability_not_by_the_word_list(self):
        """Issue 45, defect 2: "Clear" is in no word list, so no hook rule fires for it. The prompt's delete rule is
        framed on what the click does, not on the control's name, so the model still blocks it on a form entry."""
        self.boot(filled=True)
        page = ('group "Employment 2" [ref_9]\n textbox "Employer" [ref_8] type="text"\n'
                ' button "Clear" [ref_1] type="button"\n')
        before = len(self.judge_calls())
        # the structural rule stands down as well: it fires on a delete word, and no list knows this name
        self.assertDenied(self.try_click(page), "Haiku check blocked")
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_delete_word_inside_the_control_counts_as_well_as_its_own_name(self):
        """Issue 45, defect 5: the final and apply words read the control's own name plus the text inside it; the
        delete and withdraw words read the name alone, so a button whose text sits in a child escaped both."""
        self.boot(filled=True)
        remove = ('group "Education 2" [ref_9]\n button "Options" [ref_1] type="button"\n'
                  '  generic "Remove education" [ref_2]\n')
        withdraw = 'button "Options" [ref_1] type="button"\n generic "Withdraw application" [ref_2]\n'
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(remove), "Haiku check blocked")
        self.assertDenied(self.try_click(withdraw), "withdraws")
        self.assertEqual(len(self.judge_calls()), before + 1)  # the withdraw word never reaches the model

    def test_a_delete_link_with_an_address_is_still_checked(self):
        """Issue 45, defect 3: a link with a navigable address is excluded from the role test by `navigable_href`, so
        only the `excused` flag sends a delete link to the model. Every other delete fixture is a button and would stay
        green with that flag gone."""
        self.boot(filled=True)
        entry = ('group "Education 2" [ref_9]\n textbox "School" [ref_8] type="text"\n'
                 ' link "Remove" [ref_1] href="/education/2/delete"\n')
        upload = ('group "Resume" [ref_9]\n link "Resume.pdf" [ref_8]\n'
                  ' link "Remove" [ref_1] href="/resume/1/delete"\n')
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(entry), "sits beside")
        self.assertAllowed(self.try_click(upload))  # asked, not allowed on its name: `excused` carries it there
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_delete_link_with_an_address_is_asked_and_never_allowed_on_its_name(self):
        """Pins the `bool(remove)` term of `excused`: `navigable_href` excludes a link with a real address from the
        role test, so without the flag a delete word on it fell out of the check allowed with no model call. The
        fake judge blocks a flat page's "Remove" (no container), so the verdict must come from the judge."""
        self.boot(filled=True)
        page = 'link "Resume.pdf" [ref_8]\nlink "Remove" [ref_1] href="/resume/1/delete"\n'
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(page, "ref_1"), "Haiku check blocked")
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_a_signup_name_in_a_picker_says_so_in_the_prompt(self):
        """Same as the delete case: `widget` was computed only for final, apply and delete words."""
        self.boot(filled=True)
        page = 'listbox "Alerts" [ref_9]\n button "Register interest" [ref_1] type="button"\n'
        self.try_click(page)
        self.assertEqual(self.judge_calls()[-1]["widget"], "yes")

    def test_a_delete_word_in_a_picker_says_so_in_the_prompt(self):
        """Issue 45, defect 6: the picker line was computed only for a final or apply word, so for a delete word it
        always read "no" without the hook ever looking. It is evidence the model reads, so it has to be true."""
        self.boot(filled=True)
        page = 'listbox "Chosen tags" [ref_9]\n button "Remove" [ref_1] type="button"\n'
        self.assertAllowed(self.try_click(page))
        self.assertEqual(self.judge_calls()[-1]["widget"], "yes")

    def test_a_long_delete_name_is_never_cached_by_name(self):
        """Issue 45, defect 4: the payload's name is cut at 300 characters while the routing reads the full one, so a
        312-character name ending in "Remove" routed as a delete word and then passed the cache guard's own word test.
        The decision travels in the payload now.

        Asserted on the store and not on a second click: the key `Ctx.judged_click` reads carries `widget` and the one
        `guard` writes does not, so nothing written here is ever found again. The guard starts mattering in practice
        when that key fix lands in PR 1; the condition is what this pins."""
        import judge as J  # noqa: E402  (scripts/ is on the path)
        self.boot(filled=True)
        name = "A" * 305 + " Remove"
        page = ('group "Resume" [ref_9]\n link "Resume.pdf" [ref_8]\n'
                ' button "%s" [ref_1] type="button"\n' % name)
        self.assertAllowed(self.try_click(page))
        import hooks  # noqa: E402
        short = hooks.short_click_payload({"name": name[:300], "role": "button", "filled": True, "host": "jobs.example.com"})
        key = J.key_for("click", short)
        self.assertFalse(J.cached(self.run_dir(), key))  # the run's cache, and the across-runs file J.cached also reads
        persistent = self.root / ".ohmyjob" / "judge_cache.json"
        self.assertFalse(persistent.exists() and key in json.loads(persistent.read_text())["entries"])

    def test_a_withdraw_word_is_refused_with_no_judge_call(self):
        self.boot(filled=True)
        before = len(self.judge_calls())
        for name in ("Withdraw application", "Zurückziehen", "撤回"):
            self.assertDenied(self.try_click('button "%s" [ref_1] type="button"\n' % name), "withdraws")
        self.assertEqual(len(self.judge_calls()), before)

    def test_a_delete_word_in_a_consent_dialog_stays_a_hard_refusal(self):
        self.boot()
        page = 'dialog "We use cookies" [ref_9]\n button "Remove" [ref_1] type="button"\n'
        before = len(self.judge_calls())
        self.assertDenied(self.try_click(page), "deletes")
        self.assertEqual(len(self.judge_calls()), before)

    def test_a_remove_word_cannot_ride_a_step_word_a_google_word_or_an_apply_pass(self):
        before = len(self.judge_calls())
        self.boot(filled=True)
        # "Remove" left SAFE, so it no longer excuses an apply word as a step word
        self.assertDenied(self.try_click('button "Remove and apply" [ref_1] type="button"\n'), "can send the application")
        self.boot()
        # unfilled tab: neither the Google pass nor the apply pass may let a delete word through unasked
        self.assertDenied(self.try_click('button "Delete Google account" [ref_1] type="button"\n'), "Haiku check blocked")
        self.assertDenied(self.try_click('button "Remove and apply" [ref_1] type="button"\n'), "Haiku check blocked")
        self.assertEqual(len(self.judge_calls()), before + 2)

    def test_the_prompt_shows_the_container_and_the_widget(self):
        import judge as J  # noqa: E402  (scripts/ is on the path)
        text = J.build_prompt("click", {"name": "Done", "context": "x", "host": "h", "widget": True,
                                        "container": "Education 2"})
        self.assertIn("In a picker or list: yes", text)
        self.assertIn('Inside: "Education 2"', text)
        self.assertNotIn("Edit, Remove", text)  # "Remove" is no allow word (issue 45)

    def test_the_prompt_blocks_a_delete_by_what_it_does_not_by_the_control_name(self):
        """Issue 45, defect 2: `main` blocked "delete or withdraw data" by capability, whatever the control was named.
        A rule gated on the name ("A control named Delete or Remove ...") leaves "Clear", "Discard" and "Reset" with no
        rule at all, and a withdraw synonym outside the word list ("Rescind") with neither a word refusal nor a rule."""
        import judge as J  # noqa: E402  (scripts/ is on the path)
        text = J.build_prompt("click", {"name": "Clear", "context": "x", "host": "h"})
        self.assertNotIn("named Delete or Remove", text)
        for word in ("delete", "remove", "clear", "discard", "reset", "erase", "trash", "withdraw"):
            self.assertIn(word, text.lower(), word)
        # an X that closes a dialog is Close and stays allowed: `main` allowed it, and a delete rule that swallows
        # every X would park a worker on closing a cookie banner
        self.assertIn("is Close, and allowed", text)
        # the hook's word lists know nothing of "Clear", and the rule still has to reach it
        self.assertIn("inside the control: no", text)
        self.assertIn("inside the control: yes", J.build_prompt("click", {"name": "Options", "delete": True}))

    def edit_tab(self, **fields):
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        state["tabs"]["101"].update(fields)
        path.write_text(json.dumps(state))

    def read(self, text, url="https://jobs.example.com/apply", mode="interactive"):
        self.clear_watchdog()
        self.post("read_page", {"tabId": 101, "filter": mode}, text + TAIL % url, W, "A1")

    def try_click(self, text, ref="ref_1", mode="interactive", **kw):
        self.read(text, mode=mode, **kw)
        self.clear_watchdog()
        return self.click(101, ref, "A1", "Goes on")

    def test_final_word_beside_a_consent_phrase(self):
        self.boot()
        for page in ['dialog "Privacy choices" [ref_9]\n button "Submit application" [ref_1]\n',
                     'form "Job alert: manage preferences" [ref_9]\n button "Send" [ref_1]\n',
                     'dialog "Apply" [ref_9]\n button "Submit and save my choices" [ref_1]\n',
                     'button "Submit application - we use cookies" [ref_1]\n']:
            self.assertDenied(self.try_click(page), "final control")

    def test_nameless_button_without_type_after_a_form(self):
        self.boot(filled=True)
        self.assertDenied(self.try_click("form [ref_9]\n button [ref_1]\n"), "submit button without a name")
        self.assertAllowed(self.try_click('form [ref_9]\n button [ref_1] type="button"\n'))

    def test_same_control_twice_with_another_click_between(self):
        self.boot()
        self.read(PAGE_NAV)
        self.assertAllowed(self.click(101, "ref_66", "A1"))
        self.assertAllowed(self.click(101, "ref_72", "A1"))
        self.assertDenied(self.click(101, "ref_66", "A1"), "clicked this control already")
        self.read(PAGE_NAV)
        batch = {"actions": [{"name": "computer", "input": {"tabId": 101, "action": "left_click", "ref": r,
                                                             "action_summary": "Goes on"}} for r in ("ref_66", "ref_72", "ref_66")]}
        self.assertDenied(self.guard(C + "browser_batch", batch, W, "A1"), "clicked this control already")

    def test_form_input_on_a_radio_needs_a_new_read(self):
        self.boot()
        self.read(PAGE_NAV)
        self.assertAllowed(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_51", "value": True}, W, "A1"))
        self.assertDenied(self.click(101, "ref_66", "A1"), "read_page first")

    def test_back_voids_the_page_state(self):
        self.boot()
        self.read(PAGE_NAV)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "back"}, W, "A1"))
        self.post("navigate", {"tabId": 101, "url": "back"}, "Navigated back" + TAIL % "https://www.google.com/x", W, "A1")
        self.assertEqual(self.state()["tab_urls"]["101"], "https://www.google.com/x")
        self.assertDenied(self.click(101, "ref_71", "A1"), "Unknown ref")

    def test_www_hop_keeps_filled(self):
        self.boot("https://www.careers.example.com/apply")
        self.read(sample("constructed_form_page.txt"), url="https://www.careers.example.com/apply")
        self.assertTrue(self.state()["tabs"]["101"]["filled"])
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://careers.example.com/apply"}, W, "A1"))
        self.assertDenied(self.try_click('main [ref_9]\n button "Apply now" [ref_1] type="button"\n',
                                         url="https://careers.example.com/apply"), "application form")

    # F2: a start-Apply link on a tab that only looks like a form (a job page with search boxes) goes through.
    APPLY_LINK = 'link "Apply now" [ref_1] href="/talentcommunity/apply/1?locale=en_GB"\n'
    OPENS = "Opens the Apply now page of the job"

    def apply_click(self, text, note=OPENS, url="https://jobs.example.com/apply", **kw):
        self.read(text, url=url, **kw)
        return self.click(101, "ref_1", "A1", note)

    def test_a_start_apply_link_passes_on_a_tab_with_no_answer(self):
        self.boot(filled=True)
        self.assertAllowed(self.apply_click(self.APPLY_LINK))
        self.assertTrue(any("apply-link-pass" in line for line in
                            (self.run_dir() / "guard.log").read_text(encoding="utf-8").splitlines()))

    def test_the_start_apply_link_is_denied_in_every_other_case(self):
        self.boot(filled=True)
        # a button, a "#" link and a javascript: link
        self.assertDenied(self.apply_click('button "Apply now" [ref_1] type="button"\n'), "application form")
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="#"\n'), "application form")
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="javascript:void(0)"\n'), "application form")
        # a board, as the current page and as the target
        self.assertDenied(self.apply_click(self.APPLY_LINK, url="https://www.indeed.com/viewjob"), "application form")
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="https://www.ziprecruiter.com/apply/1"\n'),
                          "application form")
        # a note that says it submits, and a link to this very page
        self.assertDenied(self.apply_click(self.APPLY_LINK, note="Submits the application"), "can send the application")
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="https://jobs.example.com/apply#top"\n'),
                          "application form")

    def test_the_start_apply_link_is_denied_after_an_answer(self):
        self.boot(filled=True)
        self.read('textbox "Cover note" [ref_2] type="text"\n' + self.APPLY_LINK)
        self.assertExplicitAllow(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_2", "value": "x"}, W, "A1"))
        self.assertTrue(self.state()["tabs"]["101"]["acted"])
        # the answer is there: the order to park comes back
        self.assertDenied(self.apply_click(self.APPLY_LINK), "status 'parked'")

    def test_a_consent_toggle_is_no_answer_but_a_tick_is(self):
        self.boot(filled=True)
        self.read('dialog "Cookie preferences" [ref_9]\n button "Analytics" [ref_3] type="checkbox"\n' + self.APPLY_LINK)
        self.assertAllowed(self.click(101, "ref_3", "A1", "Turns off the analytics cookies"))
        self.assertFalse(self.state()["tabs"]["101"].get("acted"))
        self.assertAllowed(self.apply_click(self.APPLY_LINK))
        self.boot(filled=True)
        self.read('checkbox "I can work in the UK" [ref_3]\n' + self.APPLY_LINK)
        self.assertAllowed(self.click(101, "ref_3", "A1", "Ticks the work right box"))
        self.assertTrue(self.state()["tabs"]["101"]["acted"])

    def test_the_order_to_park_needs_an_answer_on_the_tab(self):
        self.boot(filled=True)
        self.assertDenied(self.apply_click('button "Apply now" [ref_1] type="button"\n'), "Nothing was filled on this tab")
        self.assertDenied(self.apply_click('button "Submit application" [ref_1] type="button"\n'), "Nothing was filled")
        self.edit_tab(acted=True)
        self.assertDenied(self.apply_click('button "Apply now" [ref_1] type="button"\n'), "status 'parked'")

    # R1: every condition of the start-Apply exception, one test each
    def test_r1_an_applies_note_and_a_confirm_link_are_denied(self):
        self.boot(filled=True)
        confirm = 'link "Apply" [ref_1] href="/jobs/1/apply/confirm"\n'
        self.assertDenied(self.apply_click(confirm, note="Applies to this job"), "can send the application")
        self.assertAllowed(self.apply_click(confirm))  # a note that opens a page is the narrow pass

    def test_r1_the_name_must_hold_an_apply_word(self):
        self.boot(filled=True)
        self.assertDenied(self.apply_click('link "Continue" [ref_1] href="/next"\n', note="Applies to this job"),
                          "can send the application")

    def test_r1_a_sticky_tab_never_gets_the_exception_not_even_on_its_own_address(self):
        self.boot(filled=True)
        path = self.run_dir() / "state.json"
        state = json.loads(path.read_text())
        state["jobs"][self.f1]["apply_url"] = "https://jobs.example.com/apply"
        state["tabs"]["101"]["sticky"] = True
        path.write_text(json.dumps(state))
        self.assertDenied(self.apply_click(self.APPLY_LINK))

    def test_r1_a_backslash_address_is_read_as_the_browser_reads_it(self):
        self.boot(filled=True)
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="/\\www.indeed.com/x"\n'))

    def test_r1_the_target_page_must_differ_from_this_one(self):
        self.boot("https://jobs.example.com/apply/", filled=True)
        for href in ("?apply=1", "./?submit", "/apply", "/APPLY/", "#top"):
            self.assertDenied(self.apply_click(f'link "Apply now" [ref_1] href="{href}"\n',
                                               url="https://jobs.example.com/apply/"), "can send the application")
            self.clear_watchdog()

    def test_r1_the_wider_board_list_covers_the_exception_only(self):
        self.boot(filled=True)
        self.assertDenied(self.apply_click(self.APPLY_LINK, url="https://www.dice.com/job/1"))
        self.assertDenied(self.apply_click('link "Apply now" [ref_1] href="https://www.cwjobs.co.uk/a"\n'))

    def test_r1_a_page_with_a_file_input_is_an_application_form(self):
        self.boot(filled=True)
        page = 'button "Attach resume" [ref_5] type="file"\n' + self.APPLY_LINK
        self.assertDenied(self.apply_click(page))

    # R2: the child names of a nameless button reach the finish gate through the interactive read that ends a park
    def test_r2_a_later_interactive_read_keeps_the_child_names(self):
        self.boot()
        self.read('button [ref_7] type="submit"\n generic "Submit application" [ref_8]\n', mode="all")
        self.read('button [ref_7] type="submit"\n', mode="interactive")
        refs = L.snapshot_refs(self.run_dir(), 101)
        self.assertEqual(L.final_controls(refs, False, L.snapshot_text(self.run_dir(), 101)), ["Submit application"])
        self.edit_tab(acted=True)
        result = {"status": "parked", "final_button": "", "open": [], "filled": ["x"], "uploaded": None}
        self.assertEqual(L.check_result(self.run_dir(), self.state(), {"tab": 101}, result)[0], "PASS")

    def test_s1_the_child_names_are_not_carried_to_another_page(self):
        self.boot()
        self.read('button [ref_45] type="submit"\n generic "Send me jobs like this" [ref_46]\n', mode="all",
                  url="https://jobs.example.com/a")
        self.read('button [ref_45] type="submit"\n', url="https://jobs.example.com/b")
        refs = L.snapshot_refs(self.run_dir(), 101)
        self.assertNotIn("inside", refs["ref_45"])
        self.edit_tab(acted=True)
        result = {"status": "parked", "final_button": "", "open": [], "filled": ["x"], "uploaded": None}
        self.assertNotEqual(L.check_result(self.run_dir(), self.state(), {"tab": 101}, result)[0], "PASS")

    def test_s3_a_note_that_applies_and_also_opens_a_page_is_denied(self):
        self.boot(filled=True)
        confirm = 'link "Apply" [ref_1] href="/jobs/1/apply/confirm"\n'
        self.assertDenied(self.apply_click(confirm, note="Applies to this job and opens the confirmation page"),
                          "can send the application")
        self.assertDenied(self.apply_click(
            confirm, note="Opens the Apply page, applies with my profile and loads the confirmation page"),
            "can send the application")
        self.assertAllowed(self.apply_click(confirm, note="Opens the Apply now page of the job"))

    def test_s4_port_and_path_parameter_self_links_and_the_guardian_board(self):
        self.boot(filled=True)
        for href in ("https://jobs.example.com:443/apply", "/apply;jsessionid=ABC123"):
            self.assertDenied(self.apply_click(f'link "Apply now" [ref_1] href="{href}"\n'), "can send the application")
            self.clear_watchdog()
        self.assertDenied(self.apply_click(self.APPLY_LINK, url="https://jobs.theguardian.com/job/1"),
                          "can send the application")

    # R4: wall_seen
    def test_r4_wall_seen_needs_a_password_or_code_ask_and_stays_on_the_host(self):
        self.boot()
        self.read('link "Register" [ref_1] href="/r"\ngeneric "confirm your identity" [ref_2]\n')
        self.assertFalse(self.state()["tabs"]["101"].get("wall_seen"))
        self.read('heading "Confirm Your Identity" [ref_1]\n')
        self.assertTrue(self.state()["tabs"]["101"]["wall_seen"])
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://jobs.example.com/apply"}, W, "A1"))
        self.assertTrue(self.state()["tabs"]["101"]["wall_seen"])  # a reload keeps it
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://other.example.org/"}, W, "A1"))
        self.assertFalse(self.state()["tabs"]["101"]["wall_seen"])

    # R5
    def test_r5_a_radio_button_is_an_answer(self):
        self.boot()
        self.read('button "Yes" [ref_3] type="radio"\n')
        self.assertAllowed(self.click(101, "ref_3", "A1", "Picks Yes"))
        self.assertTrue(self.state()["tabs"]["101"]["acted"])

    def test_r5_the_nothing_filled_text_leaves_the_parked_way_open(self):
        self.boot(filled=True)
        self.assertDenied(self.apply_click('button "Apply now" [ref_1] type="button"\n'),
                          "filled up to this button, stop with status 'parked'")

    def test_final_word_in_a_child_of_a_named_button(self):
        self.boot()
        self.assertDenied(self.try_click('button "Review" [ref_1] type="submit"\n generic "and submit" [ref_2]\n', mode="all"),
                          "final control")
        self.assertDenied(self.try_click('button "Next" [ref_1] type="button"\n generic "Submit application" [ref_2]\n', mode="all"),
                          "final control")

    def test_oauth_page_refuses_every_non_choice_click(self):
        for url in ("https://appleid.apple.com/auth/authorize?client_id=x", "https://provider.example.com/oauth/authorize?x=1"):
            self.boot(url)
            for name in ("Continue", "Yes", "Approve", "Grant access"):
                self.assertDenied(self.try_click('button "%s" [ref_1]\n' % name, url=url), "sign-in permission")
                self.clear_watchdog()
        self.boot("https://jobs.example.com/apply?oauth_token=abc")
        self.assertAllowed(self.try_click('button "Next" [ref_1]\n'))

    def test_menuitem_goes_to_the_haiku_check(self):
        self.boot()
        before = len(self.judge_calls())
        self.try_click('menuitem "Soumission finale" [ref_1]\n')
        self.assertEqual(len(self.judge_calls()), before + 1)

    def test_focus_ref_does_not_survive_navigation(self):
        self.boot()
        self.read('dialog "Form" [ref_40]\n textbox "First name" [ref_41] type="text"\n')
        self.assertAllowed(self.click(101, "ref_41", "A1"))
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://jobs.example.com/b"}, W, "A1"))
        self.read('main [ref_1]\n textbox "Keyword" [ref_41] type="text"\n')
        self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "abc"}, W, "A1"),
                          "type only right after you click")

    def test_worker_with_a_brief_cannot_bind_a_sibling_tab(self):
        self.aa_json("start")
        (f1, t1, b1), (f2, t2, b2) = self.assign(101, 102)
        self.assertAllowed(self.guard("Read", {"file_path": b1}, W, "A1"))
        url = {"tabId": t2, "url": "https://careers.example.com/apply"}
        self.assertDenied(self.guard(C + "navigate", url, W, "A1"), "use only your own tab")
        self.assertAllowed(self.guard(C + "navigate", url, W, "A2"))

    def test_field_marks_are_stripped_and_text_and_choice_names_skip_the_word_rules(self):
        """The extension names fields "[x] Yes", "<label> = <value>", "<question> / <option>" (spec field-marks 5)."""
        self.boot()
        before = len(self.judge_calls())
        self.assertAllowed(self.try_click('button "[x] Yes" [ref_1] aria-pressed="true"\n'))  # SAFE after the strip: no judge
        self.assertEqual(len(self.judge_calls()), before)
        self.assertDenied(self.try_click('button "[disabled] Submit application" [ref_1]\n'), "final control")
        for filled in (False, True):
            self.edit_tab(filled=filled, filled_host="jobs.example.com")
            self.assertAllowed(self.try_click('radio "Do you want us to delete your data after 6 months? / Yes" [ref_1]\n'))
            self.assertAllowed(self.try_click('radio "How did you hear about us? / Google" [ref_1]\n'))
            self.assertAllowed(self.try_click('checkbox "Allow analytics cookies" [ref_1]\n'))
            self.assertAllowed(self.try_click('dialog "We use cookies" [ref_9]\n checkbox "[x] Allow analytics cookies" [ref_1]\n'))
            self.assertAllowed(self.try_click('textbox "[req] Confirm email = a@b.c" [ref_1]\n'))
            self.assertAllowed(self.try_click('textbox "Position you are applying for = Engineer" [ref_1]\n'))
        self.assertNotIn("jobs.example.com", self.state().get("signin_hosts", {}))  # "/ Google" took no lease

    def test_a_click_on_a_marker_of_the_extension_is_refused(self):
        self.boot()
        for name in ("[AA alert] File name is too long", "[AA frame]", "[AA frame: no address]"):
            page = f'link "{name}" [ref_1]\n'
            self.assertDenied(self.try_click(page), "marker of the helper extension")
            self.read(page)
            self.assertDenied(self.guard(C + "form_input", {"tabId": 101, "ref": "ref_1", "value": "x"}, W, "A1"), "marker")
            self.assertDenied(self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_1", "paths": ["/tmp/x.pdf"]}, W, "A1"), "marker")
            self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "hover", "ref": "ref_1"}, W, "A1"), "marker")
            # a caller that is not an ohmyjob agent: the same refusal
            self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "left_click", "ref": "ref_1"}), "marker")

    def test_an_option_keeps_the_delete_rule_and_a_text_click_on_an_oauth_page_is_refused(self):
        self.boot()
        self.assertDenied(self.try_click('listbox [ref_9]\n option "Withdraw application" [ref_1]\n'), "withdraws")
        url = "https://provider.example.com/oauth/authorize?x=1"
        self.boot(url)
        self.assertDenied(self.try_click('textbox "Email" [ref_1]\n', url=url), "sign-in permission")

    def test_an_option_with_a_final_word_is_allowed_on_a_filled_tab(self):
        self.boot(filled=True)
        for name in ("Acknowledge/Confirm", "Done"):
            self.assertAllowed(self.try_click('listbox [ref_9]\n option "%s" [ref_1]\n' % name))


if __name__ == "__main__":
    unittest.main()


class TestGuardLoosenings(T.Base):
    """The five narrow loosenings (issues 21, 24, 26, 27): each allowed only in its one shape, and every neighbour of
    that shape still refused."""

    def start(self):
        self.aa_json("start")
        (self.f1, self.t1, self.b1), (self.f2, self.t2, self.b2) = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": 101, "url": "https://careers.example.com/a"}, W, "A1"))

    def read(self, text, tab=101):
        self.post("read_page", {"tabId": tab, "filter": "interactive"}, text, W, "A1")

    def key(self, text):
        return self.guard(C + "computer", {"tabId": 101, "action": "key", "text": text}, W, "A1")

    def typed(self, text):
        return self.guard(C + "computer", {"tabId": 101, "action": "type", "text": text}, W, "A1")

    def log_events(self, event):
        lines = (self.run_dir() / "guard.log").read_text(encoding="utf-8").splitlines()
        return [e for e in (json.loads(line) for line in lines if line.strip()) if e["event"] == event]

    # ---- 1. Enter in a search list

    PAGE = ('combobox "School or University" [ref_5]\nsearchbox "Find" [ref_6]\ntextbox "First name" [ref_7] type="text"\n'
            'button "Next" [ref_8] type="button"\n')

    def test_enter_is_allowed_only_in_a_combobox_or_searchbox(self):
        self.start()
        self.read(self.PAGE)
        self.assertDenied(self.key("Enter"), "press only")  # nothing focused yet
        self.clear_watchdog()
        for ref in ("ref_5", "ref_6"):
            self.assertAllowed(self.click(101, ref, "A1", "Focuses the field"))
            for keys in ("Enter", "enter", "Return", " Enter "):
                self.assertExplicitAllow(self.key(keys))
            for keys in ("shift+Enter", "cmd+Enter", "NumpadEnter", "KP_Return", "space", "Enter Enter", "Enter Tab",
                         "\r", "\n"):
                self.assertDenied(self.key(keys), "press only")
                self.clear_watchdog()
        # a plain text box and a button keep the old refusal: Enter there submits a form or presses the button
        for ref in ("ref_7", "ref_8"):
            self.assertAllowed(self.click(101, ref, "A1", "Focuses the field"))
            self.assertDenied(self.key("Enter"), "press only")
            self.clear_watchdog()
            self.read(self.PAGE)
        # the rest of the allowlist is as before
        self.assertExplicitAllow(self.key("Tab"))

    def test_workday_search_list_labels_are_search_fields(self):
        for name in ("School or University", "Skills", "Search", "Filter results"):
            self.assertTrue(L.is_search_field({"role": "combobox", "name": name}), name)
        for name in ("First name", "School", "University attended"):
            self.assertFalse(L.is_search_field({"role": "textbox", "name": name}), name)

    # ---- 2. A second upload file

    def test_upload_takes_the_resume_or_one_file_of_its_folder(self):
        self.start()
        self.read(sample("constructed_form_page.txt"))
        pdf = Path(self.state()["jobs"][self.f1]["pdf"])
        folder = pdf.parent
        (folder / "Cover letter.pdf").write_bytes(b"%PDF-1.4 cover")
        (folder / "sub").mkdir(exist_ok=True)
        (folder / "sub" / "deep.pdf").write_bytes(b"%PDF-1.4 deep")
        outside = folder.parent.parent / "outside.pdf"
        outside.write_bytes(b"%PDF-1.4 outside")
        (folder / "link.pdf").symlink_to(outside)
        other = Path(self.state()["jobs"][self.f2]["pdf"])  # the other job's resume: another folder

        def upload(*paths):
            self.clear_watchdog()
            return self.guard(C + "file_upload", {"tabId": 101, "ref": "ref_45", "paths": list(paths)}, W, "A1")

        cover = str((folder / "Cover letter.pdf").resolve())
        self.assertExplicitAllow(upload(str(folder / "Cover letter.pdf")))
        proc = upload(str(folder / "sub" / ".." / "Cover letter.pdf"))  # rewritten to the resolved path
        self.assertExplicitAllow(proc)
        self.assertEqual(json.loads(proc.stdout)["hookSpecificOutput"]["updatedInput"],
                         {"tabId": 101, "ref": "ref_45", "paths": [cover]})
        self.assertExplicitAllow(upload(str(pdf)))
        for paths in ([str(folder / "missing.pdf")], [str(folder / "sub")], [str(folder / "sub" / "deep.pdf")],
                      [str(folder / "link.pdf")], [str(other)], [str(folder / ".." / other.parent.name / other.name)],
                      [str(folder / "..")], ["/etc/hosts"], [str(pdf), cover], [], [cover, cover]):
            self.assertDenied(upload(*paths), "Upload only this job's resume")
        self.assertDenied(upload(str(other)), "same folder")
        outside.unlink()

    # ---- 3. A line break in type

    BOXES = ('textarea "Cover letter" [ref_9]\ntextbox "About you" [ref_10] aria-multiline="true"\n'
             'textbox "Notes" [ref_11] aria-multiline="false"\ntextbox "First name" [ref_12] type="text"\n')

    def test_a_line_break_is_typed_only_into_a_multi_line_box(self):
        self.start()
        self.read(self.BOXES)
        for ref in ("ref_9", "ref_10"):
            self.assertAllowed(self.click(101, ref, "A1", "Focuses the field"))
            self.assertExplicitAllow(self.typed("line one\nline two"))
            for text in ("a\tb", "a\rb", "a\r\nb", "a\x1bb", "a\x7fb", "a\n\tb"):
                self.assertDenied(self.typed(text), "control characters")
                self.clear_watchdog()
            self.read(self.BOXES)
        for ref in ("ref_11", "ref_12"):
            self.assertAllowed(self.click(101, ref, "A1", "Focuses the field"))
            self.assertDenied(self.typed("a\nb"), "control characters")
            self.clear_watchdog()
            self.read(self.BOXES)

    # ---- 4. "Accept all" when the banner offers nothing else

    def consent(self, extra="", head='dialog "Cookie settings" [ref_300]\n'):
        self.read(head + ' button "Accept all" [ref_301] type="button"\n link "Privacy policy" [ref_302] href="/privacy"\n'
                  + extra)

    def test_accept_all_passes_only_when_the_dialog_offers_nothing_else(self):
        self.start()
        self.consent()
        self.assertExplicitAllow(self.click(101, "ref_301", "A1", "Accepts the cookies"))
        events = self.log_events("consent-dialog")
        self.assertEqual([(e["name"], e.get("only_choice")) for e in events], [("Accept all", True)])
        for extra in (' button "Reject all" [ref_303] type="button"\n', ' button "Only necessary" [ref_303]\n',
                      ' button "Decline" [ref_303]\n', ' button "Settings" [ref_303]\n', ' button [ref_303]\n',
                      ' link "Manage preferences" [ref_303] href="#"\n', ' link "Reject cookies" [ref_303] href="#"\n',
                      ' checkbox "Analytics" [ref_303] type="checkbox"\n', ' button "Ads" [ref_303] type="checkbox"\n',
                      ' switch "Marketing" [ref_303]\n', ' radio "Off" [ref_303] type="radio"\n'):
            self.consent(extra)
            self.assertDenied(self.click(101, "ref_301", "A1", "Accepts the cookies"), "most private choice")
            self.clear_watchdog()
        # no dialog around the control: nothing to scan, so the refusal stands
        self.read('button "Accept all cookies" [ref_301] type="button"\n')
        self.assertDenied(self.click(101, "ref_301", "A1", "Accepts the cookies"), "most private choice")
        self.clear_watchdog()
        # a final or delete word beside the consent phrase, or a permission word, keeps its refusal
        for name in ("Accept all and submit", "Accept all and delete my data"):
            self.read(f'dialog "Cookie settings" [ref_300]\n button "{name}" [ref_301] type="button"\n')
            self.assertDenied(self.click(101, "ref_301", "A1", "Accepts the cookies"))
            self.clear_watchdog()
        self.read('dialog "Cookie settings" [ref_300]\n button "Allow all" [ref_301] type="button"\n')
        self.assertDenied(self.click(101, "ref_301", "A1", "Allows the cookies"), "permission")

    # ---- 5. A failed Haiku check on an ordinary name

    def test_a_failed_check_lets_an_ordinary_name_through_and_logs_it(self):
        self.start()
        self.read('button "Fallisci" [ref_7] type="button"\n')
        self.assertExplicitAllow(self.click(101, "ref_7", "A1", "Goes on"))
        self.assertEqual([e["name"] for e in self.log_events("judge-fail-open")], ["Fallisci"])
        # the failure is not cached as a verdict: the check is asked again, fails again, the click goes through again
        self.read('button "Fallisci" [ref_7] type="button"\n')
        self.assertExplicitAllow(self.click(101, "ref_7", "A1", "Goes on"))
        # after MAX_FAILURES the hook stops asking and still lets it through, logging each time
        asked = len(self.judge_calls())
        self.read('button "Fallisci" [ref_7] type="button"\n')
        self.assertExplicitAllow(self.click(101, "ref_7", "A1", "Goes on"))
        self.assertEqual(len(self.judge_calls()), asked)
        self.assertEqual(len(self.log_events("judge-fail-open")), 3)

    def test_a_failed_check_still_refuses_a_name_with_a_dangerous_word(self):
        self.start()
        # a sign-up word is the dangerous word that reaches the check through the word rules
        self.read('button "Register fallisci" [ref_7] type="button"\n')
        self.assertDenied(self.click(101, "ref_7", "A1", "Goes on"), "could not check")
        self.clear_watchdog()
        # its own name is fine but the text inside carries a delete word: the same scan, the same refusal
        self.read('button "Fallisci" [ref_8] type="button"\n generic "Remove education" [ref_9]\n')
        self.assertDenied(self.click(101, "ref_8", "A1", "Goes on"))
        self.assertFalse(self.log_events("judge-fail-open"))
