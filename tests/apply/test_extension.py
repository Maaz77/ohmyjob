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
import re
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
    from playwright.sync_api import TimeoutError as PlaywrightTimeout, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None
    PlaywrightTimeout = Exception



def _is_ours(worker) -> bool:
    return worker.url.startswith("chrome-extension://") and worker.url.endswith("/background.js")


def extension_worker(ctx):
    """The helper extension's own service worker. The browser can run other service workers too (built-in
    components), so taking the first one is flaky: their chrome.tabs is undefined."""
    for worker in ctx.service_workers:
        if _is_ours(worker):
            return worker
    # 60s tolerates a cold Chromium on a loaded CI runner (15s was exceeded in run 44837613885); a healthy
    # start returns as soon as the event fires, so the ceiling is only reached when no worker registers.
    try:
        return ctx.wait_for_event("serviceworker", predicate=_is_ours, timeout=60000)
    except PlaywrightTimeout as exc:
        # a worker that registered between the scan above and the listener is invisible to the event
        for worker in ctx.service_workers:
            if _is_ours(worker):
                return worker
        raise AssertionError("the extension's background.js service worker never registered within 60s") from exc


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
                self.assertTrue(home.title().startswith("[AA grouped 1]"), home.title())
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

                # Google buttons with no name of their own get one, also when they come late, and a button
                # whose text sits in a span counts as nameless (read_page shows no name for it); others do not
                page.goto("http://127.0.0.1:8001/icons.html")
                time.sleep(1.0)
                names = page.evaluate("Object.fromEntries([...document.querySelectorAll('button')].map(b => [b.id, b.getAttribute('aria-label')]))")
                self.assertEqual(names, {"github": None, "google": "Continue with Google", "text": None,
                                         "wrapped": "Continue with Google", "late": "Continue with Google"})
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


    def test_a_hand_added_tab_comes_back_too(self):
        """Helper 1.7.0: the adopt page moves every tab of every group "Needs Attention", not only the listed ids."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                extra = ctx.new_page()
                extra.goto("http://127.0.0.1:8001/other.html")
                extra_id = sw.evaluate("async () => (await chrome.tabs.query({})).map(t => t.id).sort((a, b) => b - a)[0]")
                parked = sw.evaluate(f"""async () => {{
                    const g = await chrome.tabs.group({{tabIds: [{job_id}, {extra_id}], createProperties: {{}}}});
                    await chrome.tabGroups.update(g, {{title: "Needs Attention"}});
                    return g; }}""")
                self.assertNotEqual(parked, gid)
                # the plan names the job tab only; the tab the candidate added comes back as well
                plan = quote(json.dumps([job_id], separators=(",", ":")), safe="")
                home.goto("https://www.linkedin.com/robots.txt?aa-adopt#aa-adopt=" + plan)
                time.sleep(1.2)
                self.assertTrue(home.title().startswith("[AA adopted 2]"), home.title())
                for tab in (job_id, extra_id):
                    self.assertEqual(sw.evaluate(f"async () => (await chrome.tabs.get({tab})).groupId"), gid)
            finally:
                ctx.close()

    def test_google_links_are_named_and_the_title_counts_them(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/google-link.html")
                time.sleep(1.0)
                names = job.evaluate("Object.fromEntries(['glink', 'gdiv', 'plain'].map("
                                     "id => [id, document.getElementById(id).getAttribute('aria-label')]))")
                self.assertEqual(names, {"glink": "Continue with Google", "gdiv": "Continue with Google", "plain": None})
                # two Google controls, both named by the helper itself
                self.assertRegex(job.title(), r"\[AA g=2 named=2 v=[0-9][\w.+-]*\]$")

                job.goto("http://127.0.0.1:8001/no-google.html")
                time.sleep(1.0)
                self.assertRegex(job.title(), r"\[AA g=0 named=0 v=[0-9][\w.+-]*\]$")

                # a store badge holds a Google image but is not a Google sign-in
                job.goto("http://127.0.0.1:8001/store-badge.html")
                time.sleep(1.0)
                names = job.evaluate("['badge', 'holder', 'badge2'].map("
                                     "id => document.getElementById(id).getAttribute('aria-label'))")
                self.assertEqual(names, [None, None, None])
                self.assertRegex(job.title(), r"\[AA g=0 named=0 v=[0-9][\w.+-]*\]$")
            finally:
                ctx.close()

    def test_the_real_aviva_sign_up_page(self):
        """aviva.talent-community.com holds the text of its social buttons in a span, so they reach read_page with no
        name. Only the Google one is named here: the LinkedIn button is not a Google control."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/aviva-signup.html")
                time.sleep(1.0)
                names = job.evaluate("Object.fromEntries([...document.querySelectorAll('button')].map("
                                     "b => [b.id, b.getAttribute('aria-label')]))")
                self.assertEqual(names, {"button-start-signup-with-linkedin": None,
                                         "button-start-signup-with-google": "Continue with Google",
                                         "button-submit-signup-with-email": "Register now"})
                # the button and the icon holder inside it, both named by the helper, as on the page itself
                self.assertRegex(job.title(), r"\[AA g=2 named=2 v=[0-9][\w.+-]*\]$")
            finally:
                ctx.close()

    def test_a_frame_of_another_site_carries_its_address(self):
        """read_page cannot see into a frame of another site, so the extension puts a link to the frame's own address
        beside it, a frame of the same site included. A frame too small to hold a form is left alone."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/framed-form.html")
                time.sleep(1.0)
                marks = job.evaluate("Object.fromEntries(['board', 'pixel', 'same'].map(id => {"
                                     "const before = document.getElementById(id).previousElementSibling;"
                                     "return [id, before && before.dataset.aaFrame === '1' ? before.getAttribute('href') : null];"
                                     "}))")
                self.assertEqual(marks["board"], "http://localhost:8001/other.html")
                self.assertIsNone(marks["pixel"])
                # read_page reads no frame, a frame of the same site included
                self.assertEqual(marks["same"], "http://127.0.0.1:8001/no-google.html")
                # the link is read_page's only way to the address, and it must not be written twice
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-frame]').length"), 2)
                self.assertEqual(job.evaluate("document.querySelector('a[data-aa-frame]').textContent"), "[AA frame]")
            finally:
                ctx.close()

    def test_a_frame_that_moved_says_where_it_is(self):
        """The src attribute is a frame's first address. A frame navigated by script, and a srcdoc frame, have no src
        worth following, so each frame of a run tab tells the page above it where it is now."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/moved-frame.html")
                time.sleep(2.0)
                marks = job.evaluate("Object.fromEntries(['moved', 'written'].map(id => {"
                                     "const before = document.getElementById(id).previousElementSibling;"
                                     "return [id, before && before.dataset.aaFrame === '1' ? before.getAttribute('href') : null];"
                                     "}))")
                self.assertEqual(marks["moved"], "http://localhost:8001/no-google.html")
                self.assertEqual(marks["written"], "http://localhost:8001/button.html")
            finally:
                ctx.close()

    def test_a_frame_that_grows_late_is_marked(self):
        """A frame is small while the page is built and is sized to its form afterwards, by style alone. That is no
        change of the tree, so the frame has to be measured again when it grows."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/grown-frame.html")
                time.sleep(1.0)
                mark = "() => { const b = document.getElementById('board').previousElementSibling;" \
                       "return b && b.dataset.aaFrame === '1' ? b.getAttribute('href') : null; }"
                self.assertIsNone(job.evaluate(mark), "a 10x10 frame holds no form")
                time.sleep(2.0)
                self.assertEqual(job.evaluate(mark), "http://localhost:8001/other.html")
            finally:
                ctx.close()

    def test_a_second_copy_of_bridge_js_does_nothing(self):
        """An extension reload leaves every open tab running the old content script, so the service worker puts the
        current one in beside it. A document must still end up with one copy: one title tag, one frame link."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                job.goto("http://127.0.0.1:8001/framed-form.html")
                time.sleep(1.0)
                version = sw.evaluate("async () => chrome.runtime.getManifest().version")
                self.assertEqual(job.evaluate("document.documentElement.dataset.aaBridge"), version)
                self.assertRegex(job.title(), r"\[AA g=\d+ named=\d+ v=" + re.escape(version) + r"\]$")
                # what adoptTabs does for a tab that came back from "Needs Attention"
                sw.evaluate(f"async () => chrome.scripting.executeScript("
                            f"{{target: {{tabId: {job_id}, allFrames: true}}, files: ['bridge.js']}})")
                time.sleep(1.0)
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-frame]').length"), 2)
                self.assertEqual(len(re.findall(r"\[AA g=", job.title())), 1)
            finally:
                ctx.close()

    def test_a_sign_in_tab_gets_no_google_count(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
            try:
                job = [page for page in ctx.pages if page is not home][0]
                with ctx.expect_page() as info:
                    job.click("#pop")
                child = info.value
                child.wait_for_load_state()
                time.sleep(1.2)
                self.assertTrue(child.title().startswith(f"[AA from {job_id}] "), child.title())
                self.assertNotIn("[AA g=", child.title())
                self.assertNotIn("[AA g=", home.title())
            finally:
                ctx.close()

    def job_on(self, p, path):
        """start_run, with the job tab already on a page of the test site."""
        ctx, sw, home, home_id, job_id, gid = self.start_run(p, "https://www.linkedin.com/robots.txt#aa-popups-as-tabs")
        job = [page for page in ctx.pages if page is not home][0]
        job.goto("http://127.0.0.1:8001/" + path)
        time.sleep(1.0)
        return ctx, sw, home, home_id, job, job_id, gid

    LABELS = ("Object.fromEntries([...document.querySelectorAll('[id]')].map("
              "e => [e.id, e.getAttribute('aria-label')]))")

    def test_field_marks_and_names(self):
        """read_page prints no state and names a textbox by its value: the extension writes both into aria-label."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "fields.html")
            try:
                labels = job.evaluate(self.LABELS)
                self.assertEqual(labels["yes"], "[req] Do you need sponsorship? / Yes")
                self.assertEqual(labels["no"], "[x] [req] Do you need sponsorship? / No")
                self.assertEqual(labels["agree"], "I agree to the terms")  # an improved name is kept even with no mark
                self.assertEqual(labels["email"], "[req] Email = a@b.co")
                self.assertEqual(labels["pw"], "Password")  # never the value of a password
                # never on a pressable control: a late pass would leave a stale name that the guard trusts
                self.assertIsNone(labels["submit"])
                self.assertIsNone(labels["toggle"])
                self.assertEqual(labels["m1"], "Teamwork / Poor")
                self.assertEqual(labels["m2"], "Teamwork / Good")
                self.assertEqual(labels["opt"], "[x] Berlin")
                self.assertEqual(labels["sw"], "Alerts")
                self.assertEqual(labels["country"], "Country = Italy")  # a role-based text control: its text is the value
                self.assertEqual(labels["wrap"], "Region = Italy")  # not its own open listbox
                self.assertEqual(labels["trigger"], "City")  # a placeholder is no answer: no " = value"
            finally:
                ctx.close()

    def test_a_text_field_nobody_can_see_is_marked_hidden(self):
        """The guard refuses form_input on a [hidden] field (a honeypot). Only text fields, and never for opacity 0, a
        field below the fold or one scrolled out of the viewport."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "hidden-fields.html")
            try:
                labels = job.evaluate(self.LABELS)
                self.assertEqual(labels["none"], "[hidden] Website")
                self.assertEqual(labels["vis"], "[hidden] Vis Hidden")
                self.assertEqual(labels["off"], "[hidden] Offscreen Field")
                for field, name in (("visible", "First Name"), ("clear", "Clear Field"), ("box", "Consent"),
                                    ("below", "Below Fold")):
                    self.assertEqual(labels[field], name)
                job.evaluate("window.scrollTo(0, 2500)")  # the first field is now out of the viewport
                job.evaluate("document.getElementById('visible').setAttribute('required', '')")  # a change makes a pass
                time.sleep(0.5)
                labels = job.evaluate(self.LABELS)
                self.assertEqual(labels["visible"], "[req] First Name")
                self.assertEqual(labels["below"], "Below Fold")
            finally:
                ctx.close()

    def test_a_revealed_field_loses_the_hidden_mark(self):
        """A collapsed section is opened by a click, and a click schedules a pass: style and class are not watched."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "hidden-fields.html")
            try:
                self.assertEqual(job.evaluate(self.LABELS)["none"], "[hidden] Website")
                job.click("#open")
                time.sleep(0.5)
                self.assertEqual(job.evaluate(self.LABELS)["none"], "Website")
            finally:
                ctx.close()

    def test_marks_follow_a_click_and_give_the_site_label_back(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "fields.html")
            try:
                job.check("#agree")
                job.evaluate("document.getElementById('sw').setAttribute('aria-checked', 'true')")
                time.sleep(0.5)
                labels = job.evaluate(self.LABELS)
                self.assertEqual(labels["agree"], "[x] I agree to the terms")
                self.assertEqual(labels["sw"], "[x] Alerts")
                job.uncheck("#agree")
                job.evaluate("document.getElementById('sw').setAttribute('aria-checked', 'false')")
                time.sleep(0.5)
                labels = job.evaluate(self.LABELS)
                self.assertEqual(labels["agree"], "I agree to the terms")
                self.assertEqual(labels["sw"], "Alerts")
                self.assertEqual(job.evaluate("document.getElementById('sw').hasAttribute('data-aa-label')"), False)
            finally:
                ctx.close()

    def test_a_frame_without_an_address_gets_a_tag(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "srcdoc-frame.html")
            try:
                tag = job.evaluate("(() => { const b = document.getElementById('doc').previousElementSibling;"
                                   "return [b.dataset.aaFrame, b.getAttribute('aria-label'), b.hasAttribute('href')]; })()")
                self.assertEqual(tag, ["none", "[AA frame: no address]", False])
            finally:
                ctx.close()

    def test_a_frame_with_no_address_offers_the_frame_inside_it(self):
        """A frame with no address of its own holds the form one level down. read_page cannot read that inner marker,
        so the marker beside the outer frame carries the inner frame's address."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "nested-srcdoc.html")
            try:
                tag = job.evaluate("(() => { const b = document.getElementById('outer').previousElementSibling;"
                                   "return [b.dataset.aaFrame, b.getAttribute('aria-label'), b.getAttribute('href')]; })()")
                self.assertEqual(tag, ["1", "[AA frame]", "http://localhost:8001/no-google.html"])
            finally:
                ctx.close()

    def test_a_frame_inside_a_frame_is_reached_in_two_hops(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "nested-form.html")
            try:
                before = "(() => document.getElementById('%s').previousElementSibling.getAttribute('href'))()"
                self.assertEqual(job.evaluate(before % "outer"), "http://localhost:8001/nested-outer.html")
                job.goto("http://localhost:8001/nested-outer.html")
                time.sleep(1.5)
                self.assertEqual(job.evaluate(before % "inner"), "http://127.0.0.1:8001/no-google.html")
            finally:
                ctx.close()

    def test_dialogs_are_answered_in_a_run_tab(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "dialogs.html")
            try:
                seen = []
                job.on("dialog", lambda d: (seen.append(d.type), d.accept()))
                job.evaluate("alert('File name is too long')")
                self.assertIs(job.evaluate("confirm('Sure?')"), False)
                self.assertIsNone(job.evaluate("prompt('Name?')"))
                banner = job.evaluate("(() => { const a = document.querySelector('a[data-aa-dialog]');"
                                      "return [a.getAttribute('aria-label'), a.hasAttribute('href'), getComputedStyle(a).pointerEvents]; })()")
                self.assertEqual(banner, ["[AA prompt: answered Cancel] Name?", False, "none"])
                job.evaluate("alert('File name is too long')")
                self.assertEqual(job.evaluate("document.querySelector('a[data-aa-dialog]').getAttribute('aria-label')"),
                                 "[AA alert] File name is too long")
                # "Leave site?" never shows: the page's beforeunload listener does not run
                # a real click is a later step: the banner of the last one goes
                job.mouse.click(5, 300)
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-dialog]').length"), 0)
                job.click("#away")
                job.wait_for_url("**/other.html")
                self.assertEqual(seen, [])
                self.assertIsNone(job.evaluate("sessionStorage.getItem('ranBeforeUnload')"), "the page's listener was stopped")
            finally:
                ctx.close()

    def test_dialogs_are_native_outside_a_run_group(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "dialogs.html")
            try:
                other = ctx.new_page()
                seen = []
                other.on("dialog", lambda d: (seen.append((d.type, d.message)), d.accept()))
                other.goto("http://127.0.0.1:8001/dialogs.html")
                time.sleep(0.5)
                self.assertIn("[native code]", other.evaluate("window.alert.toString()"))
                other.evaluate("alert('hi')")
                self.assertIs(other.evaluate("confirm('Sure?')"), True)
                self.assertEqual(seen, [("alert", "hi"), ("confirm", "Sure?")])
            finally:
                ctx.close()

    def group_plan(self, title, job_id):
        return "https://www.linkedin.com/robots.txt?aa-groups#aa-groups=" + quote(
            json.dumps({title: [job_id]}, separators=(",", ":")), safe="")

    def test_a_grouped_tab_has_native_dialogs_and_an_adopted_one_is_marked_again(self):
        """End of a run: the moved tab drops the attribute, so its own Submit is never cancelled by a shimmed confirm.
        /continue-applying then adopts it back: the bridge's observers are installed already, yet it must be marked."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "fields.html")
            try:
                self.assertEqual(job.evaluate(self.LABELS)["opt"], "[x] Berlin")
                job.evaluate("alert('File name is too long')")
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-dialog]').length"), 1)
                home.goto(self.group_plan("Needs Attention", job_id))
                time.sleep(1.2)
                self.assertTrue(home.title().startswith("[AA grouped 1]"), home.title())
                self.assertFalse(job.evaluate("document.documentElement.hasAttribute('data-aa-popups-as-tabs')"))
                # leaving the run takes the dialog banner and the labels away
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-dialog]').length"), 0)
                self.assertIsNone(job.evaluate(self.LABELS)["opt"])
                self.assertIn("[native code]", job.evaluate("window.alert.toString()"))
                seen = []
                job.on("dialog", lambda d: (seen.append(d.type), d.accept()))
                self.assertIs(job.evaluate("confirm('Submit?')"), True)
                self.assertEqual(seen, ["confirm"])
                # the candidate ticks a box while the tab is parked: no pass runs there
                job.check("#agree")
                time.sleep(0.5)
                plan = quote(json.dumps([job_id], separators=(",", ":")), safe="")
                home.goto("https://www.linkedin.com/robots.txt?aa-adopt#aa-adopt=" + plan)
                time.sleep(1.5)
                self.assertTrue(home.title().startswith("[AA adopted 1]"), home.title())
                self.assertTrue(job.evaluate("document.documentElement.hasAttribute('data-aa-popups-as-tabs')"))
                self.assertEqual(job.evaluate(self.LABELS)["agree"], "[x] I agree to the terms")
                self.assertEqual(job.evaluate(self.LABELS)["opt"], "[x] Berlin")
                self.assertIs(job.evaluate("confirm('Submit?')"), False)
            finally:
                ctx.close()

    def test_a_no_address_tag_turns_into_a_link(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "srcdoc-frame.html")
            try:
                job.evaluate("document.getElementById('doc').contentWindow.location.replace('http://localhost:8001/other.html')")
                time.sleep(1.5)
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-frame]').length"), 1)
                link = job.evaluate("(() => { const b = document.getElementById('doc').previousElementSibling;"
                                    "return [b.dataset.aaFrame, b.getAttribute('href')]; })()")
                self.assertEqual(link, ["1", "http://localhost:8001/other.html"])
            finally:
                ctx.close()

    def test_frames_drop_the_attribute_after_grouping(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "framed-form.html")
            try:
                attr = "document.documentElement.hasAttribute('data-aa-popups-as-tabs')"
                board = [f for f in job.frames if f.url.endswith("/other.html")][0]
                self.assertTrue(board.evaluate(attr))
                home.goto(self.group_plan("Pending Review", job_id))
                time.sleep(1.5)
                self.assertFalse(job.evaluate(attr))
                self.assertFalse(board.evaluate(attr))
            finally:
                ctx.close()

    def test_a_tab_taken_out_of_the_run_group_leaves_the_run(self):
        """The candidate takes a pop-up tab out of its group: it must not be put back into its opener's group."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "job.html")
            try:
                with ctx.expect_page() as info:
                    job.click("#pop")
                child = info.value
                child.wait_for_load_state()
                time.sleep(1.0)
                attr = "document.documentElement.hasAttribute('data-aa-popups-as-tabs')"
                self.assertTrue(child.evaluate(attr))
                child_id = sw.evaluate("async () => (await chrome.tabs.query({})).map(t => t.id).sort((a, b) => b - a)[0]")
                sw.evaluate(f"async () => chrome.tabs.ungroup([{child_id}])")
                time.sleep(1.5)
                self.assertEqual(sw.evaluate(f"async () => (await chrome.tabs.get({child_id})).groupId"), -1)
                self.assertFalse(child.evaluate(attr))
            finally:
                ctx.close()

    def test_a_dialog_function_the_site_replaced_is_kept(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "fields.html")
            try:
                job.evaluate("window.__mine = window.alert = function () { return 'mine'; }")
                home.goto(self.group_plan("Pending Review", job_id))
                time.sleep(1.5)
                self.assertTrue(job.evaluate("window.alert === window.__mine"))
                # the other two were ours and are native again
                self.assertIn("[native code]", job.evaluate("window.confirm.toString()"))
            finally:
                ctx.close()

    def test_a_frame_written_by_the_page_has_no_address(self):
        """document.open on an about:blank frame gives it the page's own address: a link to it would be a loop."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "written-frame.html")
            try:
                tag = job.evaluate("(() => { const b = document.getElementById('doc').previousElementSibling;"
                                   "return [b.dataset.aaFrame, b.hasAttribute('href')]; })()")
                self.assertEqual(tag, ["none", False])
                self.assertEqual(job.evaluate("document.querySelectorAll('a[data-aa-frame]').length"), 1)
            finally:
                ctx.close()


    DEEP = ("(id) => { const find = (root) => { const hit = root.getElementById(id); if (hit) return hit;"
            "for (const el of root.querySelectorAll('*')) if (el.shadowRoot) { const r = find(el.shadowRoot); if (r) return r; }"
            "return null; }; return find(document); }")
    PROXIES = ("() => [...document.querySelectorAll('div[data-aa-shadow] > *')].map(p => { const b = p.getBoundingClientRect();"
               "const c = getComputedStyle(p); return {tag: p.tagName, type: p.getAttribute('type'), label: p.getAttribute('aria-label'),"
               "name: p.getAttribute('data-aa-shadow-name'), ph: p.getAttribute('placeholder'), opacity: c.opacity, events: c.pointerEvents,"
               "x: b.x, y: b.y, w: b.width, h: b.height}; })")
    # proxy name -> id of the real control
    REAL = {"First name*": "first", "Email*": "email", "LinkedIn": "linkedin", "City*": "city", "Phone number": "phone",
            "Country code": "cc", "Next": "next", "": "blank", "Choose a file or drop it here": "file1",
            "Choose a file or drop it here (2)": "file2", "Cover letter": "cover", "Preferred country": "sel",
            "I agree to the terms": "agree", "ph:Postal code": "postal", "Berlin": "opt", "Late field": "late"}

    def proxies(self, job):
        # two proxies have no name (the blank button and the placeholder-only field): the second is keyed "ph:<placeholder>"
        return {(p["name"] or ("ph:" + p["ph"] if p["ph"] else "")): p for p in job.evaluate(self.PROXIES)}

    def test_shadow_controls_get_light_dom_proxies(self):
        """read_page does not enter shadow roots: each real control gets a light-DOM proxy that it does list."""
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "shadow-form.html")
            try:
                found = self.proxies(job)
                self.assertEqual(sorted(found), sorted(self.REAL))
                kinds = {"First name*": ("INPUT", "text"), "Email*": ("INPUT", "email"), "LinkedIn": ("INPUT", "text"),
                         "City*": ("INPUT", "text"), "Phone number": ("INPUT", "tel"), "Country code": ("BUTTON", "submit"),
                         "Next": ("BUTTON", "submit"), "": ("BUTTON", "submit"),
                         "Choose a file or drop it here": ("INPUT", "file"),
                         "Choose a file or drop it here (2)": ("INPUT", "file"), "Cover letter": ("TEXTAREA", None),
                         "Preferred country": ("SELECT", None), "I agree to the terms": ("INPUT", "checkbox"),
                         "Berlin": ("DIV", None), "ph:Postal code": ("INPUT", "text"), "Late field": ("INPUT", "text")}
                for name, (tag, type_) in kinds.items():
                    self.assertEqual((found[name]["tag"], found[name]["type"] if type_ else None), (tag, type_), name)
                self.assertEqual(job.evaluate("document.querySelectorAll('div[data-aa-shadow] select option').length"), 3)
                # a nameless control stays nameless, so the guard's nameless-submit refusal still applies
                self.assertIsNone(found[""]["label"])
                self.assertEqual(found["Next"]["label"], "Next")
                # a placeholder-only field: markFields names it from the twin's placeholder; the shadow name stays empty
                postal = found["ph:Postal code"]
                self.assertEqual((postal["ph"], postal["label"], postal["name"]), ("Postal code", "Postal code", ""))
                # the light-DOM button is untouched
                self.assertIsNone(job.evaluate("document.getElementById('light').getAttribute('aria-label')"))
                self.assertNotIn("Apply With Indeed", found)
                # opacity > 0 (read_page drops 0), clicks pass through, and the box lies over the real control
                for name, proxy in found.items():
                    self.assertGreater(float(proxy["opacity"]), 0, name)
                    self.assertEqual(proxy["events"], "none", name)
                    real = job.evaluate(f"(id) => {{ const r = ({self.DEEP})(id).getBoundingClientRect();"
                                        "return [r.x, r.y, r.width, r.height]; }", self.REAL[name])
                    for have, want in zip((proxy["x"], proxy["y"], proxy["w"], proxy["h"]), real):
                        self.assertAlmostEqual(have, want, delta=2, msg=name)
            finally:
                ctx.close()

    def test_shadow_proxies_are_marked_and_forward(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "shadow-form.html")
            try:
                self.assertEqual(self.proxies(job)["First name*"]["label"], "[req] First name*")
                job.evaluate(f"(() => {{ const r = ({self.DEEP})('first'); r.value = 'Ada';"
                             "r.dispatchEvent(new Event('input', {bubbles: true, composed: true})); })()")
                time.sleep(0.6)
                self.assertEqual(self.proxies(job)["First name*"]["label"], "[req] First name* = Ada")
                # form_input: the proxy takes the value and fires input; the real input must receive it
                job.evaluate("(() => { const p = [...document.querySelectorAll('div[data-aa-shadow] input')]"
                             ".find(e => e.dataset.aaShadowName === 'LinkedIn'); p.value = 'in/ada';"
                             "p.dispatchEvent(new Event('input', {bubbles: true})); })()")
                self.assertEqual(job.evaluate(f"({self.DEEP})('linkedin').value"), "in/ada")
                # a click on the checkbox proxy ticks the real checkbox
                job.evaluate("[...document.querySelectorAll('div[data-aa-shadow] input')]"
                             ".find(e => e.dataset.aaShadowName === 'I agree to the terms').dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}))")
                self.assertTrue(job.evaluate(f"({self.DEEP})('agree').checked"))
                # file_upload: the file lands in the real shadow file input
                job.locator("div[data-aa-shadow] input[type=file]").first.set_input_files(
                    {"name": "cv.pdf", "mimeType": "application/pdf", "buffer": b"%PDF-1"})
                self.assertEqual(job.evaluate(f"({self.DEEP})('file1').files[0].name"), "cv.pdf")
                self.assertEqual(job.evaluate(f"({self.DEEP})('file2').files.length"), 0)
            finally:
                ctx.close()

    def test_late_shadow_field_and_leaving_the_run(self):
        with sync_playwright() as p:
            ctx, sw, home, home_id, job, job_id, gid = self.job_on(p, "shadow-form.html")
            try:
                self.assertIn("Late field", self.proxies(job))
                home.goto(self.group_plan("Pending Review", job_id))
                time.sleep(1.5)
                self.assertEqual(job.evaluate("document.querySelectorAll('div[data-aa-shadow]').length"), 0)
            finally:
                ctx.close()


if __name__ == "__main__":
    unittest.main()
