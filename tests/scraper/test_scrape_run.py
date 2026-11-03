"""scrape.run with jobspy.scrape_jobs replaced by DataFrames shaped like JobSpy 1.4.1's output."""
import json
import subprocess
from datetime import date, timedelta

import jobspy
import pandas as pd
import pytest
from jobspy.model import JobPost

import aa_lib as L
import scrape
import workspace as W
from job_key import job_key, strip_tracking
from tracker import Tracker, TrackerError


def frame(*posts, errors=None):
    """A DataFrame like scrape_jobs returns: JobPost.model_dump() rows, columns in JobPost field order."""
    df = pd.DataFrame([JobPost(**p).model_dump() for p in posts], columns=list(JobPost.model_fields))
    df.attrs = {"errors": errors or {}, "warnings": {}}
    return df


def post(title="Operations Analyst", company="Acme GmbH", url="https://www.linkedin.com/jobs/view/111", **more):
    base = dict(title=title, company=company, job_url=url, description="Do the work.\nBring SQL.",
                location={"city": "Berlin", "country": "germany"}, job_type=["fulltime"], date_posted="2026-11-02")
    base.update(more)
    return base


@pytest.fixture
def setup(workspace, monkeypatch):
    Tracker.create(workspace / "Job_Tracker.xlsx")
    calls = []
    boards = {}

    def fake(**kwargs):
        calls.append(kwargs)
        out = boards.get(kwargs["site_name"][0], frame())
        return out(kwargs) if callable(out) else out

    monkeypatch.setattr(jobspy, "scrape_jobs", fake)

    def configure(**search):
        raw = json.loads((workspace / "ohmyjob.json").read_text(encoding="utf-8"))
        raw["search"].update(dict(exclude_companies=[], exclude_seniority_level=[], filter_title_terms=[],
                                  title_aliases={}, title_only_aliases={}, search_query=["Operations Analyst"]))
        raw["search"].update(search)
        (workspace / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
        return W.load_config(workspace)["search"]

    return workspace, boards, calls, configure


def rows(root):
    return Tracker(root / "Job_Tracker.xlsx").rows()


def test_writes_folder_job_md_and_tracker_row(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(url="https://www.linkedin.com/jobs/view/111?trk=abc&utm_source=x"))
    boards["indeed"] = frame(post(title="Operations Analyst", company="Beta AG", url="https://de.indeed.com/viewjob?jk=abc123",
                                  job_url_direct="https://careers.beta.example/ops?utm_medium=x"))
    assert scrape.run(root, configure(sources=["linkedin", "indeed"])) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("linkedin: 1 scraped, 1 new") and out[1].startswith("indeed: 1 scraped, 1 new")
    assert out[-1].startswith("total: 2 scraped, 2 new jobs at Found")
    li = root / "Applications" / "111_Acme-GmbH_Operations-Analyst"
    ov = L.job_overview((li / "job.md").read_text(encoding="utf-8"))
    assert ov["Job URL"] == "https://www.linkedin.com/jobs/view/111" and ov["Source"] == "linkedin"
    assert ov["Company"] == "Acme GmbH" and ov["Location"] == "Berlin, Germany" and ov["Employment type"] == "Full-time"
    assert "Apply URL" not in ov and "Bring SQL." in L.job_description((li / "job.md").read_text(encoding="utf-8"))
    ind = next(p for p in (root / "Applications").iterdir() if p.name.endswith("Beta-AG_Operations-Analyst"))
    ov = L.job_overview((ind / "job.md").read_text(encoding="utf-8"))
    assert ov["Apply URL"] == "https://careers.beta.example/ops" and ov["Source"] == "indeed"
    got = {r["Source"]: r for r in rows(root)}
    assert got["linkedin"]["Status"] == "Found" and got["linkedin"]["_key"] == "111"
    assert got["indeed"]["Job URL"] == "https://de.indeed.com/viewjob?jk=abc123" and got["indeed"]["_key"] == job_key(got["indeed"]["Job URL"])
    assert ind.name.startswith(got["indeed"]["_key"] + "_")


def test_jobspy_arguments(setup):
    root, boards, calls, configure = setup
    scrape.run(root, configure(sources=["google", "indeed"], recency_days=2, results_per_term=7, location="Berlin, Germany",
                               country="germany", employment_types=["contract"]))
    google, indeed = calls
    assert google["site_name"] == ["google"] and indeed["site_name"] == ["indeed"]  # one board per call, in order
    assert google["google_search_term"] == "Operations Analyst jobs near Berlin, Germany in the last 3 days"
    assert "google_search_term" not in indeed
    for call in calls:
        assert call["hours_old"] == 48 and call["country_indeed"] == "germany" and call["results_wanted"] == 7
        assert call["search_term"] == "Operations Analyst" and call["location"] == "Berlin, Germany"
        assert "job_type" not in call  # the type is filtered afterwards, for every board


def test_employment_type_filter_runs_for_every_board(setup):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(url="https://www.linkedin.com/jobs/view/1", job_type=["internship"]),
                               post(url="https://www.linkedin.com/jobs/view/2", company="B", job_type=None),
                               post(url="https://www.linkedin.com/jobs/view/3", company="C", job_type=["contract", "fulltime"]))
    boards["google"] = frame(post(title="Operations Analyst", company="G Co", url="https://jobs.g.example/1", job_type=["parttime"]),
                             post(title="Operations Analyst", company="H Co", url="https://jobs.g.example/2", job_type=None))
    scrape.run(root, configure(sources=["linkedin", "google"], employment_types=["fulltime", "contract"]))
    # LinkedIn without a type means its detail page did not load (dropped, not tracked); another board may leave it out
    assert sorted(r["_key"] for r in rows(root) if r["Source"] == "linkedin") == ["3"]
    assert [r["Company"] for r in rows(root) if r["Source"] == "google"] == ["H Co"]


