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
from test_aa import ATS, C, HOOKS, INDEED_JOB, LINKEDIN_JOB, M, SCRIPTS, W

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
                        "ohmyjob apply questions", "ohmyjob apply answer --json '[]'", "ohmyjob prep start",
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
                self.assertEqual((out["pj"], out["tool"], out["ws"]), (event, tool, str(self.root)))

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
                      "open": [], "declarations": [], "blocker": "Account wall"}
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


class TestTokenTableColumns(unittest.TestCase):
    def test_scoped_names_fit_the_agent_column(self):
        import collections

        import aa_lib

        row = collections.Counter({"calls": 1, "input_tokens": 2, "output_tokens": 3})
        table = aa_lib.usage_table(None, None, {("ohmyjob:application-worker", "claude-haiku-5-5"): row, ("Haiku check", "haiku"): row})
        lines = table.splitlines()[1:]
        width = lines[0].index("model")
        for line in lines[1:]:
            self.assertEqual(line[width - 1], " ", line)  # nothing runs into the model column
        self.assertEqual(len({len(l) for l in lines}), 1, table)  # every row has the header's width


class TestSkillText(unittest.TestCase):
    def test_the_apostrophe_escape_is_written_out(self):
        for name in ("apply-jobs", "continue-applying"):
            text = (T.REPO / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn("apostrophe inside the JSON as " + chr(92) + "u0027", text)


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
        for keys in ["Tab", "shift+Tab", "Escape", "ctrl+a"]:
            self.assertExplicitAllow(self.key(keys))

    def test_typed_text_has_no_control_characters(self):
        self.start()
        self.page(101, "constructed_form_page.txt")
        self.assertAllowed(self.click(101, "ref_41", "A1", "Focuses the First name field"))
        for text in ["x\t ", "x\ty", "a\rb", "a\nb", "a\x1bb", "a\x7fb"]:
            self.assertDenied(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": text}, W, "A1"),
                              "control characters")
        self.assertExplicitAllow(self.guard(C + "computer", {"tabId": 101, "action": "type", "text": "Alex Example"}, W, "A1"))

    def test_addresses_with_control_characters_are_refused(self):
        self.start()
        for url in ["java\tscript:alert(1)", "javascript\n:alert(1)", "\x01javascript:alert(1)", "javascript:alert(1)",
                    "https://example.com/\x00x", "ht\ntps://example.com"]:
            self.assertDenied(self.guard(C + "navigate", {"tabId": 101, "url": url}, W, "A1"), "http")
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
        self.assertEqual(run(self.env), "19")  # the default cap of 20, less the master
        self.assertEqual(run({**self.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2"}), "1")
        (self.root / "ohmyjob.json").write_text("{not json")  # the config plays no part
        self.assertEqual(run(self.env), "19")
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


if __name__ == "__main__":
    unittest.main()
