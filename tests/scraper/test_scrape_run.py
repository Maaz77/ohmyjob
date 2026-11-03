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
                               post(url="https://www.linkedin.com/jobs/view/2", job_type=None),
                               post(url="https://www.linkedin.com/jobs/view/3", job_type=["contract", "fulltime"]))
    boards["google"] = frame(post(title="Operations Analyst", company="G Co", url="https://jobs.g.example/1", job_type=["parttime"]))
    scrape.run(root, configure(sources=["linkedin", "google"], employment_types=["fulltime", "contract"]))
    assert sorted(r["_key"] for r in rows(root) if r["Source"] == "linkedin") == ["2", "3"]
    assert [r for r in rows(root) if r["Source"] == "google"] == []


def test_title_filter_and_off(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(title="Head Chef", url="https://www.linkedin.com/jobs/view/5"))
    scrape.run(root, configure())
    assert "1 title dropped" in capsys.readouterr().out and rows(root) == []
    scrape.run(root, configure(title_filter="off"))
    assert [r["_key"] for r in rows(root)] == ["5"]


def test_excludes_are_applied(setup):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(
        post(url="https://www.linkedin.com/jobs/view/1", company="Example Staffing Ltd"),
        post(title="Director Operations Analyst", url="https://www.linkedin.com/jobs/view/2"),
        post(title="Sales Operations Analyst", url="https://www.linkedin.com/jobs/view/3"),
        post(url="https://www.linkedin.com/jobs/view/4"))
    scrape.run(root, configure(exclude_companies=["Example Staffing"], exclude_seniority_level=["director"],
                               filter_title_terms=["sales"]))
    assert [r["_key"] for r in rows(root)] == ["4"]


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


def test_freehire_is_skipped_with_one_warning(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    assert scrape.run(root, configure(sources=["freehire", "linkedin"], uk_sponsor_only=True)) == 0
    out = capsys.readouterr().out.splitlines()
    assert len([line for line in out if "freehire" in line]) == 1 and out[0].startswith("WARNING freehire")
    assert [c["site_name"] for c in calls] == [["linkedin"]] and len(rows(root)) == 1
    with pytest.raises(scrape.Stop, match="freehire"):
        scrape.run(root, configure(sources=["freehire"]))


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
        post(url="https://www.linkedin.com/jobs/view/1", job_level="Mid-Senior level"),
        post(title="Sr. Operations Analyst", url="https://www.linkedin.com/jobs/view/2"),
        post(url="https://www.linkedin.com/jobs/view/3", job_level="Senior"),
        post(url="https://www.linkedin.com/jobs/view/4", job_level="Director"))
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
