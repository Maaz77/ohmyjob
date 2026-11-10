"""The ledger and the traces (spec 4.10) and the blocker codes (4.9).

`ohmyjob apply end` writes one line per job to .ohmyjob/traces/ledger.jsonl, plus a trace for the jobs worth
learning from. Nothing in a trace may hold a filled value, a drafted answer, an email address or a value of the
`# Resume` header of Profile.md.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import test_aa as T
from test_aa import C, M, W

import aa_lib as L  # noqa: E402  (test_aa puts scripts/ on the path)
import ledger  # noqa: E402


class TestBlockerCodes(unittest.TestCase):
    """A blocked report must start with a code, or be one of the two exact texts."""

    def report(self, blocker):
        return {"folder": "f", "tab": 1, "status": "blocked", "where": "step 1", "final_button": "", "url": "u",
                "filled": [], "uploaded": None, "drafted": [], "open": [], "questions": [], "declarations": [],
                "blocker": blocker}

    def test_a_coded_blocker_passes(self):
        for blocker in ("CLOSED: the posting is closed", "STALLED", "UPLOAD_HANG: the upload never finished",
                        "already submitted"):
            self.assertEqual(L.validate_result(self.report(blocker), "f", 1), [], blocker)
        # the questions blocker needs the questions themselves (unchanged rule)
        report = {**self.report(L.QUESTIONS_BLOCKER), "questions": ["How many years of Python?"]}
        self.assertEqual(L.validate_result(report, "f", 1), [])

    def test_free_text_is_refused(self):
        errors = L.validate_result(self.report("the site wanted an account"), "f", 1)
        self.assertTrue(errors and "must start with a code" in errors[0], errors)

    def test_the_worker_file_lists_every_code_a_worker_can_report(self):
        # GOOGLE_MISSED is a relabel the ledger makes, and TIMEOUT is only kept for the lines of older runs
        text = (T.REPO / "agents" / "application-worker.md").read_text(encoding="utf-8")
        for code in L.BLOCKER_CODES:
            if code in ("GOOGLE_MISSED", "TIMEOUT"):
                continue
            self.assertIn(code, text, code)


class TestClassifyBlocker(unittest.TestCase):
    def test_the_prefix_wins(self):
        self.assertEqual(ledger.classify_blocker("STALLED: no progress"), "STALLED")
        self.assertEqual(ledger.classify_blocker("TIMEOUT: ran out of time"), "TIMEOUT")
        self.assertEqual(ledger.classify_blocker("GOOGLE_MISSED: a Google button was there"), "GOOGLE_MISSED")

    def test_the_two_exact_texts(self):
        self.assertEqual(ledger.classify_blocker(L.QUESTIONS_BLOCKER), "QUESTIONS")
        self.assertEqual(ledger.classify_blocker("already submitted."), "ALREADY_SUBMITTED")

    def test_free_text_falls_back_to_its_words(self):
        self.assertEqual(ledger.classify_blocker("the job is closed"), "CLOSED")
        self.assertEqual(ledger.classify_blocker("it wanted a password"), "ACCOUNT_WALL")
        self.assertEqual(ledger.classify_blocker("a captcha"), "CAPTCHA")
        self.assertEqual(ledger.classify_blocker("the form is in an iframe"), "NOT_ACCESSIBLE")
        self.assertEqual(ledger.classify_blocker("the page would not load"), "LOAD_FAIL")
        self.assertEqual(ledger.classify_blocker("something else entirely"), "OTHER")


class TestClassifyOutcome(unittest.TestCase):
    def test_the_watchdog_stops_are_walls(self):
        for stopped_by in ("stall", "repeat_deny", "budget"):
            self.assertEqual(ledger.classify_outcome("BLOCKED", "STALLED", "STALLED: x", True, stopped_by), "wall")

    def test_a_worker_that_vanished_is_a_harness_failure(self):
        for stopped_by in ("idle", "ended"):
            self.assertEqual(ledger.classify_outcome("FAIL", "", "", False, stopped_by), "harness")
        self.assertEqual(ledger.classify_outcome("FAIL", "", "", False, None), "harness")
        self.assertEqual(ledger.classify_outcome("BLOCKED", "OTHER", "the hook refused my brief", True), "harness")
        self.assertEqual(ledger.classify_outcome("BLOCKED", "OTHER", "", True, judge_failure=True), "harness")

    def test_a_parked_job_is_parked_and_a_handoff_is_a_handoff(self):
        self.assertEqual(ledger.classify_outcome("PASS", "", "", True), "parked")
        for code in ("QUESTIONS", "CLOSED", "ALREADY_SUBMITTED"):
            self.assertEqual(ledger.classify_outcome("BLOCKED", code, "x", True), "handoff")

    def test_an_account_wall_that_the_page_does_not_show(self):
        self.assertEqual(ledger.classify_outcome("BLOCKED", "ACCOUNT_WALL", "x", True, account_wall_verified=True),
                         "handoff")
        self.assertEqual(ledger.classify_outcome("BLOCKED", "ACCOUNT_WALL", "x", True, account_wall_verified=False),
                         "unverified_handoff")


class TestClassifyBin(unittest.TestCase):
    def test_walls_and_harness_failures_get_a_trace(self):
        self.assertEqual(ledger.classify_bin("wall", "workday", 10, {"input": 1}, []), "wall")
        self.assertEqual(ledger.classify_bin("unverified_handoff", "workday", 10, {"input": 1}, []), "wall")
        self.assertEqual(ledger.classify_bin("harness", "workday", 10, {"input": 1}, []), "harness")
        self.assertEqual(ledger.classify_bin("handoff", "workday", 10, {"input": 1}, []), "skip")

    def test_a_win_on_a_known_ats_is_skipped(self):
        self.assertEqual(ledger.classify_bin("parked", "workday", 10, {"input": 1}, []), "skip")

    def test_the_first_win_on_an_unknown_ats_is_kept(self):
        self.assertEqual(ledger.classify_bin("parked", "jobs.example.com", 10, {"input": 1}, []), "first_win")

    def test_a_costly_win_is_kept(self):
        self.assertEqual(ledger.classify_bin("parked", "workday", 200, {"input": 1}, []), "costly_win")

    def test_the_median_sums_the_token_dicts(self):
        """A list of token dicts would raise TypeError in statistics.median."""
        lines = [{"ats": "workday", "outcome": "parked", "turns": 10, "tokens": {"input": 100, "output": 10}}] * 5
        self.assertEqual(ledger.classify_bin("parked", "workday", 10, {"input": 100, "output": 10}, lines), "skip")
        self.assertEqual(ledger.classify_bin("parked", "workday", 40, {"input": 100, "output": 10}, lines), "costly_win")


class TestRedaction(unittest.TestCase):
    def test_a_report_keeps_the_field_names_and_drops_the_values(self):
        out = ledger.redact_result({"filled": ["First name = Alex", "Email = a@b.c"], "drafted": [{"q": "Why?", "a": "Because"}],
                                    "open": ["Salary"], "url": "https://x/y", "secret": "nope"})
        self.assertEqual(out["filled"], ["First name", "Email"])
        self.assertEqual(out["drafted"], [{"q": "Why?"}])
        self.assertNotIn("secret", out)

    def test_an_email_address_never_reaches_a_trace(self):
        self.assertEqual(ledger.redact_text("write to alex@example.com now"), "write to <email> now")

    def test_a_profile_header_value_never_reaches_a_trace(self):
        profile = T.FIXTURE / "Profile.md"
        name = next((line.split(":", 1)[1].strip() for line in profile.read_text(encoding="utf-8").splitlines()
                     if line.strip().lower().startswith("- name:")), None)
        self.assertTrue(name, "the fixture Profile.md has a '- Name:' line")
        original, L.PROFILE = L.PROFILE, profile
        try:
            self.assertNotIn(name, ledger.redact_text(f"Signed in as {name}"))
        finally:
            L.PROFILE = original

    def test_the_page_tail_keeps_only_the_lines_that_explain_the_stop(self):
        page = "\n".join(["heading 'Jobs'", "link 'Home'", "button 'Continue with Google'", "text 'or'",
                          "textbox 'Password'", "link 'Imprint'"] + [f"filler {i}" for i in range(40)])
        tail = ledger.page_tail(page)
        self.assertIn("Continue with Google", tail)
        self.assertIn("Password", tail)
        self.assertNotIn("filler 30", tail)


class TestLedgerInTheRun(T.Base):
    """A whole run: 'end' writes the ledger before the run folder goes, and a trace for the blocked job."""

    def test_end_writes_the_ledger_and_a_trace(self):
        self.aa_json("start")
        self.master_setup()
        (folder, tab1, _b1), (folder2, tab2, _b2) = self.assign(101, 102)
        for tab, agent in ((tab1, "A1"), (tab2, "A2")):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, agent))
            self.post("read_page", {"tabId": tab, "filter": "interactive"},
                      f'textbox "First name" [ref_1]\nbutton "Submit application" [ref_9]\n\nTab Context:\n- tab {tab}',
                      W, agent)
        base = {"where": "step 1", "final_button": "", "url": "https://careers.example.com/a",
                "filled": ["First name = Alex"], "uploaded": None, "drafted": [{"q": "Why us?", "a": "Because"}],
                "open": [], "questions": [], "declarations": []}
        self.assertAllowed(self.handback(json.dumps({**base, "folder": folder, "tab": tab1, "status": "blocked",
                                                     "blocker": "ACCOUNT_WALL: it wants an account"}), agent_id="A1"))
        self.assertAllowed(self.handback(json.dumps({**base, "folder": folder2, "tab": tab2, "status": "parked",
                                                     "final_button": "Submit application", "blocker": None}),
                                         agent_id="A2"))
        self.aa("finish")
        out = self.aa("end")
        self.assertIn("Ledger: 2 lines", out)
        lines = [json.loads(line) for line in (self.root / ".ohmyjob" / "traces" / "ledger.jsonl")
                 .read_text(encoding="utf-8").splitlines()]
        by_folder = {line["folder"]: line for line in lines}
        self.assertEqual(by_folder[folder]["code"], "ACCOUNT_WALL")
        self.assertEqual(by_folder[folder]["host"], "careers.example.com")
        self.assertEqual(by_folder[folder2]["outcome"], "parked")
        # the blocked job gets a trace; nothing of the candidate is in it
        trace_name = by_folder[folder]["trace"]
        self.assertTrue(trace_name, by_folder[folder])
        trace = json.loads((self.root / ".ohmyjob" / "traces" / trace_name).read_text(encoding="utf-8"))
        self.assertEqual(trace["report"]["filled"], ["First name"])
        self.assertEqual(trace["report"]["drafted"], [{"q": "Why us?"}])
        self.assertNotIn("Because", json.dumps(trace))
        # the traces survive the run folder's removal
        self.assertTrue((self.root / ".ohmyjob" / "traces" / "ledger.jsonl").exists())
        self.assertFalse((self.root / ".ohmyjob" / "run" / "apply" / "CURRENT").exists())

    def test_a_damaged_judge_usage_line_does_not_stop_end(self):
        self.aa_json("start")
        run_dir = self.run_dir()
        (run_dir / "judge_usage.jsonl").write_text("{not json\n{\"model\": \"haiku\"}\n", encoding="utf-8")
        out = self.aa("end")
        self.assertIn("RUN FOLDER DELETED", out)


class TestAccountWallRelabel(T.Base):
    """4.9: an ACCOUNT_WALL on a page that had a Google button and no sign-in step becomes GOOGLE_MISSED."""

    def test_the_relabel(self):
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _brief), *_rest = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1"))
        self.post("read_page", {"tabId": tab, "filter": "interactive"},
                  f'button "Continue with Google" [ref_3]\n\nTab Context:\n- tab {tab}', W, "A1")
        self.assertAllowed(self.handback(json.dumps(
            {"folder": folder, "tab": tab, "status": "blocked", "where": "sign-in", "final_button": "",
             "url": "https://careers.example.com/a", "filled": [], "uploaded": None, "drafted": [], "open": [],
             "questions": [], "declarations": [], "blocker": "ACCOUNT_WALL: it wants an account"}), agent_id="A1"))
        self.aa("finish")
        self.aa("end")
        lines = [json.loads(line) for line in (self.root / ".ohmyjob" / "traces" / "ledger.jsonl")
                 .read_text(encoding="utf-8").splitlines()]
        self.assertEqual([line["code"] for line in lines if line["folder"] == folder], ["GOOGLE_MISSED"])


class TestUsageHelpers(unittest.TestCase):
    """agent_usage and the transcript helpers that the ledger needs."""

    def setUp(self):
        self.dir = Path(T.tempfile.mkdtemp(prefix="usage_"))
        session = self.dir / "sess.jsonl"
        session.write_text("", encoding="utf-8")
        subagents = self.dir / "sess" / "subagents"
        subagents.mkdir(parents=True)
        (subagents / "agent-m1.meta.json").write_text(json.dumps({"agentType": M}), encoding="utf-8")
        (subagents / "agent-m1.jsonl").write_text(json.dumps(
            {"type": "assistant", "message": {"id": "a", "model": "sonnet", "usage": {"input_tokens": 2}}}) + "\n",
            encoding="utf-8")
        (subagents / "agent-w1.meta.json").write_text(json.dumps(
            {"agentType": W, "parentAgentId": "m1", "description": "Apply: Acme"}), encoding="utf-8")
        (subagents / "agent-w1.jsonl").write_text("\n".join([
            json.dumps({"type": "user", "message": {"content": "folder=42_Acme_Role tab=101 brief=/x/brief.md"}}),
            json.dumps({"type": "assistant", "message": {"id": "b", "model": "haiku", "usage": {"output_tokens": 5}}}),
            json.dumps({"type": "assistant", "message": {"id": "b", "model": "haiku", "usage": {"output_tokens": 7}}}),
        ]) + "\n", encoding="utf-8")
        self.session = str(session)

    def tearDown(self):
        T.shutil.rmtree(self.dir, ignore_errors=True)

    def test_one_record_per_agent_with_the_largest_usage_of_each_call(self):
        records = {rec["agent_type"]: rec for rec in L.agent_usage(self.session, "m1")}
        self.assertEqual(sorted(records), sorted([M, W]))
        self.assertEqual(records[W]["usage"]["output_tokens"], 7)  # one call, written twice
        self.assertEqual(records[W]["usage"]["calls"], 1)

    def test_the_table_is_built_on_the_same_records(self):
        table = L.usage_table(self.session, "m1")
        self.assertIn("Token usage of this run", table)
        self.assertIn(W, table)
        self.assertRegex(table, r"TOTAL\s+2\s+2\s+2\s+0\s+0\s+7")

    def test_the_folder_comes_from_the_worker_prompt(self):
        self.assertEqual(L.folder_from_prompt("folder=42_Acme_Role tab=101"), "42_Acme_Role")
        self.assertIsNone(L.folder_from_prompt("nothing here"))
        self.assertEqual(L.worker_folder_from_transcript("w1", self.session, {"42_Acme_Role"}), "42_Acme_Role")
        self.assertIsNone(L.worker_folder_from_transcript("w1", self.session, {"other"}))


class TestSigninInfo(unittest.TestCase):
    """What the ledger says about a job's Google sign-in, read from guard.log (spec 4.8)."""

    def setUp(self):
        self.run_dir = Path(T.tempfile.mkdtemp(prefix="signin_"))

    def tearDown(self):
        T.shutil.rmtree(self.run_dir, ignore_errors=True)

    def log(self, *events: dict) -> None:
        with open(self.run_dir / "guard.log", "a", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event) + "\n")

    def test_without_a_log_there_is_nothing(self):
        self.assertIsNone(ledger.signin_info(self.run_dir, 100, {"tabs": {}}))

    def test_the_google_option_alone(self):
        self.log({"event": "google-option", "tab": 100, "host": "careers.example.com"})
        info = ledger.signin_info(self.run_dir, 100, {"tabs": {"100": {"role": "job"}}})
        self.assertEqual((info["step"], info["type"], info["host"]), ("option", "own", "careers.example.com"))

    def test_the_whole_flow_of_its_own_sign_in(self):
        self.log({"event": "google-option", "tab": 100, "host": "careers.example.com"},
                 {"event": "google-account", "tab": 150, "host": "accounts.google.com"},
                 {"event": "google-consent", "tab": 150, "host": "accounts.google.com"},
                 {"event": "signin-done", "tab": 100, "host": "careers.example.com"})
        state = {"tabs": {"100": {"role": "job"}, "150": {"role": "signin", "opener": 100}}}
        info = ledger.signin_info(self.run_dir, 100, state)
        self.assertEqual((info["step"], info["type"]), ("done", "own"))

    def test_a_worker_that_waited_for_another_tab(self):
        self.log({"event": "lease-wait", "tab": 200, "host": "careers.example.com"},
                 {"event": "google-option", "tab": 200, "host": "careers.example.com"})
        info = ledger.signin_info(self.run_dir, 200, {"tabs": {"200": {"role": "job"}}})
        self.assertEqual((info["step"], info["type"]), ("option", "waited"))

    def test_a_worker_that_used_another_tab_s_session(self):
        self.log({"event": "lease-wait", "tab": 200, "host": "careers.example.com"},
                 {"event": "lease-shared", "tab": 200, "host": "careers.example.com"},
                 {"event": "signin-done", "tab": 200, "host": "careers.example.com"})
        info = ledger.signin_info(self.run_dir, 200, {"tabs": {"200": {"role": "job"}}})
        self.assertEqual((info["step"], info["type"]), ("done", "shared"))

    def test_only_lease_events_are_not_a_sign_in(self):
        self.log({"event": "lease-wait", "tab": 200, "host": "careers.example.com"})
        self.assertIsNone(ledger.signin_info(self.run_dir, 200, {"tabs": {"200": {"role": "job"}}}))