def test_title_filter_and_off(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(title="Head Chef", url="https://www.linkedin.com/jobs/view/5"))
    scrape.run(root, configure())
    assert "1 title dropped" in capsys.readouterr().out and rows(root) == []
    scrape.run(root, configure(title_filter="off"))
    assert [r["_key"] for r in rows(root)] == ["5"]


def test_excludes_are_applied(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(
        post(url="https://www.linkedin.com/jobs/view/1", company="Example Staffing Ltd"),
        post(title="Director Operations Analyst", url="https://www.linkedin.com/jobs/view/2", company="B"),
        post(title="Sales Operations Analyst", url="https://www.linkedin.com/jobs/view/3", company="C"),
        post(url="https://www.linkedin.com/jobs/view/4", company="D"))
    scrape.run(root, configure(exclude_companies=["Example Staffing"], exclude_seniority_level=["director"],
                               filter_title_terms=["sales"]))
    out = capsys.readouterr().out
    assert "1 level, 1 term" in out and "1 auto-excluded" in out
    got = {r["_key"]: r for r in rows(root)}
    assert sorted(got) == ["1", "4"]
    # an excluded company is recorded as Deleted with a note, and gets no folder
    assert got["1"]["Status"] == "Deleted" and "excluded entry 'Example Staffing'" in got["1"]["Notes"]
    assert got["4"]["Status"] == "Found"
    assert not list((root / "Applications").glob("1_*")) and list((root / "Applications").glob("4_*"))
    scrape.run(root, configure(exclude_companies=["Example Staffing"]))
    assert "2 known dropped" in capsys.readouterr().out  # keys 1 and 4 are met again: known, not added twice


def test_twin_from_later_source_is_dropped_and_order_decides(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(title="Operations Analyst (m/f/d)", location={"city": "Berlin", "state": "BE", "country": "germany"}))
    boards["indeed"] = frame(post(company="ACME", url="https://de.indeed.com/viewjob?jk=zz"),
                             post(title="Operations Analyst", company="Other", url="https://de.indeed.com/viewjob?jk=yy"))
    scrape.run(root, configure(sources=["linkedin", "indeed"]))
    assert "1 twin dropped" in capsys.readouterr().out
    assert sorted(r["Source"] + ":" + r["Company"] for r in rows(root)) == ["indeed:Other", "linkedin:Acme GmbH"]


def test_twin_priority_follows_sources_order(setup):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    boards["indeed"] = frame(post(url="https://de.indeed.com/viewjob?jk=zz"))
    scrape.run(root, configure(sources=["indeed", "linkedin"]))
    assert [r["Source"] for r in rows(root)] == ["indeed"]


def test_twin_of_recent_tracker_row_only(setup):
    root, boards, calls, configure = setup
    tracker = Tracker(root / "Job_Tracker.xlsx")
    tracker.add_row({"Job URL": "https://other.example/a", "Status": "Submitted", "Company": "Acme", "Job Title": "Operations Analyst",
                     "Date Found": (date.today() - timedelta(days=3)).isoformat() + " 08:00", "Location": "Berlin"})
    tracker.add_row({"Job URL": "https://other.example/b", "Status": "Found", "Company": "Old Co", "Job Title": "Operations Analyst",
                     "Date Found": (date.today() - timedelta(days=90)).isoformat() + " 08:00", "Location": "Berlin"})
    tracker.save()
    boards["linkedin"] = frame(post(), post(company="Old Co", url="https://www.linkedin.com/jobs/view/222"))
    scrape.run(root, configure(), )
    assert sorted(r["Company"] for r in rows(root)) == ["Acme", "Old Co", "Old Co"]
    assert [r["_key"] for r in rows(root) if r["Source"] == "linkedin"] == ["222"]


def test_existing_folder_and_row_are_skipped_never_overwritten(setup, capsys):
    root, boards, calls, configure = setup
    folder = root / "Applications" / "111_Acme-GmbH_Operations-Analyst"
    folder.mkdir()
    (folder / "job.md").write_text("MINE", encoding="utf-8")
    pending = root / "Pending-Review" / "222_Done_Operations-Analyst"
    pending.mkdir()
    boards["linkedin"] = frame(post(), post(url="https://www.linkedin.com/jobs/view/222", company="Done"))
    scrape.run(root, configure())
    assert (folder / "job.md").read_text(encoding="utf-8") == "MINE" and rows(root) == []
    assert "2 known dropped" in capsys.readouterr().out


def test_same_posting_twice_keeps_one_key(setup, capsys):
    root, boards, calls, configure = setup
    first = "https://jobs.example.org/p/42?id=7&gclid=AAA&utm_campaign=google_jobs_apply&fbclid=B"
    second = "https://jobs.example.org/p/42?id=7&gclid=ZZZ&utm_campaign=other&fbclid=C"
    assert job_key(strip_tracking(first)) == job_key(strip_tracking(second))
    boards["google"] = frame(post(url=first))
    scrape.run(root, configure(sources=["google"]))
    boards["google"] = frame(post(url=second))
    scrape.run(root, configure(sources=["google"]))
    assert len(rows(root)) == 1 and len(list((root / "Applications").glob("9*_Acme-GmbH_*"))) == 1
    assert "1 known dropped" in capsys.readouterr().out.splitlines()[-2]


def test_board_error_is_reported_and_all_failing_is_fatal(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(errors={"linkedin": "response status code 429 (blocked: use a proxy)"})
    boards["indeed"] = frame(post(url="https://de.indeed.com/viewjob?jk=a1"))
    with pytest.raises(scrape.Stop, match="every board failed"):
        scrape.run(root, configure(sources=["linkedin"]))
    assert "linkedin: 0 scraped, 0 new ERROR 'Operations Analyst': response status code 429" in capsys.readouterr().out
    assert scrape.run(root, configure(sources=["linkedin", "indeed"])) == scrape.EXIT_BOARD_FAILED  # one board failed, the other ran
    assert len(rows(root)) == 1

    def boom(kwargs):
        raise ValueError("Invalid country_indeed")

    boards["linkedin"] = boom
    assert scrape.run(root, configure(sources=["linkedin", "indeed"])) == scrape.EXIT_BOARD_FAILED


def test_tracker_saved_during_the_scrape_is_not_a_conflict(setup):
    root, boards, calls, configure = setup

    def meanwhile(kwargs):
        other = Tracker(root / "Job_Tracker.xlsx")
        other.add_row({"Job URL": "https://other.example/x", "Status": "Found", "Company": "Z", "Job Title": "Z"})
        other.save()
        return frame(post())

    boards["linkedin"] = meanwhile
    assert scrape.run(root, configure()) == 0  # the tracker is loaded after the scrape, so both rows are kept
    assert sorted(r["Company"] for r in rows(root)) == ["Acme GmbH", "Z"]


def test_stale_tracker_at_save_is_a_stop(setup, monkeypatch):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    real = scrape.open_tracker
    opened = []

    def opener(path):
        tracker = real(path)
        opened.append(tracker)
        if len(opened) == 2:  # the load after the scrape: someone saves before our save
            other = Tracker(path / "Job_Tracker.xlsx")
            other.add_row({"Job URL": "https://other.example/x", "Status": "Found", "Company": "Z", "Job Title": "Z"})
            other.save()
        return tracker

    monkeypatch.setattr(scrape, "open_tracker", opener)
    with pytest.raises(scrape.Stop, match="changed on disk"):
        scrape.run(root, configure())
    assert [r["Company"] for r in rows(root)] == ["Z"]


def test_dry_run_writes_nothing(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    assert scrape.run(root, configure(), dry_run=True) == 0
    assert "1 would be new" in capsys.readouterr().out
    assert rows(root) == [] and not any((root / "Applications").glob("111*"))


def test_git_snapshot_opt_in(setup, capsys, monkeypatch):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    scrape.run(root, configure(git_snapshot=True))
    assert "git_snapshot: the workspace is not a git repo; skipped" in capsys.readouterr().out
    for name, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@e.x"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@e.x")):
        monkeypatch.setenv(name, value)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    boards["linkedin"] = frame(post(url="https://www.linkedin.com/jobs/view/333"))
    scrape.run(root, configure(git_snapshot=True))
    log = subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert log.startswith("ohmyjob snapshot")
    shown = subprocess.run(["git", "-C", str(root), "show", "--stat", "--format="], capture_output=True, text=True).stdout
    assert "Job_Tracker.xlsx" in shown and "111_Acme-GmbH" in shown and "333_" not in shown  # the state before this run
    before = len(log.splitlines())
    scrape.run(root, configure(git_snapshot=False))
    assert len(subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout.splitlines()) == before


def test_main_prints_fatal_lines(workspace, capsys, monkeypatch):
    raw = json.loads((workspace / "ohmyjob.json").read_text(encoding="utf-8"))
    raw["search"]["search_query"] = []
    (workspace / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
    assert scrape.main([]) == 1
    assert capsys.readouterr().out.startswith("FATAL: search.search_query is empty")
    raw["search"]["search_query"] = ["x"]
    (workspace / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
    assert scrape.main([]) == 1  # no tracker yet
    assert "FATAL: Tracker not found" in capsys.readouterr().out
    raw["search"]["sources"] = ["nope"]
    (workspace / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
    assert scrape.main([]) == 1
    assert capsys.readouterr().out.startswith("FATAL: ")


def test_fetch_description_is_always_requested(setup):
    root, boards, calls, configure = setup
    scrape.run(root, configure(sources=["linkedin", "google", "indeed"]))
    assert len(calls) == 3 and all(c["fetch_description"] is True for c in calls)


def test_google_linkedin_slug_links_get_the_linkedin_key(setup):
    root, boards, calls, configure = setup
    boards["google"] = frame(post(url="https://www.linkedin.com/jobs/view/operations-analyst-at-acme-4399000077?utm_source=google"))
    scrape.run(root, configure(sources=["google"]))
    assert [(r["_key"], r["Job URL"]) for r in rows(root)] == [("4399000077", "https://www.linkedin.com/jobs/view/4399000077")]


def test_board_warnings_are_printed(setup, capsys):
    root, boards, calls, configure = setup
    boards["glassdoor"] = frame()
    boards["glassdoor"].attrs = {"errors": {}, "warnings": {"glassdoor": ["country not supported"]}}
    scrape.run(root, configure(sources=["glassdoor"]))
    assert "WARNING glassdoor: country not supported" in capsys.readouterr().out


def test_job_level_is_a_whole_value_and_aliases_apply(setup):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(
        post(url="https://www.linkedin.com/jobs/view/1", company="A", job_level="Mid-Senior level"),
        post(title="Sr. Operations Analyst", url="https://www.linkedin.com/jobs/view/2", company="B"),
        post(url="https://www.linkedin.com/jobs/view/3", company="C", job_level="Senior"),
        post(url="https://www.linkedin.com/jobs/view/4", company="D", job_level="Director"))
    scrape.run(root, configure(exclude_seniority_level=["senior"], title_aliases={"sr": "senior"}))
    assert sorted(r["_key"] for r in rows(root)) == ["1", "4"]


def test_known_posting_of_an_earlier_board_still_shadows_its_twin(setup):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    boards["indeed"] = frame(post(url="https://de.indeed.com/viewjob?jk=zz"))
    scrape.run(root, configure(sources=["linkedin"]))
    scrape.run(root, configure(sources=["linkedin", "indeed"]))  # linkedin's copy is known now; indeed's is its twin
    assert [r["Source"] for r in rows(root)] == ["linkedin"]


def test_decided_tracker_rows_never_age_out_and_need_the_same_city(setup):
    root, boards, calls, configure = setup
    tracker = Tracker(root / "Job_Tracker.xlsx")
    old = "2024-01-01 08:00"
    tracker.add_row({"Job URL": "https://o.example/1", "Status": "Submitted", "Company": "Acme", "Job Title": "Operations Analyst",
                     "Date Found": old, "Location": "Berlin"})
    tracker.add_row({"Job URL": "https://o.example/2", "Status": "Found", "Company": "NoCity", "Job Title": "Operations Analyst",
                     "Date Found": date.today().isoformat() + " 08:00"})
    tracker.save()
    boards["linkedin"] = frame(post(), post(company="NoCity", url="https://www.linkedin.com/jobs/view/9"))
    scrape.run(root, configure())
    assert [r["_key"] for r in rows(root) if r["Source"] == "linkedin"] == ["9"]


def test_git_snapshot_commits_only_its_paths(setup, monkeypatch):
    root, boards, calls, configure = setup
    for name, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@e.x"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@e.x")):
        monkeypatch.setenv(name, value)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "notes.txt").write_text("mine", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "notes.txt"], check=True)
    scrape.run(root, configure(git_snapshot=True))
    shown = subprocess.run(["git", "-C", str(root), "show", "--stat", "--format="], capture_output=True, text=True).stdout
    assert "Job_Tracker.xlsx" in shown and "notes.txt" not in shown
    staged = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--name-only"], capture_output=True, text=True).stdout
    assert staged.split() == ["notes.txt"]


