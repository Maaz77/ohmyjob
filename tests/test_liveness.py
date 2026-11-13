"""Tests for the early liveness check (scripts/liveness.py) and its two call sites, `prep start` and `apply start`.

No live network call anywhere: the classifier is fed constructed pages (tests/fixtures/liveness/, "Lumenfield Logistics"
and "Quillon Systems" of the fictional Alex Example workspace), `fetch` is replaced by a stub, and the call-site tests run
pj.py / aa.py in a subprocess whose `liveness.fetch` is a stub that answers from a JSON table (the same trick test_pj uses
for the compile).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
PAGES = REPO / "tests" / "fixtures" / "liveness"

import liveness as LV  # noqa: E402
from liveness import Reply, Verdict  # noqa: E402

LI = "https://www.linkedin.com/jobs/view/4399000001"
LI_FINAL = "https://www.linkedin.com/jobs/view/operations-analyst-at-lumenfield-logistics-4399000001"
EMPLOYER = "https://careers.quillon.example/jobs/project-coordinator-48213"

# Recorded from the real services (trimmed to the head, the top card and the description; no tracking tokens, no
# query strings). The four LinkedIn postings are labelled by the owner; each was fetched as `GET /jobs/view/<id>`
# through liveness.fetch: HTTP 200, no redirect. The Lever and Greenhouse pages answer a made-up posting id on a
# public board (no known closed posting was available): see the audit in scripts/liveness.py.
REAL_LINKEDIN = {"4453875402": "closed", "4454194482": "closed", "4467574932": "open", "4486909828": "open"}
LINKEDIN_FILE = {"4453875402": "linkedin_closed_4453875402.html", "4454194482": "linkedin_closed_4454194482.html",
                 "4467574932": "linkedin_open_4467574932.html", "4486909828": "linkedin_open_4486909828.html"}
CLOSED_ID, OPEN_ID = "4453875402", "4467574932"
LEVER_ID = "00000000-0000-4000-8000-000000000000"
LEVER = f"https://jobs.lever.co/palantir/{LEVER_ID}"  # recorded: HTTP 404
LEVER_OPEN = "https://jobs.lever.co/palantir/6ed76ce8-4156-4b60-b120-403538bd66cd"  # recorded: HTTP 200
GREENHOUSE_OPEN = "https://job-boards.greenhouse.io/discord/jobs/8927168002"  # recorded: HTTP 200, no redirect
GREENHOUSE = "https://job-boards.greenhouse.io/discord/jobs/1234567"  # recorded: 200 on the board below
GREENHOUSE_MOVED = "https://job-boards.greenhouse.io/discord?error=true"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def reply(status: int = 200, body: str = "", url: str = "") -> Reply:
    return Reply(status, url, body)


def li_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def li_reply(job_id: str, body: str | None = None) -> Reply:
    return Reply(200, li_url(job_id), page(LINKEDIN_FILE[job_id]) if body is None else body)


# --------------------------------------------------------------------------- the classifier: LinkedIn


@pytest.mark.parametrize("job_id", sorted(REAL_LINKEDIN))
def test_the_four_real_linkedin_pages_get_the_owners_label(job_id):
    verdict = LV.classify(li_url(job_id), li_reply(job_id))
    assert verdict.outcome == REAL_LINKEDIN[job_id], verdict
    assert verdict.code == ("linkedin-closed-banner" if REAL_LINKEDIN[job_id] == "closed" else "ok")


def test_the_closed_pages_carry_the_banner_and_the_open_pages_do_not():
    for job_id, label in REAL_LINKEDIN.items():
        body = page(LINKEDIN_FILE[job_id])
        assert ("closed-job__flavor--closed" in body) == (label == "closed"), job_id
        assert ("apply-button" in body) == (label == "open"), job_id


def test_a_description_that_says_no_longer_accepting_applications_is_not_closed():
    """The false-closed trap, on a real open page: the phrase is in the description (agencies), the top card is open."""
    body = page(LINKEDIN_FILE[OPEN_ID])
    marker = '<div class="show-more-less-html__markup'
    assert marker in body
    injected = body.replace(marker, "<p>We are no longer accepting applications from recruitment agencies.</p>" + marker, 1)
    injected = injected.replace("</section>", "<p>No longer accepting applications</p></section>", 1)
    assert injected.lower().count("no longer accepting applications") == 2
    assert LV.classify(li_url(OPEN_ID), li_reply(OPEN_ID, injected)) == Verdict("open", "ok")


def test_markup_of_the_banner_quoted_inside_a_description_is_not_closed():
    body = page(LINKEDIN_FILE[OPEN_ID])
    quoted = '&lt;figcaption class="closed-job__flavor--closed"&gt;No longer accepting applications&lt;/figcaption&gt;'
    injected = body.replace('<div class="show-more-less-html__markup', f"<p>{quoted}</p>" + '<div class="show-more-less-html__markup', 1)
    assert quoted in injected and LV.classify(li_url(OPEN_ID), li_reply(OPEN_ID, injected)).outcome == "open"


def test_both_the_banner_element_and_its_words_are_needed():
    """The real closed page with the words changed, or with the element renamed, is not positive evidence."""
    body = page(LINKEDIN_FILE[CLOSED_ID])
    assert "No longer accepting applications" in body
    changed_words = body.replace("No longer accepting applications", "Applications close soon")
    renamed = body.replace("closed-job__flavor--closed", "topcard__note")
    for variant in (changed_words, renamed):
        assert variant != body and LV.classify(li_url(CLOSED_ID), li_reply(CLOSED_ID, variant)).outcome == "open"
    assert LV.classify(li_url(CLOSED_ID), li_reply(CLOSED_ID, body)).outcome == "closed"


@pytest.mark.parametrize("final", ["https://www.linkedin.com/signup/cold-join", "https://www.linkedin.com/authwall?x=1",
                                   "https://www.linkedin.com/uas/login"])
def test_linkedin_sign_in_wall_is_unknown_even_when_its_page_says_closed(final):
    """The wall page is constructed (LinkedIn answered the real fetches without one); it carries the closed banner on purpose."""
    out = LV.classify(LI, Reply(200, final, page("linkedin_signup_wall.html")))
    assert out.outcome == "unknown"
    assert LV.classify(LI, Reply(200, final, page(LINKEDIN_FILE[CLOSED_ID]))).outcome == "unknown"


@pytest.mark.parametrize("status", [999, 429, 403, 401, 404, 410, 500, 503])
def test_linkedin_refused_or_odd_status_is_unknown_never_closed(status):
    """LinkedIn rate-limits: a refusal is doubt. Not even 404 or 410 counts as closed there, whatever the body says."""
    assert LV.classify(li_url(CLOSED_ID), Reply(status, li_url(CLOSED_ID), page(LINKEDIN_FILE[CLOSED_ID]))).outcome == "unknown"


def test_linkedin_redirect_off_the_job_page_is_unknown():
    assert LV.classify(LI, Reply(200, "https://www.linkedin.com/jobs/search/?keywords=x", page(LINKEDIN_FILE[CLOSED_ID]))) \
        .outcome == "unknown"


# --------------------------------------------------------------------------- the classifier: other hosts
# Three outcomes per host, through the production classifier (no stub in between): Lever and Greenhouse with recorded
# pages, and the host-independent 410. A plain employer page is never closed on its text.


def test_lever_three_outcomes():
    assert LV.classify(LEVER, Reply(404, LEVER, page("lever_404.html"))) == Verdict("closed", "http-404-lever")
    assert LV.classify(LEVER_OPEN, Reply(200, LEVER_OPEN, page("lever_open_palantir.html"))) == Verdict("open", "ok")
    for status in (429, 403, 500, 503):
        assert LV.classify(LEVER, Reply(status, LEVER, "")).outcome == "unknown"
    assert LV.classify(LEVER, Reply(200, "https://jobs.lever.co/login", "")).outcome == "unknown"


def test_lever_404_needs_a_posting_address_on_lever():
    """A 404 on the board page, on a look-alike host or on a non-posting path is not evidence."""
    for url in ("https://jobs.lever.co/palantir", "https://jobs.lever.co/palantir/not-an-id",
                f"https://notlever.co/palantir/{LEVER_ID}", f"https://lever.co.evil.example/palantir/{LEVER_ID}"):
        assert LV.classify(url, Reply(404, url, page("lever_404.html"))).outcome == "unknown", url
    eu = f"https://jobs.eu.lever.co/palantir/{LEVER_ID}"
    assert LV.classify(eu, Reply(404, eu, page("lever_404.html"))).outcome == "closed"


def test_greenhouse_three_outcomes():
    assert LV.classify(GREENHOUSE, Reply(200, GREENHOUSE_MOVED, page("greenhouse_error_board_discord.html"))) \
        == Verdict("closed", "greenhouse-closed-redirect")
    assert LV.classify(GREENHOUSE_OPEN, Reply(200, GREENHOUSE_OPEN, page("greenhouse_open_discord.html"))) == Verdict("open", "ok")
    assert LV.classify(GREENHOUSE, Reply(503, GREENHOUSE, "")).outcome == "unknown"
    assert LV.classify(GREENHOUSE, Reply(200, "https://job-boards.greenhouse.io/login", "")).outcome == "unknown"


def test_greenhouse_redirect_needs_a_posting_address_and_the_error_flag():
    board = "https://job-boards.greenhouse.io/discord"
    # a board address that carries the error flag was never a posting; a posting that reaches a posting is open
    assert LV.classify(board, Reply(200, GREENHOUSE_MOVED, "")).outcome == "open"
    assert LV.classify(GREENHOUSE, Reply(200, board, page("greenhouse_error_board_discord.html"))).outcome == "open"
    assert LV.classify(GREENHOUSE, Reply(200, GREENHOUSE + "?error=true", "")).outcome == "open"
    # the board that sends a missing id to the company's own site: left alone (observed for another real board)
    assert LV.classify("https://boards.greenhouse.io/figma/jobs/1234567",
                       Reply(200, "https://www.figma.com/careers/", "<h1>Careers</h1>")).outcome == "unknown"
    fake = "https://greenhouse.io.evil.example/discord/jobs/1234567"
    assert LV.classify(fake, Reply(200, "https://greenhouse.io.evil.example/discord?error=true", "")).outcome == "open"


def test_410_is_closed_on_any_host_but_404_is_not():
    assert LV.classify(EMPLOYER, reply(410, url=EMPLOYER)) == Verdict("closed", "http-410")
    assert LV.classify(GREENHOUSE_OPEN, reply(410, url=GREENHOUSE_OPEN)) == Verdict("closed", "http-410")
    assert LV.classify(EMPLOYER, reply(404, url=EMPLOYER)).outcome == "unknown"


def test_words_on_a_page_are_never_evidence_on_another_host():
    """No text rule exists for other hosts: pages that say closed in their headings, title or text stay open."""
    says = ('<html><head><title>This job is no longer available</title></head><body><h1>No longer accepting applications</h1>'
            "<h2>The position has been filled</h2><p>This job has expired and is closed.</p></body></html>")
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, says)) == Verdict("open", "ok")
    assert LV.classify(LEVER_OPEN, Reply(200, LEVER_OPEN, says)).outcome == "open"
    assert LV.classify(GREENHOUSE_OPEN, Reply(200, GREENHOUSE_OPEN, says)).outcome == "open"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 999])
def test_other_host_refusals_are_unknown(status):
    assert LV.classify(EMPLOYER, reply(status, url=EMPLOYER)).outcome == "unknown"


def test_sign_in_wall_and_redirect_to_another_site_are_unknown():
    for final in ("https://careers.quillon.example/login?next=/jobs/1", "https://sso.other.example/start"):
        assert LV.classify(EMPLOYER, Reply(410, final, "")).outcome == "unknown"  # even a 410 behind a wall or another site
    assert LV.classify(LEVER, Reply(404, "https://jobs.lever.co/sign-in", "")).outcome == "unknown"


def test_a_page_that_cannot_be_read_is_not_closed():
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, "\x00\x01<h1")).outcome == "open"
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, "")).outcome == "open"


def test_the_closed_note_names_the_closing_and_the_date():
    for code in LV.CLOSED_TEXT:
        text = LV.note(code, "2026-11-13")
        assert text.startswith("Deleted 2026-11-13: posting closed (liveness check 2026-11-13: ") and LV.CLOSED_TEXT[code] in text


# --------------------------------------------------------------------------- check(): outcomes, politeness, deadline


@pytest.fixture
def quick(monkeypatch):
    """No pauses, no kill switch, and a recording sleep."""
    monkeypatch.delenv(LV.KILL_SWITCH, raising=False)
    sleeps: list[float] = []
    monkeypatch.setattr(LV.time, "sleep", lambda s: sleeps.append(s))
    return sleeps


def stub(monkeypatch, answers: dict):
    """fetch answers from {url: Reply | Exception}; returns the list of urls asked, in order."""
    asked: list[str] = []
    lock = threading.Lock()

    def fake(url):
        with lock:
            asked.append(url)
        answer = answers[url]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(LV, "fetch", fake)
    return asked


def test_the_three_outcomes_for_linkedin_and_another_host(quick, monkeypatch):
    answers = {
        li_url(CLOSED_ID): li_reply(CLOSED_ID),
        li_url(OPEN_ID): li_reply(OPEN_ID),
        "https://www.linkedin.com/jobs/view/4399000003": TimeoutError("timed out"),
        LEVER: Reply(404, LEVER, page("lever_404.html")),
        LEVER_OPEN: Reply(200, LEVER_OPEN, page("lever_open_palantir.html")),
        f"https://jobs.lever.co/palantir/{'1' * 8}-1111-4111-8111-{'1' * 12}": Reply(200, "https://jobs.lever.co/sign-in", "<h1>Sign in</h1>"),
    }
    stub(monkeypatch, answers)
    jobs = [(f"k{i}", url) for i, url in enumerate(answers)]
    out = LV.check(jobs)
    assert [out[f"k{i}"].outcome for i in range(6)] == ["closed", "open", "unknown", "closed", "open", "unknown"]
    assert out["k2"].code == "network-error" and out["k5"].code == "sign-in-wall"
    assert LV.find_closed(jobs) == {"k0": "linkedin-closed-banner", "k3": "http-404-lever"}


def test_a_timeout_or_a_sign_in_wall_is_treated_as_open_by_find_closed(quick, monkeypatch):
    stub(monkeypatch, {LI: TimeoutError("t"), EMPLOYER: Reply(410, "https://careers.quillon.example/login", "")})
    assert LV.find_closed([("a", LI), ("b", EMPLOYER)]) == {}


def test_any_error_inside_the_check_is_unknown_and_the_others_still_run(quick, monkeypatch):
    other = "https://careers.quillon.example/jobs/2"
    stub(monkeypatch, {LI: RuntimeError("boom"), other: Reply(410, other, "")})
    out = LV.check([("a", LI), ("b", other)])
    assert out["a"] == Verdict("unknown", "network-error") and out["b"].outcome == "closed"


def test_a_host_is_left_alone_after_repeated_refusals(quick, monkeypatch):
    urls = [f"https://www.linkedin.com/jobs/view/43990000{i:02d}" for i in range(8)]
    asked = stub(monkeypatch, {u: Reply(999, u, "") for u in urls})
    out = LV.check([(u, u) for u in urls])
    assert len(asked) == LV.STOP_AFTER == 3
    assert [out[u].code for u in urls] == ["http-999"] * 3 + ["host-stopped"] * 5
    assert not LV.find_closed([(u, u) for u in urls])  # the same stub, again: still nothing closed


def test_a_good_answer_resets_the_refusal_count(quick, monkeypatch):
    urls = [f"https://careers.quillon.example/jobs/{i}" for i in range(7)]
    answers = {u: Reply(429, u, "") for u in urls}
    answers[urls[2]] = Reply(200, urls[2], "<h1>Role</h1>")
    answers[urls[5]] = Reply(200, urls[5], "<h1>Role</h1>")
    asked = stub(monkeypatch, answers)
    LV.check([(u, u) for u in urls])
    assert len(asked) == 7  # never three refusals in a row


def test_another_host_is_not_stopped_by_the_first_one(quick, monkeypatch):
    li = [f"https://www.linkedin.com/jobs/view/43990000{i:02d}" for i in range(5)]
    answers = {u: Reply(429, u, "") for u in li}
    answers[EMPLOYER] = Reply(410, EMPLOYER, "")
    stub(monkeypatch, answers)
    out = LV.check([(u, u) for u in li] + [("e", EMPLOYER)])
    assert out["e"].outcome == "closed"


def test_calls_to_one_host_are_paused_and_calls_to_another_host_are_not(quick, monkeypatch):
    a = [f"https://careers.quillon.example/jobs/{i}" for i in range(3)]
    b = ["https://www.linkedin.com/jobs/view/4399000009"]
    stub(monkeypatch, {**{u: Reply(200, u, "x") for u in a}, b[0]: Reply(200, LI_FINAL, "x")})
    LV.check([(u, u) for u in a + b])
    pauses = [s for s in quick if s > 0]
    assert len(pauses) == 2  # three calls to the employer host: two pauses; the one LinkedIn call has none
    assert all(0 < s <= LV.PAUSE_SECONDS for s in pauses)


def test_the_same_address_is_asked_once(quick, monkeypatch):
    asked = stub(monkeypatch, {EMPLOYER: Reply(410, EMPLOYER, "")})
    out = LV.check([("a", EMPLOYER), ("b", EMPLOYER)])
    assert asked == [EMPLOYER] and out["a"].outcome == out["b"].outcome == "closed"


def test_the_time_budget_ends_the_check_and_the_rest_is_unknown(quick, monkeypatch):
    urls = [f"https://careers.quillon.example/jobs/{i}" for i in range(4)]
    asked = stub(monkeypatch, {u: Reply(410, u, "") for u in urls})
    monkeypatch.setattr(LV, "DEADLINE_SECONDS", 0)
    out = LV.check([(u, u) for u in urls])
    assert asked == [] and {v.code for v in out.values()} == {"time-budget"}


def test_addresses_that_are_not_a_named_http_site_are_never_fetched(quick, monkeypatch):
    bad = ["file:///etc/passwd", "http://127.0.0.1/admin", "http://localhost/x", "https://10.0.0.1/jobs/1",
           "ftp://careers.quillon.example/x", "https://user:pw@careers.quillon.example/x", "", "not a url"]
    asked = stub(monkeypatch, {})
    out = LV.check([(str(i), u) for i, u in enumerate(bad)])
    assert asked == [] and {v.code for v in out.values()} == {"bad-url"}


def test_the_kill_switch_asks_nobody(monkeypatch):
    monkeypatch.setenv(LV.KILL_SWITCH, "1")
    asked = stub(monkeypatch, {EMPLOYER: Reply(410, EMPLOYER, "")})
    assert LV.find_closed([("a", EMPLOYER)]) == {} and asked == []


def test_check_never_raises(quick, monkeypatch):
    monkeypatch.setattr(LV, "_run_host", lambda *a: 1 / 0)
    assert LV.check([("a", EMPLOYER)])["a"].outcome == "unknown"
    assert LV.check([]) == {}


# --------------------------------------------------------------------------- fetch(): the scraper's session


def test_fetch_uses_the_scrapers_session_headers_and_a_timeout(monkeypatch):
    import jobspy.util

    seen = {}

    class Raw:
        def read(self, n, decode_content=False):
            seen["read"] = (n, decode_content)
            return "Näher".encode("utf-8")

    class Response:
        status_code, url, encoding, raw = 200, "https://careers.quillon.example/final", "utf-8", Raw()

        def close(self):
            seen["closed"] = True

    class Session:
        def __init__(self, proxies=None, clear_cookies=False):
            seen["clear_cookies"] = clear_cookies
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, url, **kw):
            seen["get"] = (url, kw)
            return Response()

    monkeypatch.setattr(jobspy.util, "RequestsRotating", Session)
    got = LV.fetch(EMPLOYER)
    assert got == Reply(200, "https://careers.quillon.example/final", "Näher")
    assert seen["get"][1]["timeout"] == LV.TIMEOUT_SECONDS and seen["clear_cookies"] and seen["closed"]
    assert seen["read"] == (LV.MAX_BODY_BYTES, True)


# --------------------------------------------------------------------------- the call sites, in a subprocess

STUB_RUNNER = '''import json, sys
import liveness
from liveness import Reply
table = json.load(open(sys.argv[2], encoding="utf-8"))
log = sys.argv[2] + ".calls"
def fake(url):
    open(log, "a", encoding="utf-8").write(url + "\\n")
    answer = table[url]
    if "raise" in answer:
        raise TimeoutError(answer["raise"])
    body = open(answer["page"], encoding="utf-8").read() if "page" in answer else ""
    return Reply(answer["status"], answer.get("url", url), body)
liveness.fetch = fake
liveness.PAUSE_SECONDS = liveness.PAUSE_LINKEDIN_SECONDS = 0
import importlib
importlib.import_module(sys.argv[1]).main(sys.argv[3:])
'''


class Site:
    """Runs `pj` or `aa` with liveness.fetch stubbed from a table; the kill switch is off for these runs."""

    def __init__(self, root: Path, tmp: Path):
        self.root, self.tmp = root, tmp
        (tmp / "runner.py").write_text(STUB_RUNNER, encoding="utf-8")
        self.table = tmp / "pages.json"
        self.env = {**os.environ, "OHMYJOB_WORKSPACE": str(root), "PYTHONPATH": str(SCRIPTS), "AA_STAGGER": "0"}
        for name in ("CLAUDE_PROJECT_DIR", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", LV.KILL_SWITCH):
            self.env.pop(name, None)

    def serve(self, table: dict) -> None:
        self.table.write_text(json.dumps({u: {**a, "page": str(PAGES / a["page"])} if "page" in a else a
                                          for u, a in table.items()}), encoding="utf-8")
        Path(str(self.table) + ".calls").unlink(missing_ok=True)

    def calls(self) -> list[str]:
        log = Path(str(self.table) + ".calls")
        return log.read_text(encoding="utf-8").split() if log.exists() else []

    def run(self, module: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(self.tmp / "runner.py"), module, str(self.table), *args],
                              capture_output=True, text=True, env=self.env, cwd=self.root)


def tracker_rows(root: Path) -> dict:
    import tracker as T

    return {r["_key"]: r for r in T.Tracker(root / "Job_Tracker.xlsx").rows()}


LUMEN, QUILLON = "4399000001", "969845171025937"
QUILLON_JOB = "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268"
QUILLON_APPLY = "https://careers.quillon.example/jobs/project-coordinator-48213"
CLOSED_LI = {"status": 200, "page": LINKEDIN_FILE[CLOSED_ID]}
OPEN_LI = {"status": 200, "page": LINKEDIN_FILE[OPEN_ID]}
CLOSED_EMPLOYER = {"status": 410}  # the host-independent rule
OPEN_EMPLOYER = {"status": 200, "page": "lever_open_palantir.html"}


@pytest.fixture
def prep(workspace, tmp_path):
    """Two jobs of the fixture workspace at Found, a tracker, and both resume PDFs the screening needs."""
    sys.path.insert(0, str(REPO / "tests"))
    import test_pj

    env = test_pj.Env(workspace, tmp_path)
    env.folder(LUMEN, "Lumenfield Logistics", "Operations Analyst")
    env.row(LUMEN, "Lumenfield Logistics", "Operations Analyst", "Found")
    env.folder(QUILLON, "Quillon Systems", "Project Coordinator", url=QUILLON_JOB)
    env.row(QUILLON, "Quillon Systems", "Project Coordinator", "Found", url=QUILLON_JOB)
    md = next((workspace / "Applications").glob(f"{QUILLON}_*")) / "job.md"
    md.write_text(md.read_text(encoding="utf-8").replace(f"- Job URL: {QUILLON_JOB}\n",
                                                         f"- Job URL: {QUILLON_JOB}\n- Apply URL: {QUILLON_APPLY}\n"), encoding="utf-8")
    site = Site(workspace, tmp_path)
    site.env["PYTHONPATH"] = f"{env.fakedir}{os.pathsep}{SCRIPTS}"
    return site, env


def start_json(proc: subprocess.CompletedProcess) -> dict:
    assert proc.returncode == 0 and proc.stderr == "", proc.stdout + proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_prep_start_deletes_a_closed_found_job_before_any_worker_and_lists_it(prep):
    site, env = prep
    site.serve({LI: CLOSED_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start = start_json(site.run("pj", "start"))
    assert (start["screen"], start["build"]) == (1, 0)  # one worker for the open job, none for the closed one
    assert len(start["deleted"]) == 1 and start["deleted"][0].startswith("Lumenfield Logistics | Operations Analyst: Deleted ")
    assert "posting closed" in start["deleted"][0] and "liveness check" in start["deleted"][0]
    row = tracker_rows(site.root)[LUMEN]
    assert row["Status"] == "Deleted" and "posting closed" in row["Notes"] and "liveness check 20" in row["Notes"]
    assert not list((site.root / "Applications").glob(f"{LUMEN}_*"))  # folder gone, after the row was saved
    assert tracker_rows(site.root)[QUILLON]["Status"] == "Found"
    assert [w[0] for w in env.workers(env.pj("assign-screen", env=site.env))] == [QUILLON]  # no brief for the closed job
    summary = env.pj("end", env=site.env)
    assert "- START Lumenfield Logistics | Operations Analyst: Deleted " in summary
    assert "Deletion check OK" in summary and "Folder check OK" in summary


def test_prep_start_checks_the_apply_url_when_a_job_has_one(prep):
    site, _env = prep
    site.serve({LI: OPEN_LI, QUILLON_APPLY: CLOSED_EMPLOYER})
    start_json(site.run("pj", "start"))
    assert site.calls().count(QUILLON_APPLY) == 1 and QUILLON_JOB not in site.calls()
    assert tracker_rows(site.root)[QUILLON]["Status"] == "Deleted"
    assert tracker_rows(site.root)[LUMEN]["Status"] == "Found"


def test_prep_start_keeps_a_job_whose_check_timed_out_or_hit_a_sign_in_wall(prep):
    site, _env = prep
    site.serve({LI: {"raise": "timed out"},
                QUILLON_APPLY: {"status": 410, "url": "https://careers.quillon.example/sign-in"}})
    start = start_json(site.run("pj", "start"))
    assert (start["screen"], start["build"], start["deleted"]) == (2, 0, [])
    rows = tracker_rows(site.root)
    assert rows[LUMEN]["Status"] == rows[QUILLON]["Status"] == "Found"
    assert len(list((site.root / "Applications").iterdir())) == 2


def test_prep_start_deletes_a_closed_matched_job_with_its_yaml(prep):
    site, env = prep
    folder = next((site.root / "Applications").glob(f"{LUMEN}_*"))
    (folder / "Alex_Lumenfield_Operations-Analyst.yaml").write_text("name: Alex Example\n", encoding="utf-8")
    import tracker as T

    tr = T.Tracker(site.root / "Job_Tracker.xlsx")
    tr.set_cell(tracker_rows(site.root)[LUMEN]["_index"], "Status", "Matched")
    tr.save()
    site.serve({LI: CLOSED_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start = start_json(site.run("pj", "start"))
    assert (start["screen"], start["build"]) == (1, 0)
    assert tracker_rows(site.root)[LUMEN]["Status"] == "Deleted" and not folder.exists()


def test_prep_start_does_not_check_a_job_it_just_dropped_as_a_duplicate(prep):
    site, env = prep
    env.folder("4399000002", "Lumenfield Logistics", "Operations Analyst-copy", url="https://www.linkedin.com/jobs/view/4399000002")
    env.row("4399000002", "Lumenfield Logistics", "Operations Analyst", "Found", url="https://www.linkedin.com/jobs/view/4399000002")
    site.serve({LI: OPEN_LI, "https://www.linkedin.com/jobs/view/4399000002": OPEN_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start_json(site.run("pj", "start"))
    dup = "https://www.linkedin.com/jobs/view/4399000002"
    assert tracker_rows(site.root)["4399000002"]["Status"] == "Deleted" and "duplicate" in tracker_rows(site.root)["4399000002"]["Notes"]
    assert dup not in site.calls() and LI in site.calls()


def test_a_failing_check_never_stops_prep_start(prep):
    site, _env = prep
    site.serve({LI: {"raise": "boom"}, QUILLON_APPLY: {"raise": "boom"}})
    assert start_json(site.run("pj", "start"))["screen"] == 2


@pytest.fixture
def apply(workspace, tmp_path):
    """The two jobs of the fixture workspace at Resume Built, each with a tailored yaml and PDF."""
    from tracker import Tracker

    for name in ("Pending-Review", "Needs-Attention", "Submitted"):
        (workspace / name).mkdir(exist_ok=True)
    tr = Tracker.create(workspace / "Job_Tracker.xlsx")
    for job in sorted((workspace / "Applications").iterdir()):
        shutil.copy2(workspace / "Default_Resume_Files" / "Operations-Analyst.yaml", job / "Alex_Example_Resume.yaml")
        (job / "Alex_Example_Resume.pdf").write_bytes(b"%PDF-1.4\n% test resume\n")
        company, role = job.name.split("_")[1:]
        url = LI if job.name.startswith(LUMEN) else QUILLON_JOB
        tr.add_row({"Job URL": url, "Status": "Resume Built", "Company": company.replace("-", " "),
                    "Job Title": role.replace("-", " "), "Location": "Berlin, Germany"})
    tr.save()
    return Site(workspace, tmp_path)


def test_apply_start_deletes_a_closed_resume_built_job_without_a_tab_and_lists_it(apply):
    apply.serve({LI: CLOSED_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start = start_json(apply.run("aa", "start"))
    assert start["count"] == 1 and [j["folder"].split("_")[0] for j in start["jobs"]] == [QUILLON]
    assert len(start["closed"]) == 1 and start["closed"][0].startswith("Lumenfield Logistics | Operations Analyst: ")
    assert start["linkedin_sign_in_check"] is False  # the closed LinkedIn job no longer asks for the sign-in check
    row = tracker_rows(apply.root)[LUMEN]
    assert row["Status"] == "Deleted" and "posting closed" in row["Notes"] and "liveness check 20" in row["Notes"]
    assert not list((apply.root / "Applications").glob(f"{LUMEN}_*"))  # a folder with a built PDF goes too, after the save
    state = json.loads(next((apply.root / ".ohmyjob" / "run" / "apply").glob("*/state.json")).read_text())
    assert list(state["jobs"]) == [j["folder"] for j in start["jobs"]]  # no tab, no brief, no worker for the closed job
    out = apply.run("aa", "finish")
    assert "- DELETED (closed, found by the early liveness check, no tab opened) Lumenfield Logistics" in out.stdout


def test_apply_start_where_every_job_is_closed_ends_with_count_zero(apply):
    apply.serve({LI: CLOSED_LI, QUILLON_APPLY: CLOSED_EMPLOYER})
    start = start_json(apply.run("aa", "start"))
    assert start["count"] == 0 and len(start["closed"]) == 2
    assert {r["Status"] for r in tracker_rows(apply.root).values()} == {"Deleted"}


def test_apply_start_keeps_jobs_whose_check_failed_and_reports_nothing_closed(apply):
    apply.serve({LI: {"status": 429}, QUILLON_APPLY: {"raise": "timed out"}})
    start = start_json(apply.run("aa", "start"))
    assert start["count"] == 2 and start["closed"] == []
    assert {r["Status"] for r in tracker_rows(apply.root).values()} == {"Resume Built"}
    assert len(list((apply.root / "Applications").iterdir())) == 2


def test_apply_start_top_k_keeps_the_open_jobs_when_the_best_one_is_closed(apply):
    import tracker as T

    tr = T.Tracker(apply.root / "Job_Tracker.xlsx")
    for key, score in ((LUMEN, 9), (QUILLON, 7)):
        tr.set_cell(tracker_rows(apply.root)[key]["_index"], "Match Score", score)
    tr.save()
    apply.serve({LI: CLOSED_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start = start_json(apply.run("aa", "start", "--top-k-match", "1"))
    assert [j["folder"].split("_")[0] for j in start["jobs"]] == [QUILLON]


def test_apply_start_continue_mode_does_no_check(apply):
    apply.serve({})
    start_json(apply.run("aa", "start", "--continue"))
    assert apply.calls() == []


def test_apply_start_leaves_everything_when_the_tracker_cannot_be_saved(apply):
    """A locked tracker: no row changes and no folder is deleted; the job stays queued for the stage 3 CLOSED path."""
    (apply.root / "~$Job_Tracker.xlsx").write_text("lock")
    apply.serve({LI: CLOSED_LI, QUILLON_APPLY: OPEN_EMPLOYER})
    start = start_json(apply.run("aa", "start"))
    assert start["count"] == 2 and start["closed"] == [] and any("could not be saved" in w for w in start["warnings"])
    assert len(list((apply.root / "Applications").iterdir())) == 2
    (apply.root / "~$Job_Tracker.xlsx").unlink()
    assert {r["Status"] for r in tracker_rows(apply.root).values()} == {"Resume Built"}
