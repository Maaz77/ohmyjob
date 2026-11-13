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
GREENHOUSE = "https://boards.greenhouse.io/quillon/jobs/48213"
LEVER = "https://jobs.lever.co/quillon/0f1e2d3c"


def page(name: str) -> str:
    return (PAGES / name).read_text(encoding="utf-8")


def reply(status: int = 200, body: str = "", url: str = "") -> Reply:
    return Reply(status, url, body)


# --------------------------------------------------------------------------- the classifier: LinkedIn


def test_linkedin_banner_is_closed():
    assert LV.classify(LI, Reply(200, LI_FINAL, page("linkedin_closed.html"))) == Verdict("closed", "linkedin-closed-banner")


def test_linkedin_open_page_is_open():
    assert LV.classify(LI, Reply(200, LI_FINAL, page("linkedin_open.html"))).outcome == "open"


def test_a_description_that_says_no_longer_accepting_applications_is_not_closed():
    """The false-closed trap: the phrase is in the description (agencies) of a posting whose top card is open."""
    body = page("linkedin_open.html")
    assert "no longer accepting applications" in body.lower()
    assert LV.classify(LI, Reply(200, LI_FINAL, body)) == Verdict("open", "ok")
    other = page("linkedin_phrase_outside_banner.html")  # also a `closed-job-note` element: not the banner
    assert LV.classify(LI, Reply(200, LI_FINAL, other)) == Verdict("open", "ok")


@pytest.mark.parametrize("final", ["https://www.linkedin.com/signup/cold-join", "https://www.linkedin.com/authwall?x=1",
                                   "https://www.linkedin.com/uas/login"])
def test_linkedin_sign_in_wall_is_unknown_even_when_its_page_says_closed(final):
    out = LV.classify(LI, Reply(200, final, page("linkedin_signup_wall.html")))
    assert out.outcome == "unknown"


@pytest.mark.parametrize("status", [999, 429, 403, 401, 404, 410, 500, 503])
def test_linkedin_refused_or_odd_status_is_unknown_never_closed(status):
    """LinkedIn rate-limits: a refusal is doubt. Not even 404 or 410 counts as closed there."""
    assert LV.classify(LI, Reply(status, LI, page("linkedin_closed.html"))).outcome == "unknown"


def test_linkedin_redirect_off_the_job_page_is_unknown():
    assert LV.classify(LI, Reply(200, "https://www.linkedin.com/jobs/search/?keywords=x", page("linkedin_closed.html"))) \
        .outcome == "unknown"


# --------------------------------------------------------------------------- the classifier: other hosts


def test_employer_page_with_closed_heading_is_closed():
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, page("employer_closed.html"))) == Verdict("closed", "closed-heading")


def test_employer_open_page_that_mentions_expired_and_unavailable_in_text_is_open():
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, page("employer_open.html"))) == Verdict("open", "ok")


def test_410_is_closed_on_any_host_but_404_is_not():
    assert LV.classify(EMPLOYER, reply(410, url=EMPLOYER)) == Verdict("closed", "http-410")
    assert LV.classify(EMPLOYER, reply(404, url=EMPLOYER)).outcome == "unknown"


def test_lever_404_and_greenhouse_error_redirect_are_closed():
    assert LV.classify(LEVER, reply(404, url=LEVER)) == Verdict("closed", "http-404-lever")
    moved = "https://boards.greenhouse.io/quillon?error=true"
    assert LV.classify(GREENHOUSE, Reply(200, moved, page("greenhouse_board.html"))) == Verdict("closed", "greenhouse-closed-redirect")
    assert LV.classify(GREENHOUSE, Reply(200, "https://boards.greenhouse.io/quillon", page("greenhouse_board.html"))).outcome == "open"


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 999])
def test_other_host_refusals_are_unknown(status):
    assert LV.classify(EMPLOYER, reply(status, url=EMPLOYER)).outcome == "unknown"


def test_sign_in_wall_and_redirect_to_another_site_are_unknown():
    assert LV.classify(EMPLOYER, Reply(200, "https://careers.quillon.example/login?next=/jobs/1", page("employer_closed.html"))) \
        .outcome == "unknown"
    assert LV.classify(EMPLOYER, Reply(200, "https://sso.other.example/start", page("employer_closed.html"))) \
        .outcome == "unknown"


def test_a_page_that_cannot_be_read_is_not_closed():
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, "\x00\x01<h1")).outcome == "open"
    assert LV.classify(EMPLOYER, Reply(200, EMPLOYER, "")).outcome == "open"


def test_the_closed_note_names_the_closing_and_the_date():
    text = LV.note("linkedin-closed-banner", "2026-11-13")
    assert text.startswith("Deleted 2026-11-13: posting closed (liveness check 2026-11-13: ")
    assert all("closed" in LV.note(code, "2026-11-13").lower() or "410" in LV.note(code, "2026-11-13") for code in LV.CLOSED_TEXT)


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
        LI: Reply(200, LI_FINAL, page("linkedin_closed.html")),
        "https://www.linkedin.com/jobs/view/4399000002": Reply(200, LI_FINAL, page("linkedin_open.html")),
        "https://www.linkedin.com/jobs/view/4399000003": TimeoutError("timed out"),
        EMPLOYER: Reply(200, EMPLOYER, page("employer_closed.html")),
        "https://careers.quillon.example/jobs/2": Reply(200, EMPLOYER, page("employer_open.html")),
        "https://careers.quillon.example/jobs/3": Reply(200, "https://careers.quillon.example/sign-in", "<h1>Sign in</h1>"),
    }
    stub(monkeypatch, answers)
    out = LV.check([(f"k{i}", url) for i, url in enumerate(answers)])
    assert [out[f"k{i}"].outcome for i in range(6)] == ["closed", "open", "unknown", "closed", "open", "unknown"]
    assert out["k2"].code == "network-error" and out["k5"].code == "sign-in-wall"
    assert LV.find_closed([(f"k{i}", url) for i, url in enumerate(answers)]) == {
        "k0": "linkedin-closed-banner", "k3": "closed-heading"}


def test_a_timeout_or_a_sign_in_wall_is_treated_as_open_by_find_closed(quick, monkeypatch):
    stub(monkeypatch, {LI: TimeoutError("t"), EMPLOYER: Reply(200, "https://careers.quillon.example/login", "x")})
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
CLOSED_LI = {"status": 200, "page": "linkedin_closed.html"}
OPEN_LI = {"status": 200, "page": "linkedin_open.html"}
CLOSED_EMPLOYER = {"status": 200, "page": "employer_closed.html"}
OPEN_EMPLOYER = {"status": 200, "page": "employer_open.html"}


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
                QUILLON_APPLY: {"status": 200, "url": "https://careers.quillon.example/sign-in", "page": "employer_closed.html"}})
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
