"""Browser test of the helper extension (chrome-extension/) in headless Chromium.

It runs only where Playwright for Python and its Chromium are installed; else it is skipped.
Two local web servers stand for a job site (127.0.0.1:8001) and a sign-in site (127.0.0.1:8002).
"""
from __future__ import annotations

import functools
import http.server
import json
import socketserver
import tempfile
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
EXTENSION = HERE.parents[1] / "chrome-extension"
VERSION = json.loads((EXTENSION / "manifest.json").read_text())["version"]
ON = f"[AA on {VERSION}]"  # the tag of the tab that registered the run group; Chrome trims a trailing space
SITE = HERE / "extension_site"
# The job page is served as https://www.linkedin.com/feed; newer Chromium blocks its iframe on 127.0.0.1 without this.
NO_LNA = ["--disable-features=LocalNetworkAccessChecks"]

try:
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None



def _is_ours(worker) -> bool:
    return worker.url.startswith("chrome-extension://") and worker.url.endswith("/background.js")


def extension_worker(ctx):
    """The helper extension's own service worker. The browser can run other service workers too (built-in
    components), so taking the first one is flaky: their chrome.tabs is undefined."""
    for worker in ctx.service_workers:
        if _is_ours(worker):
            return worker
    return ctx.wait_for_event("serviceworker", predicate=_is_ours, timeout=15000)


def ROBOTS(route):
    """LinkedIn's robots.txt is a plain-text page, as the extension meets it (not an HTML page with scripts)."""
    route.fulfill(status=200, content_type="text/plain", body="User-agent: *\nDisallow: /\n")