def test_unexpected_errors_are_one_fatal_line(workspace, capsys, monkeypatch):
    def broken(root, search, dry_run=False):
        raise PermissionError("denied: Applications")

    monkeypatch.setattr(scrape, "run", broken)
    assert scrape.main([]) == scrape.EXIT_FATAL
    assert capsys.readouterr().out == "FATAL: unexpected PermissionError: denied: Applications\n"


# --------------------------------------------------------------------------- freehire as a source

import freehire_source  # noqa: E402


def fh_item(n=1, **more):
    base = {"public_slug": f"slug-{n}", "source": "greenhouse", "url": f"https://boards.example.org/acme/jobs/{n}?utm_source=freehire.me",
            "title": "Operations Analyst", "company": "Freehire Co", "location": "Berlin, Germany",
            "description": "## About\nKeep it running.", "collections": [], "posted_at": "2026-11-01T00:00:00Z",
            "enrichment": {"employment_type": "full_time"}}
    base.update(more)
    return base


@pytest.fixture
def freehire(monkeypatch):
    class Api:
        calls = []
        items = []

    api = Api()
    api.calls = []
    monkeypatch.setattr(freehire_source.time, "sleep", lambda s: None)

    def fetch(params, probe=False):
        if params["limit"] != 1:  # a one-result request only explains an empty search
            api.calls.append(dict(params))
        return {"data": list(api.items) if params["offset"] == 0 and params["limit"] != 1 else [],
                "meta": {"total": len(api.items) if params["limit"] != 1 else 0}}

    monkeypatch.setattr(freehire_source, "_fetch", fetch)
    return api


