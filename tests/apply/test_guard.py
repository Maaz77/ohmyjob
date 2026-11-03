"""Rules of hooks.py and aa.py that the ohmyjob plugin added to the old application assistant: explicit allows, the
master's Bash, who is a Chrome caller, the spoofing rule, the prep hand-off, the workspace lookup, the preflight for
runs without a LinkedIn job, the run-folder removal and the tracker stop."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
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
            proc = subprocess.run([sys.executable, str(HOOKS), "guard"], input=json.dumps({**payload, "cwd": str(outside)}),
                                  capture_output=True, text=True, env=self.env, cwd=outside)
            self.assertDenied(proc, "javascript")
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
        self.assertIn("- Years of Rust? None.", (self.root / "Profile.md").read_text())


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


class TestSlots(T.Base):
    def test_slots_follow_the_config_and_the_subagent_cap(self):
        code = "import aa_lib as L; print(L.SLOTS)"
        run = lambda env: subprocess.run([sys.executable, "-c", code], env=env, cwd=SCRIPTS, capture_output=True,  # noqa: E731
                                         text=True).stdout.strip()
        self.assertEqual(run(self.env), "3")  # the fixture sets max_parallel_workers: 3
        self.assertEqual(run({**self.env, "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "2"}), "1")
        # importing needs no valid config: only reading SLOTS does
        (self.root / "ohmyjob.json").write_text("{not json")
        code = "import aa_lib as L; print('imported'); L.SLOTS"
        proc = subprocess.run([sys.executable, "-c", code], env=self.env, cwd=SCRIPTS, capture_output=True, text=True)
        self.assertIn("imported", proc.stdout)
        self.assertNotEqual(proc.returncode, 0)


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