# Threads: Chromium keeps idle pre-connected sockets, and they stall a one-thread server (page loads time out).
class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipIf(sync_playwright is None, "Playwright for Python is not installed")
class TestPopupsAsTabs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = []
        handler = functools.partial(Quiet, directory=str(SITE))
        try:
            for port in (8001, 8002):
                server = Server(("127.0.0.1", port), handler)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                cls.servers.append(server)
        except OSError as exc:
            cls.tearDownClass()
            raise unittest.SkipTest(f"ports 8001 and 8002 are not free ({exc})")

    @classmethod
    def tearDownClass(cls):
        for server in cls.servers:
            server.shutdown()
            server.server_close()

    def test_popups_in_the_run_group_open_as_tabs(self):
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    tempfile.mkdtemp(), channel="chromium", headless=True,
                    args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", *NO_LNA],
                )
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"Chromium cannot start here ({str(exc).splitlines()[0]})")
            try:
                sw = extension_worker(ctx)
                ev = sw.evaluate
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                gid = ev("async () => { const t = await chrome.tabs.query({}); "
                         "return await chrome.tabs.group({tabIds: t.map(x => x.id)}); }")
                # the extension takes the marker only from the LinkedIn feed or robots.txt
                ctx.route("https://www.linkedin.com/feed/**", lambda route: route.fulfill(path=SITE / "job.html"))
                page.goto("https://www.linkedin.com/feed/#aa-popups-as-tabs")
                time.sleep(0.8)
                self.assertTrue(page.title().startswith(ON), page.title())
                job_tab = ev(f"async () => (await chrome.tabs.query({{groupId: {gid}}})).map(t => t.id)")[0]

                # a pop-up of the page: a tab in the group, with its opener
                with ctx.expect_page() as info:
                    page.click("#pop")
                child = info.value
                child.wait_for_load_state()
                time.sleep(0.8)
                types = sorted(w["type"] for w in ev("async () => (await chrome.windows.getAll()).map(w => ({type: w.type}))"))
                self.assertEqual(types, ["normal"])
                self.assertEqual(ev(f"async () => (await chrome.tabs.query({{groupId: {gid}}})).length"), 2)
                self.assertTrue(child.title().startswith(f"[AA from {job_tab}] "), child.title())
                self.assertTrue(child.evaluate("!!window.opener"))
                self.assertTrue(any(m.startswith("token-from-signin") for m in page.evaluate("window.__msgs")))
                child.evaluate("window.close()")
                time.sleep(0.5)
                self.assertTrue(page.evaluate("window.__w.closed"))

                # a pop-up of a cross-origin frame (as the Google button does)
                with ctx.expect_page() as info:
                    page.frame_locator("#fr").locator("#g").click()
                child2 = info.value
                child2.wait_for_load_state()
                time.sleep(0.8)
                self.assertTrue(child2.title().startswith(f"[AA from {job_tab}] "), child2.title())
                types = sorted(w["type"] for w in ev("async () => (await chrome.windows.getAll()).map(w => ({type: w.type}))"))
                self.assertEqual(types, ["normal"])
                self.assertTrue(any("from=iframe" in m for m in page.frames[1].evaluate("window.__msgs")))
                child2.close()

                # outside the run group nothing changes
                other = ctx.new_page()
                other.goto("http://127.0.0.1:8001/job.html")
                time.sleep(0.5)
                with ctx.expect_page() as info:
                    other.click("#pop")
                info.value.wait_for_load_state()
                time.sleep(0.5)
                types = [w["type"] for w in ev("async () => (await chrome.windows.getAll()).map(w => ({type: w.type}))")]
                self.assertIn("popup", types)
                self.assertFalse(other.title().startswith("[AA"))
            finally:
                ctx.close()

    def test_popup_of_a_background_tab_names_its_real_opener(self):
        # Chrome's openerTabId is the tab that was active, not the tab that called window.open.
        # Workers click in background tabs, so pop-ups were tagged "[AA from <another tab>]".
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    tempfile.mkdtemp(), channel="chromium", headless=True,
                    args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", *NO_LNA],
                )
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"Chromium cannot start here ({str(exc).splitlines()[0]})")
            try:
                sw = extension_worker(ctx)
                ev = sw.evaluate
                ctx.route("https://www.linkedin.com/feed/**", lambda route: route.fulfill(path=SITE / "job.html"))
                home = ctx.pages[0] if ctx.pages else ctx.new_page()
                job = ctx.new_page()
                gid = ev("async () => { const t = await chrome.tabs.query({}); "
                         "return await chrome.tabs.group({tabIds: t.map(x => x.id)}); }")
                home.goto("https://www.linkedin.com/feed/#aa-popups-as-tabs")
                job.goto("http://127.0.0.1:8001/job.html")
                time.sleep(0.8)
                home_id, job_id = ev(f"async () => (await chrome.tabs.query({{groupId: {gid}}})).map(t => t.id)")
                ev(f"async () => chrome.tabs.update({home_id}, {{active: true}})")
                time.sleep(0.3)
                with ctx.expect_page() as info:
                    job.evaluate("document.querySelector('#pop').click()")
                child = info.value
                child.wait_for_load_state()
                time.sleep(1.5)
                self.assertTrue(child.title().startswith(f"[AA from {job_id}] "), child.title())
            finally:
                ctx.close()


    def test_robots_txt_marker_registers_the_group(self):
        # a run with no LinkedIn job registers on the public page, not on the feed
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    tempfile.mkdtemp(), channel="chromium", headless=True,
                    args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", *NO_LNA],
                )
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"Chromium cannot start here ({str(exc).splitlines()[0]})")
            try:
                sw = extension_worker(ctx)
                ev = sw.evaluate
                ctx.route("https://www.linkedin.com/robots.txt**", ROBOTS)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                ev("async () => { const t = await chrome.tabs.query({}); "
                   "return await chrome.tabs.group({tabIds: t.map(x => x.id)}); }")
                page.goto("https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
                time.sleep(0.8)
                self.assertTrue(page.title().startswith(ON), page.title())
            finally:
                ctx.close()

    def start_run(self, p, marker):
        """A browser with the extension, a run group registered by `marker` in the home tab, and one job tab in it."""
        try:
            ctx = p.chromium.launch_persistent_context(
                tempfile.mkdtemp(), channel="chromium", headless=True,
                args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", *NO_LNA],
            )
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Chromium cannot start here ({str(exc).splitlines()[0]})")
        sw = extension_worker(ctx)
        ctx.route("https://www.linkedin.com/robots.txt**", ROBOTS)
        home = ctx.pages[0] if ctx.pages else ctx.new_page()
        job = ctx.new_page()
        job.goto("http://127.0.0.1:8001/job.html")
        ids = sw.evaluate("async () => (await chrome.tabs.query({})).map(t => t.id)")
        gid = sw.evaluate(f"async () => await chrome.tabs.group({{tabIds: {ids}}})")
        home.goto(marker)
        time.sleep(0.8)
        self.assertTrue(home.title().startswith(ON), home.title())
        home_id, job_id = ids
        return ctx, sw, home, home_id, job_id, gid

    def test_group_page_moves_the_job_tabs_into_their_groups(self):
        # the master loads robots.txt?aa-groups#aa-groups=<plan> in its home tab (aa.py group-plan)
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                plan = quote(json.dumps({"Pending Review": [job_id]}, separators=(",", ":")), safe="")
                home.goto("https://www.linkedin.com/robots.txt?aa-groups#aa-groups=" + plan)
                time.sleep(1.0)
                self.assertTrue(home.title().startswith("[AA grouped 1] "), home.title())
                titles = sw.evaluate("async () => (await chrome.tabGroups.query({})).map(g => g.title)")
                self.assertIn("Pending Review", titles)
                moved = sw.evaluate(f"async () => (await chrome.tabs.get({job_id})).groupId")
                self.assertNotEqual(moved, gid)
                # a job page cannot ask for groups: only the owner tab of the run group can
                other = ctx.new_page()
                other.goto("http://127.0.0.1:8001/job.html")
                time.sleep(0.5)
                self.assertFalse(other.title().startswith("[AA grouped"))
            finally:
                ctx.close()

    def test_adopt_page_brings_the_needs_attention_tabs_back(self):
        # the home tab shows robots.txt#aa-popups-as-tabs; the adopt page differs from it before the '#', so Chrome loads it
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                parked = sw.evaluate(f"""async () => {{
                    const g = await chrome.tabs.group({{tabIds: [{job_id}], createProperties: {{}}}});
                    await chrome.tabGroups.update(g, {{title: "Needs Attention"}});
                    return g; }}""")
                self.assertNotEqual(parked, gid)
                plan = quote(json.dumps([job_id], separators=(",", ":")), safe="")
                home.goto("https://www.linkedin.com/robots.txt?aa-adopt#aa-adopt=" + plan)
                time.sleep(1.0)
                self.assertTrue(home.title().startswith("[AA adopted "), home.title())
                back = sw.evaluate(f"async () => (await chrome.tabs.get({job_id})).groupId")
                self.assertEqual(back, gid)
            finally:
                ctx.close()

    def test_icon_buttons_and_new_owner(self):
        with sync_playwright() as p:
            try:
                ctx = p.chromium.launch_persistent_context(
                    tempfile.mkdtemp(), channel="chromium", headless=True,
                    args=[f"--disable-extensions-except={EXTENSION}", f"--load-extension={EXTENSION}", *NO_LNA],
                )
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"Chromium cannot start here ({str(exc).splitlines()[0]})")
            try:
                sw = extension_worker(ctx)
                ev = sw.evaluate
                ctx.route("https://www.linkedin.com/feed/**", lambda route: route.fulfill(path=SITE / "job.html"))
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                gid = ev("async () => { const t = await chrome.tabs.query({}); "
                         "return await chrome.tabs.group({tabIds: t.map(x => x.id)}); }")
                page.goto("https://www.linkedin.com/feed/#aa-popups-as-tabs")
                time.sleep(0.8)
                self.assertTrue(page.title().startswith(ON), page.title())

                # icon-only Google buttons get a name, also when they come late; other buttons do not
                page.goto("http://127.0.0.1:8001/icons.html")
                time.sleep(1.0)
                names = page.evaluate("Object.fromEntries([...document.querySelectorAll('button')].map(b => [b.id, b.getAttribute('aria-label')]))")
                self.assertEqual(names, {"github": None, "google": "Continue with Google", "text": None, "late": "Continue with Google"})
                self.assertEqual(page.evaluate("document.querySelector('#google').closest('label').getAttribute('aria-label')"),
                                 "Continue with Google")

                # a new sign-in check page takes the group over: the home tab of a stopped run stays open in it
                new_home = ctx.new_page()
                new_id = ev("async () => (await chrome.tabs.query({})).map(t => t.id).sort((a, b) => b - a)[0]")
                ev(f"async () => chrome.tabs.group({{groupId: {gid}, tabIds: [{new_id}]}})")
                new_home.goto("https://www.linkedin.com/feed/#aa-popups-as-tabs")
                time.sleep(0.8)
                self.assertTrue(new_home.title().startswith(ON), new_home.title())
                owners = ev("async () => (await chrome.storage.session.get('aaRunOwners')).aaRunOwners")
                self.assertEqual(owners[str(gid)], new_id)
            finally:
                ctx.close()


if __name__ == "__main__":
    unittest.main()
