"""The progress watchdog of application workers (spec 4.7): it replaced their 17-minute limit.

Progress is a new page, the first fill of a field, the first click on a choice control, an upload or a Google
sign-in step. A worker is stopped for a lack of progress, for the same denial repeated, for the call budget, and its
slot is freed when it ends or stays idle. The limits come from Worker_Limits.json in the workspace.
"""
from __future__ import annotations

import json
import re
import time
import unittest
from pathlib import Path

import test_aa as T
from test_aa import C, M, W

import aa_lib as L  # noqa: E402  (test_aa puts scripts/ on the path)
import hooks  # noqa: E402

PAGE = 'heading "Apply" [ref_1]\ntextbox "Name" [ref_2]\ncombobox "Country" [ref_3]\n'


class TestFingerprint(unittest.TestCase):
    """The page fingerprint: the host and path, the field names and the headings, and nothing else."""

    def test_open_options_do_not_change_it(self):
        url = "https://example.com/apply"
        more = PAGE + '  option "USA" [ref_4]\n  option "Canada" [ref_5]\n'
        self.assertEqual(L.page_fingerprint(PAGE, url), L.page_fingerprint(more, url))

    def test_a_listbox_or_a_menu_item_does_not_change_it(self):
        url = "https://example.com/apply"
        more = PAGE + 'listbox "choices" [ref_6]\nmenuitem "Delete" [ref_7]\n'
        self.assertEqual(L.page_fingerprint(PAGE, url), L.page_fingerprint(more, url))

    def test_a_query_change_does_not_change_it(self):
        self.assertEqual(L.page_fingerprint(PAGE, "https://example.com/apply?step=1"),
                         L.page_fingerprint(PAGE, "https://example.com/apply?step=2"))

    def test_a_new_step_is_a_new_fingerprint(self):
        step1 = 'heading "Contact Info" [ref_1]\ntextbox "Email" [ref_2]\n'
        step2 = 'heading "Experience" [ref_1]\ntextbox "Company" [ref_2]\ntextbox "Title" [ref_3]\n'
        url = "https://www.linkedin.com/jobs/view/1"
        self.assertNotEqual(L.page_fingerprint(step1, url), L.page_fingerprint(step2, url))

    def test_another_path_is_a_new_fingerprint(self):
        self.assertNotEqual(L.page_fingerprint(PAGE, "https://example.com/apply"),
                            L.page_fingerprint(PAGE, "https://example.com/apply/step2"))