def test_freehire_rows_get_a_folder_a_source_and_a_note(setup, freehire, capsys):
    root, boards, calls, configure = setup
    freehire.items = [fh_item(1), fh_item(2, title="Operations Analyst II", company="Other Co", source="reed",
                                          url="https://www.reed.example/jobs/22?gclid=zz")]
    assert scrape.run(root, configure(sources=["freehire"], recency_days=3)) == 0
    assert calls == [] and freehire.calls[0]["open_within_days"] == 3 and freehire.calls[0]["countries"] == "DE"
    assert "freehire: 2 scraped, 2 new" in capsys.readouterr().out
    got = sorted(rows(root), key=lambda r: r["Company"])
    assert [r["Source"] for r in got] == ["freehire", "freehire"]
    assert got[0]["Notes"] == "Source: freehire/greenhouse" and got[1]["Job URL"] == "https://www.reed.example/jobs/22"
    folder = next(p for p in (root / "Applications").iterdir() if p.name.startswith(got[0]["_key"] + "_"))
    assert folder.name.endswith("_Freehire-Co_Operations-Analyst")
    text = (folder / "job.md").read_text(encoding="utf-8")
    ov = L.job_overview(text)
    assert ov["Source"] == "freehire" and ov["Listed via"] == "greenhouse" and ov["Job URL"] == got[0]["Job URL"]
    assert "#### About" in text and "Apply URL" not in ov


