"""scripts/add_job.py (/ohmyjob:add-job): a posting found by hand becomes an ordinary job at Found.

Pages are never fetched: add_job.fetch is replaced by a table of HTML samples. The last tests run the real prep, apply
and reconcile scripts (subprocesses) on what add_job wrote.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from pypdf import PdfWriter

import add_job
import aa_lib as L
import scrape
import workspace as W
from job_key import job_key, pasted_url
from tracker import Tracker

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
FILLER = ("You will analyse weekly operations data, build reports in SQL and spreadsheets, and work with the planning team "
          "to find and fix delays in the warehouse. We expect two years of experience with data, clear writing and a "
          "habit of checking your own numbers before anyone else does.")
URL = "https://careers.harborline.example/jobs/ops-analyst-42"
URL2 = "https://careers.harborline.example/jobs/coordinator-7"


def ldjson(company="Harborline Freight", title="Operations Analyst", city="Hamburg", description=FILLER, **more):
    posting = {"@context": "https://schema.org", "@type": "JobPosting", "title": title, "datePosted": "2026-11-03T08:00:00Z",
               "hiringOrganization": {"@type": "Organization", "name": company}, "employmentType": ["FULL_TIME"],
               "description": f"<p>{description}</p><h2>Requirements</h2><ul><li>SQL</li><li>Excel</li></ul>"}
    if city:
        posting["jobLocation"] = {"@type": "Place", "address": {"addressLocality": city, "addressCountry": "DE"}}
    posting.update(more)
    return f'<script type="application/ld+json">{json.dumps(posting)}</script>'


def page(*, ld="", title="Careers", body=FILLER):
    return (f"<html><head><title>{title}</title>{ld}</head><body><nav>Home Jobs About us Sign in</nav>"
            f"<main><h1>Open role</h1><p>{body}</p></main><footer>Cookies Imprint</footer></body></html>")


@pytest.fixture
def env(workspace, monkeypatch):
    for folder in (workspace / "Applications").iterdir():  # the fixture's two sample folders have no rows here
        shutil.rmtree(folder)
    Tracker.create(workspace / "Job_Tracker.xlsx")
    for name in ("Operations-Analyst", "Project-Coordinator"):  # a usable resume for `prep start`
        writer = PdfWriter()
        writer.add_blank_page(612, 792)
        with open(workspace / "Default_Resume_Files" / f"{name}.pdf", "wb") as handle:
            writer.write(handle)
    pages, fetched = {}, []

    def fake_fetch(url):
        fetched.append(url)
        if url not in pages:
            raise add_job.Unreadable("the page could not be loaded (fake)")
        return pages[url]

    monkeypatch.setattr(add_job, "fetch", fake_fetch)
    return workspace, pages, fetched


def add(root, *items, search=None):
    postings = [add_job.normalize(i if isinstance(i, dict) else {"url": i}) for i in items]
    return add_job.run(root, postings, search or W.load_config(root)["search"])


def rows(root):
    return Tracker(root / "Job_Tracker.xlsx").rows()


def folders(root):
    return sorted(p.name for p in (root / "Applications").iterdir())


def overview(root, name):
    return L.job_overview((root / "Applications" / name / "job.md").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- add by URL


def test_url_add_writes_the_folder_job_md_and_a_found_row(env, capsys):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson())
    assert add(root, URL) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("added: Harborline Freight - Operations Analyst (")
    assert out[-1] == "total: 1 added, 0 already known, 0 unreadable, 0 need input; run /ohmyjob:prep-jobs"
    key = job_key(URL)
    assert folders(root) == [f"{key}_Harborline-Freight_Operations-Analyst"]
    ov = overview(root, folders(root)[0])
    assert ov["Job URL"] == URL and ov["Source"] == "Added by hand (careers.harborline.example)"
    assert ov["Location"] == "Hamburg, DE" and ov["Employment type"] == "Full-time" and ov["Date Posted"] == "2026-11-03"
    description = L.job_description((root / "Applications" / folders(root)[0] / "job.md").read_text(encoding="utf-8"))
    assert "weekly operations data" in description and "- SQL" in description
    (row,) = rows(root)
    assert (row["Status"], row["Job URL"], row["_key"], row["Company"]) == ("Found", URL, key, "Harborline Freight")
    assert row["Source"] == "Added by hand (careers.harborline.example)" and row["Location"] == "Hamburg, DE"


def test_several_urls_in_one_call_and_tracking_parameters_are_dropped(env, capsys):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson())
    pages[URL2] = page(ld=ldjson(title="Project Coordinator", city="Berlin"))
    assert add(root, URL + "?utm_source=newsletter&gclid=x", URL2) == 0
    assert len(rows(root)) == 2 and sorted(r["Job URL"] for r in rows(root)) == sorted([URL, URL2])
    assert "total: 2 added" in capsys.readouterr().out


def test_linkedin_url_keeps_its_id_and_its_site_in_the_source(env):
    root, pages, fetched = env
    url = "https://www.linkedin.com/jobs/view/data-analyst-at-acme-4399000077?trk=x"
    pages["https://www.linkedin.com/jobs/view/4399000077"] = page(ld=ldjson())
    assert add(root, url) == 0
    (row,) = rows(root)
    assert row["_key"] == "4399000077" and row["Source"] == "Added by hand (linkedin.com)"
    assert folders(root)[0].startswith("4399000077_")


def test_page_without_a_job_posting_asks_for_company_and_title_then_uses_the_visible_text(env, capsys):
    root, pages, fetched = env
    pages[URL] = page(title="Operations Analyst | Careers", body=FILLER)
    assert add(root, URL) == 2
    out = capsys.readouterr().out
    assert f'needs input: {URL}: the company and title could not be told with confidence (the page is titled "Operations Analyst | Careers")' in out
    assert rows(root) == [] and folders(root) == []
    assert add(root, {"url": URL, "company": "Harborline Freight", "title": "Operations Analyst"}) == 0
    text = (root / "Applications" / folders(root)[0] / "job.md").read_text(encoding="utf-8")
    assert "weekly operations data" in text and "Imprint" not in text and "Home Jobs About us" not in text


def test_the_candidate_can_correct_company_title_and_location_of_a_readable_page(env):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson(city=""))
    assert add(root, {"url": URL, "company": "Harborline Freight Ltd", "location": "Remote, Germany"}) == 0
    (row,) = rows(root)
    assert (row["Company"], row["Location"]) == ("Harborline Freight Ltd", "Remote, Germany")


# --------------------------------------------------------------------------- add by text


TEXT = "  " + FILLER + "\n\n## About us\nWe move freight. " + FILLER


def test_text_only_add_gets_a_stable_pasted_address_that_job_key_turns_into_the_folder_id(env, capsys):
    root, pages, fetched = env
    item = {"text": TEXT, "company": "Harborline Freight", "title": "Operations Analyst", "location": "Hamburg",
            "apply_url": "https://apply.harborline.example/ops?utm_medium=mail"}
    assert add(root, item) == 0 and fetched == []
    address = pasted_url(TEXT)
    key = job_key(address)
    assert re.fullmatch(r"8\d{14}", key) and address.startswith("paste:")
    (row,) = rows(root)
    assert (row["Job URL"], row["_key"], row["Status"]) == (address, key, "Found")
    assert row["Source"] == "Added by hand (pasted text)"
    (name,) = folders(root)
    assert name == f"{key}_Harborline-Freight_Operations-Analyst"
    ov = overview(root, name)
    assert ov["Job URL"] == address and ov["Apply URL"] == "https://apply.harborline.example/ops"
    assert "### About us" in (root / "Applications" / name / "job.md").read_text(encoding="utf-8")
    # the same text again, with other spacing and case: the same id, nothing created
    capsys.readouterr()
    assert add(root, {**item, "text": TEXT.upper().replace("\n\n", " ")}) == 0
    assert ": Harborline Freight - Operations Analyst, sheet row 2, status Found" in capsys.readouterr().out
    assert len(rows(root)) == 1 and len(folders(root)) == 1


def test_pasted_text_with_a_url_uses_the_text_and_the_urls_id_without_fetching(env):
    root, pages, fetched = env
    assert add(root, {"url": URL, "text": TEXT, "company": "Harborline Freight", "title": "Operations Analyst"}) == 0
    assert fetched == []
    (row,) = rows(root)
    assert row["Job URL"] == URL and row["_key"] == job_key(URL) and row["Source"] == "Added by hand (careers.harborline.example)"


def test_pasted_text_without_company_or_title_needs_input(env, capsys):
    root, pages, fetched = env
    assert add(root, {"text": TEXT, "company": "Harborline Freight"}) == 2
    assert "needs input: pasted text: " in capsys.readouterr().out and rows(root) == []


# --------------------------------------------------------------------------- already known


def test_the_same_url_twice_creates_nothing_and_names_the_row(env, capsys):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson())
    add(root, URL)
    capsys.readouterr()
    assert add(root, URL + "?utm_source=x") == 0
    assert capsys.readouterr().out.splitlines()[0] == (
        f"already known: {URL}: Harborline Freight - Operations Analyst, sheet row 2, status Found")
    assert len(rows(root)) == 1 and len(folders(root)) == 1 and fetched == [URL]  # not fetched again


def test_the_same_url_twice_in_one_call(env, capsys):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson())
    assert add(root, URL, URL) == 0
    out = capsys.readouterr().out
    assert out.count("added:") == 1 and "already known:" in out and len(rows(root)) == 1


def test_a_job_that_a_board_found_is_known_by_its_id_and_its_final_status_is_named(env, capsys):
    root, pages, fetched = env
    t = Tracker(root / "Job_Tracker.xlsx")
    t.add_row({"Job URL": "https://www.linkedin.com/jobs/view/4399000099", "Status": "Skipped-LowFit", "Company": "Acme",
               "Job Title": "Analyst", "Location": "Berlin"})
    t.save()
    assert add(root, "https://www.linkedin.com/jobs/view/4399000099?trk=abc") == 0
    assert ("already known: https://www.linkedin.com/jobs/view/4399000099: Acme - Analyst, sheet row 2, status Skipped-LowFit"
            in capsys.readouterr().out)
    assert fetched == [] and len(rows(root)) == 1 and folders(root) == []


def test_a_folder_without_a_row_is_known_too(env, capsys):
    root, pages, fetched = env
    (root / "Applications" / f"{job_key(URL)}_Old_Folder").mkdir()
    assert add(root, URL) == 0
    assert "already known: " + URL + ": a job folder with this id exists, without a tracker row" in capsys.readouterr().out
    assert rows(root) == [] and fetched == []


def test_a_twin_of_a_row_from_a_board_is_not_added_and_the_row_is_named(env, capsys):
    root, pages, fetched = env
    t = Tracker(root / "Job_Tracker.xlsx")
    t.add_row({"Job URL": "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268", "Status": "Resume Built",
               "Company": "Harborline Freight GmbH", "Job Title": "Operations Analyst (m/f/d)", "Location": "Hamburg, Germany",
               "Date Found": "2020-01-01 09:00"})  # an old row still counts
    t.save()
    pages[URL] = page(ld=ldjson())
    assert add(root, URL) == 0
    assert (f"already known: {URL}: same company, title and city as Harborline Freight GmbH - Operations Analyst (m/f/d), "
            "sheet row 2, status Resume Built") in capsys.readouterr().out
    assert len(rows(root)) == 1 and folders(root) == []


def test_a_posting_without_a_location_is_a_twin_in_any_city_but_one_with_a_city_is_not_a_twin_of_a_row_without_one(env, capsys):
    root, pages, fetched = env
    t = Tracker(root / "Job_Tracker.xlsx")
    t.add_row({"Job URL": "https://x.example/1", "Status": "Found", "Company": "Harborline Freight", "Job Title": "Operations Analyst",
               "Location": "Berlin"})
    t.add_row({"Job URL": "https://x.example/2", "Status": "Found", "Company": "Other Co", "Job Title": "Operations Analyst"})
    t.save()
    pages[URL] = page(ld=ldjson(city=""))
    pages[URL2] = page(ld=ldjson(company="Other Co", city="Hamburg"))
    assert add(root, URL, URL2) == 0
    out = capsys.readouterr().out
    assert f"already known: {URL}: same company, title and city as Harborline Freight - Operations Analyst, sheet row 2" in out
    assert "added: Other Co - Operations Analyst" in out


# --------------------------------------------------------------------------- unreadable


@pytest.mark.parametrize("html, why", [
    (None, "the page could not be loaded"),
    (page(ld="", title="Sign in", body="Sign in to see this job and who you know there. Join now to see the full description. " * 3), "a sign-in, script or cookie wall"),
    ("<html><body><div id=app></div><noscript>You need to enable JavaScript to run this app.</noscript></body></html>", "the text is empty"),
    (page(ld=ldjson(description="Great job. Apply now.")), "empty or too short"),
    (page(ld=ldjson(description="")), "empty or too short"),
    (page(ld=ldjson().replace('"description"', '"x-description"'), title="Sign in",
          body="Sign in to see this job and who you know there. Join now to see the full description. " * 3),
     "a sign-in, script or cookie wall"),
])
def test_an_unreadable_page_creates_no_row_and_no_folder(env, capsys, html, why):
    root, pages, fetched = env
    if html is not None:
        pages[URL] = html
    assert add(root, {"url": URL, "company": "Harborline Freight", "title": "Operations Analyst"}) == 2
    out = capsys.readouterr().out
    assert f"unreadable: {URL}: " in out and why in out and "paste the posting text instead" in out
    assert "total: 0 added, 0 already known, 1 unreadable" in out
    assert rows(root) == [] and folders(root) == []


@pytest.mark.parametrize("text", ["", "   ", "Great job at a great company. Apply today!", "Sign in to apply. Cookies. Imprint."])
def test_pasted_text_that_is_empty_or_boilerplate_creates_nothing(env, capsys, text):
    root, pages, fetched = env
    item = {"text": text, "company": "Harborline Freight", "title": "Operations Analyst", "url": URL if text else ""}
    if not text.strip():
        item["url"] = URL  # normalize needs a url or a text; the empty text then means "fetch", which fails
    assert add(root, item) == 2
    assert "unreadable:" in capsys.readouterr().out and rows(root) == [] and folders(root) == []


def test_an_error_status_a_sign_in_redirect_and_a_pdf_are_unreadable(monkeypatch):
    import io
    import urllib.error
    import urllib.request

    class Response(io.BytesIO):
        def __init__(self, final, kind, body=b"x"):
            super().__init__(body)
            self.final, self.headers = final, type("H", (), {"get_content_type": lambda s: kind, "get_content_charset": lambda s: None})()

        def geturl(self):
            return self.final

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def serve(response):
        def urlopen(request, timeout):
            if isinstance(response, Exception):
                raise response
            return response
        monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    serve(urllib.error.HTTPError(URL, 403, "Forbidden", {}, None))
    with pytest.raises(add_job.Unreadable, match="HTTP 403"):
        add_job.fetch(URL)
    serve(urllib.error.URLError("no route"))
    with pytest.raises(add_job.Unreadable, match="no route"):
        add_job.fetch(URL)
    serve(Response("https://www.linkedin.com/authwall?trk=x", "text/html"))
    with pytest.raises(add_job.Unreadable, match="sign-in"):
        add_job.fetch(URL)
    serve(Response(URL, "application/pdf"))
    with pytest.raises(add_job.Unreadable, match="not a web page"):
        add_job.fetch(URL)
    serve(Response(URL, "text/html", "<p>café</p>".encode()))
    assert add_job.fetch(URL) == "<p>café</p>"


# --------------------------------------------------------------------------- the search filters do not apply


def configure(root, **search):
    raw = json.loads((root / "ohmyjob.json").read_text(encoding="utf-8"))
    raw["search"].update(search)
    (root / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
    return W.load_config(root)["search"]


def test_no_search_filter_drops_a_posting_the_candidate_chose(env, capsys):
    root, pages, fetched = env
    search = configure(root, title_filter="strict", search_query=["Operations Analyst"], filter_title_terms=["sales"],
                       exclude_seniority_level=["senior"], employment_types=["fulltime"], recency_days=1)
    pages[URL] = page(ld=ldjson(title="Senior Sales Chef", datePosted="2019-01-01", employmentType=["CONTRACTOR"]))
    assert add(root, URL, search=search) == 0
    assert "added: Harborline Freight - Senior Sales Chef" in capsys.readouterr().out
    assert [r["Status"] for r in rows(root)] == ["Found"]


def test_an_excluded_company_is_added_with_one_warning(env, capsys):
    root, pages, fetched = env
    search = configure(root, exclude_companies=["Harborline"])
    pages[URL] = page(ld=ldjson())
    assert add(root, URL, search=search) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "WARNING Harborline Freight matches exclude_companies entry 'Harborline'; added anyway, you chose this job"
    assert out[1].startswith("added:") and [r["Status"] for r in rows(root)] == ["Found"] and len(folders(root)) == 1


# --------------------------------------------------------------------------- the command line


def run_main(root, *args):
    return subprocess.run([sys.executable, str(SCRIPTS / "add_job.py"), *args], capture_output=True, text=True,
                          env={**os.environ, "OHMYJOB_WORKSPACE": str(root)})


@pytest.mark.parametrize("name, text", [
    ("ohmyjob.json", None),
    ("Profile.md", "OHMYJOB-TEMPLATE: mock data\n# Fit Preferences\n"),
])
def test_mock_guard_refuses_with_one_fatal_line_naming_the_file(env, name, text):
    root, pages, fetched = env
    if text is None:
        raw = json.loads((root / name).read_text(encoding="utf-8"))
        raw["_template"] = "OHMYJOB-TEMPLATE: replace the mock values with yours, then delete this line"
        (root / name).write_text(json.dumps(raw), encoding="utf-8")
    else:
        (root / name).write_text(text, encoding="utf-8")
    proc = run_main(root, URL)
    assert proc.returncode == 1 and len(proc.stdout.splitlines()) == 1
    assert proc.stdout.startswith(f"FATAL: {name} still has the template marker (OHMYJOB-TEMPLATE)")
    assert rows(root) == [] and folders(root) == []


def test_mock_guard_comes_before_the_inbox_is_read(env):
    root, pages, fetched = env
    (root / "Profile.md").write_text("OHMYJOB-TEMPLATE\n", encoding="utf-8")
    proc = run_main(root, "--paste")
    assert proc.stdout.startswith("FATAL: Profile.md still has the template marker")


def test_inbox_paste_adds_the_postings_and_deletes_the_file(env, capsys):
    root, pages, fetched = env
    (root / ".ohmyjob").mkdir(exist_ok=True)
    (root / ".ohmyjob" / "add-job.json").write_text("left over", encoding="utf-8")  # --inbox clears it
    inbox = Path(run_main(root, "--inbox").stdout.strip())
    assert inbox == root / ".ohmyjob" / "add-job.json"
    inbox.write_text(json.dumps([{"text": TEXT, "company": "Harborline Freight", "title": "Operations Analyst"}]), encoding="utf-8")
    proc = run_main(root, "--paste")
    assert proc.returncode == 0 and "added: Harborline Freight - Operations Analyst" in proc.stdout
    assert not inbox.exists() and len(rows(root)) == 1


@pytest.mark.parametrize("content, message", [
    ("not json", "is not a JSON list"), ('{"a": 1}', "must hold a JSON list"), ("[1]", "must be an object"),
    ('[{"company": "x"}]', "needs a url or a text"), ('[{"url": 5}]', "values are all strings"),
])
def test_a_bad_inbox_is_one_fatal_line_and_is_still_deleted(env, content, message):
    root, pages, fetched = env
    inbox = root / ".ohmyjob" / "add-job.json"
    inbox.parent.mkdir(exist_ok=True)
    inbox.write_text(content, encoding="utf-8")
    proc = run_main(root, "--paste")
    assert proc.returncode == 1 and proc.stdout.startswith("FATAL: ") and message in proc.stdout and not inbox.exists()


def test_no_input_and_a_missing_inbox_are_fatal(env):
    root, pages, fetched = env
    assert run_main(root).stdout.startswith("FATAL: give at least one web address")
    assert "not found: write the postings there first" in run_main(root, "--paste").stdout


def test_a_value_that_is_no_web_address_is_unreadable(env, capsys):
    root, pages, fetched = env
    assert add(root, "ftp://files.example/job.txt") == 2 and "not a web address" in capsys.readouterr().out and fetched == []


def test_the_tracker_open_in_excel_stops_the_run_after_job_md_was_written(env):
    root, pages, fetched = env
    pages[URL] = page(ld=ldjson())
    (root / "~$Job_Tracker.xlsx").write_bytes(b"")
    with pytest.raises(scrape.Stop, match="Close the tracker in Excel/LibreOffice first"):
        add(root, URL)
    assert rows(root) == [] and len(folders(root)) == 1  # prep-jobs adds the row of a folder that has none


# --------------------------------------------------------------------------- the rest of the pipeline


def sub(root, script, *args, code=None):
    env = {**os.environ, "OHMYJOB_WORKSPACE": str(root), "PYTHONPATH": str(SCRIPTS)}
    env.pop("CLAUDE_PROJECT_DIR", None)
    cmd = [sys.executable, str(SCRIPTS / script), *args] if code is None else [sys.executable, "-c", code, *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=root)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def added_job(env, text_only):
    root, pages, fetched = env
    if text_only:
        add(root, {"text": TEXT, "company": "Harborline Freight", "title": "Operations Analyst", "location": "Hamburg"})
    else:
        pages[URL] = page(ld=ldjson())
        add(root, URL)
    return root, folders(root)[0], rows(root)[0]


@pytest.mark.parametrize("text_only", [False, True])
def test_prep_start_pairs_the_folder_with_its_row_and_queues_it_for_screening(env, text_only):
    root, name, row = added_job(env, text_only)
    start = json.loads(sub(root, "pj.py", "start", code="import sys, pj; pj.main(sys.argv[1:])"))
    assert (start["screen"], start["build"]) == (1, 0)
    (after,) = rows(root)
    assert after["Status"] == "Found" and after["_key"] == row["_key"] and len(rows(root)) == 1  # no row made, none deleted
    assert folders(root) == [name]


@pytest.mark.parametrize("text_only, apply_url, queued", [(False, "", True), (True, "https://apply.harborline.example/ops", True), (True, "", False)])
def test_apply_start_queues_an_added_job_like_a_scraped_one(env, text_only, apply_url, queued):
    root, pages, fetched = env
    if text_only:
        add(root, {"text": TEXT, "company": "Harborline Freight", "title": "Operations Analyst", "apply_url": apply_url})
    else:
        pages[URL] = page(ld=ldjson())
        add(root, URL)
    name = folders(root)[0]
    (root / "Applications" / name / "Alex_Harborline.pdf").write_bytes(b"%PDF-1.4\n")
    t = Tracker(root / "Job_Tracker.xlsx")
    t.set_status(1, "Resume Built", None)
    t.save()
    started = json.loads(sub(root, "aa.py", "start"))
    if queued:
        assert started["count"] == 1 and started["jobs"][0]["folder"] == name and started["skipped"] == []
    else:
        assert started["count"] == 0
        assert started["skipped"] == [{"job": "Harborline Freight | Operations Analyst", "folder": name,
                                       "reason": "pasted posting without a web address: apply by hand"}]


@pytest.mark.parametrize("text_only", [False, True])
def test_reconcile_pairs_the_added_folder_with_its_row(env, text_only):
    root, name, row = added_job(env, text_only)
    t = Tracker(root / "Job_Tracker.xlsx")
    t.set_status(1, "Pending Review", None)
    t.save()
    (root / "Applications" / name).rename(root / "Pending-Review" / name)
    out = sub(root, "reconcile.py", "--apply")
    assert "Pending Review -> Submitted" in out and "Unresolved folders: 0" in out and "Invariant: 1 Submitted rows == 1" in out
    assert [r["Status"] for r in rows(root)] == ["Submitted"] and (root / "Submitted" / name / "job.md").exists()