class TestLimits(unittest.TestCase):
    """Worker_Limits.json: the owner's defaults, a bad value falls back, and the file is read on every call."""

    def setUp(self):
        self.path = Path(T.tempfile.mkdtemp(prefix="limits_")) / "Worker_Limits.json"
        self.orig = L.LIMITS_PATH
        L.LIMITS_PATH = self.path

    def tearDown(self):
        L.LIMITS_PATH = self.orig
        T.shutil.rmtree(self.path.parent, ignore_errors=True)

    def test_a_missing_file_gives_the_defaults(self):
        limits = L.worker_limits()
        self.assertEqual([limits[k] for k in ("stall_nudge_calls", "stall_stop_calls", "repeat_deny_stop",
                                              "call_budget", "idle_minutes")], [15, 25, 5, 150, 6])
        self.assertEqual(limits["_warnings"], [])

    def test_the_template_holds_the_defaults(self):
        template = json.loads((T.REPO / "templates" / "Worker_Limits.json").read_text())
        self.assertEqual(template, {k: v for k, v in L._LIMITS_DEFAULTS.items()})

    def test_a_bad_value_falls_back_and_warns(self):
        L.write_json(self.path, {"stall_nudge_calls": -1, "call_budget": "x", "idle_minutes": True})
        limits = L.worker_limits()
        self.assertEqual(limits["stall_nudge_calls"], 15)
        self.assertEqual(limits["idle_minutes"], 6)
        self.assertEqual(sorted(limits["_warnings"]), ["call_budget", "idle_minutes", "stall_nudge_calls"])

    def test_a_damaged_file_falls_back_for_every_key(self):
        self.path.write_text("{not json", encoding="utf-8")
        limits = L.worker_limits()
        self.assertEqual(limits["stall_stop_calls"], 25)
        self.assertEqual(sorted(limits["_warnings"]), sorted(L._LIMITS_DEFAULTS))

    def test_a_nudge_that_is_not_before_the_stop_falls_back(self):
        L.write_json(self.path, {"stall_nudge_calls": 20, "stall_stop_calls": 20})
        limits = L.worker_limits()
        self.assertEqual(limits["stall_nudge_calls"], 15)
        self.assertIn("stall_nudge_calls", limits["_warnings"])

    def test_the_budget_stays_under_the_agent_max_turns(self):
        L.write_json(self.path, {"call_budget": 10_000})
        limits = L.worker_limits()
        self.assertLessEqual(limits["call_budget"], L._agent_max_turns() - 10)
        self.assertIn("call_budget", limits["_warnings"])

    def test_max_turns_comes_from_the_agent_file(self):
        text = (T.REPO / "agents" / "application-worker.md").read_text(encoding="utf-8")
        self.assertIn("maxTurns: 160", text)
        self.assertEqual(L._agent_max_turns(), 160)

    def test_a_change_is_seen_on_the_next_call(self):
        L.write_json(self.path, {"stall_nudge_calls": 7})
        self.assertEqual(L.worker_limits()["stall_nudge_calls"], 7)
        L.write_json(self.path, {"stall_nudge_calls": 9})
        self.assertEqual(L.worker_limits()["stall_nudge_calls"], 9)

    def test_prep_workers_keep_their_time_limit(self):
        self.assertEqual((L.WORKER_SECONDS, L.STALE_SECONDS), (17 * 60, 20 * 60))


class TestCounters(unittest.TestCase):
    """record_stall / record_deny / record_progress on a watchdog dict: the nudge, the stops, the reset."""

    limits = {"stall_nudge_calls": 3, "stall_stop_calls": 5, "repeat_deny_stop": 3,
              "call_budget": 110, "idle_minutes": 5}

    def setUp(self):
        self.job: dict = {}
        self.wd = L.init_watchdog()

    def test_the_nudge_is_pending_once_at_its_limit(self):
        for _ in range(3):
            hooks.record_stall(self.wd, self.limits, self.job)
        self.assertTrue(self.wd["pending_nudge"])
        self.assertIsNone(self.job.get("stopped_by"))
        self.wd["nudged"], self.wd["pending_nudge"] = True, False  # the snapshot hook delivered it
        hooks.record_stall(self.wd, self.limits, self.job)
        self.assertFalse(self.wd["pending_nudge"])  # never a second nudge

    def test_the_stall_limit_stops_the_worker(self):
        for _ in range(5):
            hooks.record_stall(self.wd, self.limits, self.job)
        self.assertEqual((self.wd["stall"], self.job["stopped_by"]), (5, "stall"))

    def test_progress_resets_the_counters(self):
        for _ in range(4):
            hooks.record_stall(self.wd, self.limits, self.job)
        hooks.record_progress(self.wd)
        self.assertEqual((self.wd["stall"], self.wd["progress"], self.wd["max_stall"]), (0, 1, 4))
        self.assertFalse(self.wd["nudged"])

    def test_the_same_denial_three_times_stops_the_worker(self):
        for _ in range(3):
            hooks.record_deny(self.wd, "Click one control at a time.", self.limits, self.job, {}, "A1")
        self.assertEqual(self.job["stopped_by"], "repeat_deny")

    def test_another_denial_starts_the_count_again(self):
        hooks.record_deny(self.wd, "reason one", self.limits, self.job, {}, "A1")
        hooks.record_deny(self.wd, "reason one", self.limits, self.job, {}, "A1")
        hooks.record_deny(self.wd, "reason two", self.limits, self.job, {}, "A1")
        self.assertEqual((self.wd["deny_count"], self.job.get("stopped_by")), (1, None))

    def test_a_denial_is_a_stall_step_too(self):
        hooks.record_deny(self.wd, "reason one", self.limits, self.job, {}, "A1")
        self.assertEqual(self.wd["stall"], 1)

    def test_a_sign_in_lease_wait_counts_for_nothing(self):
        state = {"tabs": {"100": {"role": "job", "agent_id": "A1", "folder": "f"}},
                 "signin_hosts": {"example.com": {"holder": "200", "status": "pending", "since": time.time()}}}
        reason = "Tab 200 is signing in to example.com. Wait, then read the page again."
        for _ in range(24):
            hooks.record_deny(self.wd, reason, self.limits, self.job, state, "A1")
        self.assertEqual((self.wd["stall"], self.wd["deny_count"], self.job.get("stopped_by")), (0, 0, None))

    def test_the_worker_tab_comes_from_the_registry_key(self):
        """4.7 defect 1: a tab entry has no "tab" key; the tab id IS the key."""
        state = {"tabs": {"100": {"role": "job", "agent_id": "A1", "folder": "f"}},
                 "jobs": {"f": {"tab": 100}}}
        self.assertEqual(hooks.worker_tab(state, "A1"), "100")
        self.assertEqual(hooks.worker_job(state, "A1"), {"tab": 100})
        self.assertIsNone(hooks.worker_tab(state, "A2"))

    def test_every_stop_reason_has_its_own_message(self):
        for reason in ("stall", "repeat_deny", "budget", "idle", "ended"):
            text = hooks.stop_message(reason)
            self.assertIn("Make no more tool calls", text)
            self.assertIn("STALLED", text)