def test_uk_sponsor_only_drives_the_collection_parameter(setup, freehire):
    root, boards, calls, configure = setup
    scrape.run(root, configure(sources=["freehire"], country="uk", location="London, UK"))
    scrape.run(root, configure(sources=["freehire"], country="uk", location="London, UK", uk_sponsor_only=True))
    assert "collections" not in freehire.calls[0] and freehire.calls[1]["collections"] == "uk-skilled-worker-sponsor"


def test_freehire_seniority_label_feeds_exclude_seniority_level(setup, freehire):
    root, boards, calls, configure = setup
    level = lambda label: {"employment_type": "full_time", "seniority": label}  # noqa: E731
    freehire.items = [fh_item(1, company="A", enrichment=level("lead")), fh_item(2, company="B", enrichment=level("senior")),
                      fh_item(3, company="C", enrichment=level(None)), fh_item(4, company="D", enrichment=level("c_level"))]
    scrape.run(root, configure(sources=["freehire"], exclude_seniority_level=["lead", "c_level"]))
    assert sorted(r["Company"] for r in rows(root)) == ["B", "C"]


def test_freehire_without_a_country_warns_and_the_other_boards_run(setup, freehire, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    assert scrape.run(root, configure(sources=["freehire", "linkedin"], location="Atlantis", country="")) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("WARNING freehire: no country found") and freehire.calls == []
    assert len(rows(root)) == 1


def test_freehire_twin_of_a_linkedin_job_is_dropped_and_freehire_first_wins_if_listed_first(setup, freehire):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(company="Freehire Co"))
    freehire.items = [fh_item(1)]
    scrape.run(root, configure(sources=["linkedin", "freehire"]))
    assert [r["Source"] for r in rows(root)] == ["linkedin"]
    root2 = root  # a second run in the other order: the tracker already holds the LinkedIn copy (known twin)
    freehire.items = [fh_item(2)]
    scrape.run(root2, configure(sources=["freehire", "linkedin"]))
    assert [r["Source"] for r in rows(root2)] == ["linkedin"]


