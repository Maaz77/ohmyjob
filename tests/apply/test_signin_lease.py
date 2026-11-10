"""The sign-in lease (spec 4.8): one Google sign-in at a time per host.

Two workers that sign in to one host at the same time lose each other's session, so the second one waits and then
loads its page again. The lease lives in state["signin_hosts"][host] = {holder, since, status, hinted}.
"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

import test_aa as T
from test_aa import C, W

import aa_lib as L  # noqa: E402  (test_aa puts scripts/ on the path)
import hooks  # noqa: E402

hooks.load_modules()  # hooks.py imports its modules when it runs as a hook; these tests call its functions directly

HOST = "careers.example.com"
URL = f"https://{HOST}/apply"


class TestLeaseRules(unittest.TestCase):
    def setUp(self):
        self.run_dir = Path(tempfile.mkdtemp(prefix="lease_")) / "run"
        self.run_dir.mkdir(parents=True)

    def tearDown(self):
        T.shutil.rmtree(self.run_dir.parent, ignore_errors=True)

    def ctx(self, tab: int, agent: str, **lease):
        state = {
            "tabs": {"100": {"role": "job", "agent_id": "A1", "folder": "job1"},
                     "200": {"role": "job", "agent_id": "A2", "folder": "job2"}},
            "tab_urls": {"100": URL, "200": URL},
            "signin_hosts": {HOST: dict(lease)} if lease else {},
        }
        return hooks.Ctx(self.run_dir, state, agent), state

    def test_the_first_worker_takes_the_lease(self):
        ctx, state = self.ctx(100, "A1")
        hooks.check_signin_lease(ctx, 100)
        lease = state["signin_hosts"][HOST]
        self.assertEqual((lease["holder"], lease["status"]), ("100", "pending"))
        self.assertIsInstance(lease["since"], float)  # a timestamp, not a string to parse on every call

    def test_a_second_worker_waits(self):
        ctx, _state = self.ctx(200, "A2", holder="100", since=time.time(), status="pending", hinted=[])
        with self.assertRaises(hooks.Deny) as caught:
            hooks.check_signin_lease(ctx, 200)
        self.assertIn("Tab 100 is signing in", str(caught.exception))

    def test_the_holder_itself_goes_on(self):
        ctx, _state = self.ctx(100, "A1", holder="100", since=time.time(), status="pending", hinted=[])
        hooks.check_signin_lease(ctx, 100)  # no Deny

    def test_a_lease_that_is_too_old_is_taken_over(self):
        ctx, state = self.ctx(200, "A2", holder="100", since=time.time() - L.LEASE_SECONDS - 1,
                              status="pending", hinted=[])
        hooks.check_signin_lease(ctx, 200)
        self.assertEqual(state["signin_hosts"][HOST]["holder"], "200")

    def test_a_signed_in_host_gives_one_reload_hint(self):
        ctx, state = self.ctx(200, "A2", holder="100", since=time.time(), status="signed_in", hinted=[])
        with self.assertRaises(hooks.Deny) as caught:
            hooks.check_signin_lease(ctx, 200)
        self.assertIn("signed in already", str(caught.exception))
        self.assertIn("200", state["signin_hosts"][HOST]["hinted"])
        hooks.check_signin_lease(ctx, 200)  # the hint comes once; then the worker goes on

    def test_a_tab_without_a_known_host_is_left_alone(self):
        ctx, state = self.ctx(100, "A1")
        ctx.state["tab_urls"] = {}
        hooks.check_signin_lease(ctx, 100)
        self.assertEqual(state["signin_hosts"], {})


class TestSignInDone(unittest.TestCase):
    """observe_tabs sees that a sign-in finished: the pop-up tab is gone, or the job tab is back on the host."""

    def state(self) -> dict:
        return {
            "tabs": {"100": {"role": "job", "agent_id": "A1", "folder": "job1", "google_clicks": 1},
                     "150": {"role": "signin", "opener": 100, "agent_id": "A1", "google_clicks": 0}},
            "tab_urls": {"100": URL},
            "signin_hosts": {HOST: {"holder": "100", "since": time.time(), "status": "pending", "hinted": []}},
        }

    def test_the_sign_in_tab_is_gone(self):
        state = self.state()
        self.assertTrue(hooks.observe_tabs(state, {100: {"title": "Apply", "url": URL}}))
        self.assertEqual(state["signin_hosts"][HOST]["status"], "signed_in")

    def test_without_an_account_click_the_lease_stays_pending(self):
        state = self.state()
        state["tabs"]["100"]["google_clicks"] = 0
        hooks.observe_tabs(state, {100: {"title": "Apply", "url": URL}})
        self.assertEqual(state["signin_hosts"][HOST]["status"], "pending")

    def test_the_click_can_happen_in_the_sign_in_tab(self):
        state = self.state()
        state["tabs"]["100"]["google_clicks"] = 0
        state["tabs"]["150"]["google_clicks"] = 1
        hooks.observe_tabs(state, {100: {"title": "Apply", "url": URL}})
        self.assertEqual(state["signin_hosts"][HOST]["status"], "signed_in")


class TestGoogleTag(unittest.TestCase):
    """The helper extension 1.7.0 counts the Google controls of a job page in its title (spec 4.5)."""

    def test_the_tag_is_parsed(self):
        match = L.GOOGLE_TAG_RE.search("Operations Analyst | Acme [AA g=3 named=1]")
        self.assertEqual((match.group(1), match.group(2)), ("3", "1"))

    def test_the_counts_land_on_the_job_tab(self):
        state = {"tabs": {"100": {"role": "job", "folder": "job1"}}, "tab_urls": {}}
        hooks.observe_tabs(state, {100: {"title": "Apply [AA g=2 named=2]", "url": URL}})
        self.assertEqual((state["tabs"]["100"]["google_count"], state["tabs"]["100"]["google_named"]), (2, 2))

    def test_a_sign_in_tab_gets_no_counts(self):
        state = {"tabs": {"150": {"role": "signin", "opener": 100}}, "tab_urls": {}}
        hooks.observe_tabs(state, {150: {"title": "[AA from 100] Sign in", "url": URL}})
        self.assertNotIn("google_count", state["tabs"]["150"])


class TestLeaseInTheRun(T.Base):
    """The lease in a real run: the second worker is denied, and the denial is no fault of its own (4.7)."""

    def sign_in_run(self) -> tuple[str, int, str, int]:
        """A run with two workers on one host; the first one took the lease."""
        self.aa_json("start")
        self.master_setup()
        (folder, tab1, _b1), (folder2, tab2, _b2) = self.assign(101, 102)
        for tab, agent in ((tab1, "A1"), (tab2, "A2")):
            self.assertAllowed(self.guard(C + "navigate", {"tabId": tab, "url": URL}, W, agent))
            self.post("read_page", {"tabId": tab, "filter": "interactive"},
                      f'button "Continue with Google" [ref_3]\n\nTab Context:\n- tab {tab}', W, agent)
        self.assertAllowed(self.click(tab1, "ref_3", "A1", "Signs in with Google"))
        return folder, tab1, folder2, tab2

    def test_the_second_worker_waits_and_the_watchdog_ignores_it(self):
        _folder, tab1, folder2, tab2 = self.sign_in_run()
        lease = self.state()["signin_hosts"][HOST]
        self.assertEqual((lease["holder"], lease["status"]), (str(tab1), "pending"))
        proc = self.click(tab2, "ref_3", "A2", "Signs in with Google")
        self.assertDenied(proc, f"Tab {tab1} is signing in")
        # the wait is not the second worker's fault: it counts for neither stall nor repeated denial
        watchdog = self.state()["jobs"][folder2].get("watchdog") or {}
        self.assertEqual((watchdog.get("stall", 0), watchdog.get("deny_count", 0)), (0, 0))


class TestLeaseRelease(TestLeaseInTheRun):
    """Its own workspace: 'finish' ends the run, so the lease run above cannot share it."""

    def test_the_second_worker_waits_and_the_watchdog_ignores_it(self):
        pass  # runs in TestLeaseInTheRun

    def test_finish_frees_a_lease_of_a_worker_that_returned(self):
        self.sign_in_run()
        self.assertIn(HOST, self.state().get("signin_hosts", {}))
        self.aa("finish")
        self.assertEqual(self.state().get("signin_hosts"), {})


if __name__ == "__main__":
    unittest.main()