class TestDreamHint(unittest.TestCase):
    """'end' suggests a dream once enough new traces are waiting."""

    def setUp(self):
        self.dir = Path(T.tempfile.mkdtemp(prefix="hint_"))
        self.orig = (ledger.LEDGER_PATH, ledger.DREAM_STATE)
        ledger.LEDGER_PATH = self.dir / "ledger.jsonl"
        ledger.DREAM_STATE = self.dir / "state.json"

    def tearDown(self):
        ledger.LEDGER_PATH, ledger.DREAM_STATE = self.orig
        T.shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, count: int) -> None:
        ledger.LEDGER_PATH.write_text("".join(json.dumps({"bin": "wall"}) + "\n" for _ in range(count)), encoding="utf-8")

    def test_no_hint_below_the_limit(self):
        self.write(ledger.DREAM_HINT_TRACES - 1)
        self.assertIsNone(ledger.dream_hint())

    def test_the_hint_names_the_command(self):
        self.write(ledger.DREAM_HINT_TRACES)
        self.assertIn("/ohmyjob:dream", ledger.dream_hint())

    def test_the_watermark_of_the_last_dream_counts(self):
        self.write(ledger.DREAM_HINT_TRACES)
        L.write_json(ledger.DREAM_STATE, {"watermark": 5})
        self.assertIsNone(ledger.dream_hint())

    def test_a_damaged_ledger_line_is_skipped(self):
        ledger.LEDGER_PATH.write_text('{"bin": "wall"}\n{not json\n', encoding="utf-8")
        self.assertEqual(len(ledger.read_ledger()), 1)


if __name__ == "__main__":
    unittest.main()