def test_freehire_failure_is_an_error_line_and_exit_2(setup, freehire, monkeypatch, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())

    def boom(params, probe=False):
        raise OSError("down")

    monkeypatch.setattr(freehire_source, "_fetch", boom)
    monkeypatch.setattr(freehire_source.time, "sleep", lambda s: None)
    assert scrape.run(root, configure(sources=["linkedin", "freehire"])) == scrape.EXIT_BOARD_FAILED
    assert "freehire: 0 scraped, 0 new ERROR" in capsys.readouterr().out and len(rows(root)) == 1


# --------------------------------------------------------------------------- behaviour kept from the first finder


def test_same_title_and_company_under_another_id_is_one_job(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(url="https://www.linkedin.com/jobs/view/1"), post(url="https://www.linkedin.com/jobs/view/2"),
                               post(url="https://www.linkedin.com/jobs/view/3", company="Other"),
                               post(url="https://www.linkedin.com/jobs/view/4", title="Operations Analyst II"))
    scrape.run(root, configure())
    assert "1 duplicate dropped" in capsys.readouterr().out
    assert sorted(r["_key"] for r in rows(root)) == ["1", "3", "4"]


def test_the_same_job_met_under_two_terms_is_kept_once_in_term_order(setup, capsys):
    root, boards, calls, configure = setup

    def by_term(kwargs):
        title = {"Operations Analyst": "Operations Analyst", "Project Coordinator": "Project Coordinator"}[kwargs["search_term"]]
        return frame(post(title=title, url="https://www.linkedin.com/jobs/view/9"))

    boards["linkedin"] = by_term
    scrape.run(root, configure(sources=["linkedin"], search_query=["Operations Analyst", "Project Coordinator"]))
    assert "linkedin: 2 scraped, 1 new (1 dup id dropped)" in capsys.readouterr().out
    assert [r["Job Title"] for r in rows(root)] == ["Operations Analyst"]  # the first term's copy
    assert sorted(c["search_term"] for c in calls) == ["Operations Analyst", "Project Coordinator"]