class RunBase(T.Base):
    """A run with one started worker. Each class gets its own workspace (T.Base builds it in setUpClass)."""

    def worker(self) -> tuple[str, int]:
        self.aa_json("start")
        self.master_setup()
        (folder, tab, _brief), *_rest = self.assign(101, 102)
        self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": "https://careers.example.com/a"}, W, "A1"))
        return folder, tab

    def limits(self, **values) -> None:
        L.write_json(self.root / "Worker_Limits.json", {**L._LIMITS_DEFAULTS, **values})

    def watchdog(self, folder: str) -> dict:
        return self.state()["jobs"][folder].get("watchdog") or {}


    def give_up_log(self) -> list[dict]:
        lines = (self.run_dir() / "guard.log").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines if '"give-up"' in line]

    def tearDown(self):
        L.write_json(self.root / "Worker_Limits.json", dict(L._LIMITS_DEFAULTS))
        if (self.root / ".ohmyjob" / "run" / "apply" / "CURRENT").exists():
            self.run_aa("end")


class TestWatchdogInTheRun(RunBase):
    """The watchdog in a real run: the nudge in the tool result, the stops, the freed slot."""

    def test_a_browser_call_counts_and_a_new_page_is_progress(self):
        folder, tab = self.worker()
        self.assertExplicitAllow(self.guard(C + "read_page", {"tabId": tab, "filter": "interactive"}, W, "A1"))
        self.assertEqual(self.watchdog(folder)["calls"], 1)  # the navigate bound the tab; this call is the first
        self.page(tab, "constructed_form_page.txt")
        self.assertEqual(self.watchdog(folder)["stall"], 0)
        self.assertEqual(self.watchdog(folder)["progress"], 1)
        self.page(tab, "constructed_form_page.txt")  # the same page again: no progress
        self.assertEqual(self.watchdog(folder)["stall"], 1)

    def test_a_read_counts_toward_the_budget(self):
        folder, _tab = self.worker()
        brief = self.run_dir() / "jobs" / folder / "brief.md"
        before = self.watchdog(folder).get("calls", 0)
        self.assertExplicitAllow(self.hook("guard", {"hook_event_name": "PreToolUse", "tool_name": "Read",
                                                     "tool_input": {"file_path": str(brief)},
                                                     "agent_type": W, "agent_id": "A1"}))
        self.assertEqual(self.watchdog(folder)["calls"], before + 1)

    def test_the_nudge_is_added_to_the_tool_result_once(self):
        self.limits(stall_nudge_calls=2, stall_stop_calls=9)
        folder, tab = self.worker()
        self.page(tab, "constructed_form_page.txt")      # progress: a new page
        self.page(tab, "constructed_form_page.txt")      # the same page: one stall step
        proc = self.post("read_page", {"tabId": tab, "filter": "interactive"},
                         T.sample("constructed_form_page.txt"), W, "A1")  # the second: the nudge
        self.assertIn("no progress in your last", proc.stdout)
        self.assertTrue(self.watchdog(folder)["nudged"])
        again = self.post("read_page", {"tabId": tab, "filter": "interactive"},
                          T.sample("constructed_form_page.txt"), W, "A1")
        self.assertNotIn("no progress in your last", again.stdout)

    def test_the_stall_limit_denies_the_next_call(self):
        self.limits(stall_nudge_calls=2, stall_stop_calls=3)
        folder, tab = self.worker()
        for _ in range(4):
            self.page(tab, "constructed_form_page.txt")
        self.assertEqual(self.state()["jobs"][folder]["stopped_by"], "stall")
        self.assertDenied(self.guard(C + "read_page", {"tabId": tab, "filter": "interactive"}, W, "A1"),
                          "no progress in your last calls")

    def test_the_call_budget_denies_the_next_call(self):
        self.limits(call_budget=2)
        folder, tab = self.worker()
        self.assertExplicitAllow(self.guard(C + "read_page", {"tabId": tab, "filter": "interactive"}, W, "A1"))
        self.assertDenied(self.guard(C + "read_page", {"tabId": tab, "filter": "interactive"}, W, "A1"),
                          "call budget")
        self.assertEqual(self.state()["jobs"][folder]["stopped_by"], "budget")

    def test_the_same_denial_repeated_stops_the_worker(self):
        self.limits(repeat_deny_stop=3)
        folder, tab = self.worker()
        for _ in range(2):
            self.assertDenied(self.guard(C + "computer", {"tabId": tab, "action": "key", "text": "Return"}, W, "A1"),
                              "press only")
        self.assertDenied(self.guard(C + "computer", {"tabId": tab, "action": "key", "text": "Return"}, W, "A1"),
                          "the same denial repeated")
        self.assertEqual(self.state()["jobs"][folder]["stopped_by"], "repeat_deny")

    def test_a_denied_call_keeps_its_counters_but_changes_nothing_else(self):
        folder, tab = self.worker()
        self.page(tab, "constructed_form_page.txt")
        before = self.state()["tabs"][str(tab)]
        self.assertDenied(self.guard(C + "computer", {"tabId": tab, "action": "key", "text": "Return"}, W, "A1"))
        after = self.state()
        self.assertEqual(after["tabs"][str(tab)], before)
        self.assertEqual(after["jobs"][folder]["watchdog"]["deny_count"], 1)

    def test_an_idle_worker_loses_its_slot_and_next_hands_the_job_on(self):
        self.limits(idle_minutes=1)
        folder, _tab = self.worker()
        self.set_job(folder, last_call_at=time.time() - 120)
        self.assertIn(folder, [w["folder"] for w in self.aa_json("next")["workers"]])
        entry = [e for e in self.give_up_log() if e["folder"] == folder]
        self.assertTrue(entry and "no tool call for 1 minutes" in entry[-1]["reason"], entry)

    def test_a_worker_that_ended_without_a_report_frees_its_slot(self):
        folder, _tab = self.worker()
        self.set_job(folder, ended_at=time.time())
        self.assertIn(folder, [w["folder"] for w in self.aa_json("next")["workers"]])
        entry = [e for e in self.give_up_log() if e["folder"] == folder]
        self.assertTrue(entry and "ended without a report" in entry[-1]["reason"], entry)

    def test_a_stopped_worker_is_denied_with_its_stop_message(self):
        folder, tab = self.worker()
        self.set_job(folder, stopped_by="idle")
        self.assertDenied(self.guard(C + "read_page", {"tabId": tab, "filter": "interactive"}, W, "A1"),
                          "idle too long")

    def test_a_late_report_of_an_idle_worker_is_still_accepted(self):
        folder, tab = self.worker()
        self.set_job(folder, stopped_by="idle")
        report = json.dumps({"folder": folder, "tab": tab, "status": "blocked", "where": "step 1",
                             "final_button": "", "url": "https://careers.example.com/a", "filled": [],
                             "uploaded": None, "drafted": [], "open": [], "questions": [], "declarations": [],
                             "blocker": "STALLED: nothing moved"})
        self.assertAllowed(self.handback(report))
        self.assertTrue((self.run_dir() / "jobs" / folder / "result.json").exists())

    def test_capture_sets_ended_at_only_when_the_worker_really_ends(self):
        """4.7 defect 3: a blocked stop (the report is corrected) is not the end of the worker."""
        folder, _tab = self.worker()
        proc = self.hook("capture", {"hook_event_name": "SubagentStop", "agent_type": W, "agent_id": "A1",
                                     "last_assistant_message": "I am done, no JSON here."})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("decision", proc.stdout)  # the stop was blocked
        self.assertIsNone(self.state()["jobs"][folder].get("ended_at"))

    def test_preflight_prints_the_limits(self):
        self.aa_json("start")
        line = self.master_setup()
        self.assertIn("OK limits: {", line)
        (self.root / "Worker_Limits.json").write_text("{nope", encoding="utf-8")
        self.assertIn("WARN limits:", self.aa("preflight", "--tab", "49"))