def test_terms_run_at_once_but_never_more_than_the_cap(setup, monkeypatch):
    import threading
    import time

    root, boards, calls, configure = setup
    live, peak, lock = [0], [0], threading.Lock()

    def slow(kwargs):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return frame()

    boards["linkedin"] = slow
    monkeypatch.setitem(scrape.BOARD_WORKERS, "linkedin", 3)
    scrape.run(root, configure(sources=["linkedin"], search_query=[f"term {n}" for n in range(8)], title_filter="off"))
    assert 2 <= peak[0] <= 3 and len(calls) == 8


def test_twin_window_and_acted_rows(setup):
    root, boards, calls, configure = setup
    tracker = Tracker(root / "Job_Tracker.xlsx")
    forty_days_ago = (date.today() - timedelta(days=40)).isoformat() + " 08:00"
    for n, (status, company) in enumerate([("Found", "Old Found"), ("Pending Review", "Old Pending"),
                                           ("needs attention", "Old Attention")]):
        tracker.add_row({"Job URL": f"https://o.example/{n}", "Status": status if status != "needs attention" else "Needs Attention",
                         "Company": company, "Job Title": "Operations Analyst", "Date Found": forty_days_ago, "Location": "Berlin"})
    tracker.save()
    boards["linkedin"] = frame(*[post(company=c, url=f"https://www.linkedin.com/jobs/view/{n}")
                                 for n, c in enumerate(["Old Found", "Old Pending", "Old Attention"], 1)])
    # recency_days 2: the window is 30 days, so a 40-day-old "Found" row is out of it; acted rows never expire
    scrape.run(root, configure(recency_days=2))
    assert sorted(r["Company"] for r in rows(root) if r["Source"] == "linkedin") == ["Old Found"]


def test_recency_zero_checks_every_tracker_row(setup):
    root, boards, calls, configure = setup
    tracker = Tracker(root / "Job_Tracker.xlsx")
    tracker.add_row({"Job URL": "https://o.example/1", "Status": "Found", "Company": "Acme GmbH", "Job Title": "Operations Analyst",
                     "Date Found": "2020-01-01 08:00", "Location": "Berlin"})
    tracker.save()
    boards["linkedin"] = frame(post())
    scrape.run(root, configure(recency_days=0))
    assert [r["Company"] for r in rows(root)] == ["Acme GmbH"]  # only the old row: the new posting is its twin
    assert calls[0]["hours_old"] is None


def test_a_job_the_board_gave_no_details_for_gets_neutral_placeholders(setup):
    root, boards, calls, configure = setup
    boards["indeed"] = frame(post(url="https://de.indeed.com/viewjob?jk=aa", description=None, location=None,
                                  date_posted=None, job_type=None))
    scrape.run(root, configure(sources=["indeed"]))
    folder = next(p for p in (root / "Applications").iterdir() if "Acme" in p.name)
    text = (folder / "job.md").read_text(encoding="utf-8")
    ov = L.job_overview(text)
    assert (ov["Date Posted"], ov["Location"], ov["Employment type"]) == ("Not available", "Not specified", "Not specified")
    assert "No description available." in text


def test_each_board_has_its_own_term_parallelism(setup, monkeypatch):
    import threading
    import time

    assert scrape.workers_for("linkedin") == 10
    assert all(scrape.workers_for(b) == 2 for b in ("indeed", "glassdoor", "zip_recruiter", "google", "bayt", "naukri"))
    root, boards, calls, configure = setup
    live, peak, lock = [0], [0], threading.Lock()

    def slow(kwargs):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.05)
        with lock:
            live[0] -= 1
        return frame()

    boards["indeed"] = slow
    scrape.run(root, configure(sources=["indeed"], search_query=[f"term {n}" for n in range(8)], title_filter="off"))
    assert peak[0] == 2 and len(calls) == 8


def test_one_role_in_two_cities_is_two_jobs(setup, capsys):
    root, boards, calls, configure = setup
    paris = {"city": "Paris", "country": "france"}
    boards["linkedin"] = frame(post(url="https://www.linkedin.com/jobs/view/1", location=paris),
                               post(url="https://www.linkedin.com/jobs/view/2", location={"city": "Lyon", "country": "france"}),
                               post(url="https://www.linkedin.com/jobs/view/3", location=paris))
    scrape.run(root, configure(sources=["linkedin"]))
    assert "1 duplicate dropped" in capsys.readouterr().out
    assert sorted(r["_key"] for r in rows(root)) == ["1", "2"]


def test_freehire_unusable_and_repeated_results_are_counted_one_by_one(setup, freehire, capsys):
    root, boards, calls, configure = setup
    freehire.items = [fh_item(1), fh_item(2, company=""), fh_item(3, enrichment={"employment_type": "internship"})]
    scrape.run(root, configure(sources=["freehire"], search_query=["Operations Analyst", "Project Coordinator"]))
    out = capsys.readouterr().out
    # per term: 3 read, 2 unusable; the second term meets job 1 again
    assert "freehire: 6 scraped, 1 new (4 bad, 1 dup id dropped)" in out


def test_a_failed_job_md_write_leaves_no_folder(setup, monkeypatch):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())

    def broken(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(scrape.Path, "write_text", broken)
    with pytest.raises(OSError):
        scrape.run(root, configure(sources=["linkedin"]))
    assert not list((root / "Applications").glob("111_*")) and scrape.known_keys(root).isdisjoint({"111"})


def test_git_snapshot_respects_gitignore(setup, capsys, monkeypatch):
    root, boards, calls, configure = setup
    for name, value in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@e.x"), ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@e.x")):
        monkeypatch.setenv(name, value)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text("Job_Tracker.xlsx\nApplications/\n", encoding="utf-8")
    scrape.run(root, configure(git_snapshot=True))
    assert "ignored by .gitignore; nothing to snapshot" in capsys.readouterr().out
    log = subprocess.run(["git", "-C", str(root), "log", "--format=%s"], capture_output=True, text=True).stdout
    assert log == ""
    (root / ".gitignore").write_text("Applications/\n", encoding="utf-8")  # one of the two is versioned now
    scrape.run(root, configure(git_snapshot=True))
    shown = subprocess.run(["git", "-C", str(root), "show", "--stat", "--format=%s"], capture_output=True, text=True).stdout
    assert "ohmyjob snapshot" in shown and "Job_Tracker.xlsx" in shown and "Applications" not in shown