class TestRetryResetsTheSlot(RunBase):
    """4.7 defect 2: a job that goes to a second worker starts it clean."""

    def test_the_new_worker_starts_clean(self):
        """4.7 defect 2: ended_at, stopped_by, last_call_at and the counters of the last worker are dropped."""
        folder, tab = self.worker()
        job_dir = self.run_dir() / "jobs" / folder
        L.write_json(job_dir / "result.json", {"folder": folder, "tab": tab, "status": "blocked",
                                               "blocker": "UPLOAD_HANG: the upload never finished"})
        self.set_job(folder, ended_at=time.time(), stopped_by="ended", last_call_at=time.time(),
                     watchdog={**L.init_watchdog(), "calls": 99, "stall": 20})
        workers = self.aa_json("next")["workers"]
        self.assertIn(folder, [w["folder"] for w in workers])
        job = self.state()["jobs"][folder]
        for key in ("ended_at", "stopped_by", "last_call_at", "watchdog"):
            self.assertIsNone(job.get(key), key)

class TestRetryNameClash(RunBase):
    """Recommendation 6: the rename must not overwrite another resume of the job."""

    def test_a_name_clash_keeps_the_job_blocked(self):
        """Recommendation 6: two PDFs would be one file after the rename; the job stays blocked instead."""
        folder, tab = self.worker()
        pdf = Path(self.state()["jobs"][folder]["pdf"])
        short = pdf.parent / (f"Alex_{folder.split('_')[1]}.pdf")
        short.write_bytes(b"%PDF-1.4\n% another resume\n")
        L.write_json(self.run_dir() / "jobs" / folder / "result.json",
                     {"folder": folder, "tab": tab, "status": "blocked",
                      "blocker": "UPLOAD_HANG: the upload never finished"})
        self.aa_json("next")
        self.assertTrue(pdf.exists() and short.exists())
        self.assertIn("exists already", self.state()["jobs"][folder]["error"])


if __name__ == "__main__":
    unittest.main()
