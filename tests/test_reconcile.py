"""scripts/reconcile.py: the new rule per folder location, dry run, --apply, exit codes and the invariant."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from job_key import job_key  # noqa: E402
from tracker import Tracker  # noqa: E402

OTHER_URL = "https://jobs.example.com/careers/opening?id=42&utm_source=x"


def url(n):
    return f"https://www.linkedin.com/jobs/view/{n}"


def run(workspace, *args):
    env = {**os.environ, "OHMYJOB_WORKSPACE": str(workspace)}
    return subprocess.run([sys.executable, str(SCRIPTS / "reconcile.py"), *args], capture_output=True, text=True, env=env)


def make(workspace, rows):
    """rows: [(job url, status)]. Returns the tracker."""
    shutil.rmtree(workspace / "Applications")  # the fixture's two sample folders have no rows here
    (workspace / "Applications").mkdir()
    t = Tracker.create(workspace / "Job_Tracker.xlsx")
    for u, status in rows:
        t.add_row({"Job URL": u, "Status": status, "Company": "Acme", "Job Title": "Analyst"})
    t.save()
    return Tracker(workspace / "Job_Tracker.xlsx")


def folder(workspace, where, key, name="Acme_Analyst"):
    path = workspace / where / f"{key}_{name}"
    path.mkdir(parents=True)
    (path / "job.md").write_text("x")
    return path


def status(workspace, n):
    return {r["_index"]: r for r in Tracker(workspace / "Job_Tracker.xlsx").rows()}[n]


def test_pending_review_folder_becomes_submitted_and_moves(workspace):
    make(workspace, [(url(1001), "Pending Review")])
    folder(workspace, "Pending-Review", 1001)
    r = run(workspace, "--apply")
    assert r.returncode == 0, r.stdout + r.stderr
    assert status(workspace, 1)["Status"] == "Submitted"
    assert "folder found in Pending-Review/" in status(workspace, 1)["Notes"]
    assert (workspace / "Submitted" / "1001_Acme_Analyst" / "job.md").exists()
    assert not (workspace / "Pending-Review" / "1001_Acme_Analyst").exists()


def test_needs_attention_folder_becomes_submitted_and_moves(workspace):
    make(workspace, [(url(1002), "Needs Attention")])
    folder(workspace, "Needs-Attention", 1002)
    assert run(workspace, "--apply").returncode == 0
    assert status(workspace, 1)["Status"] == "Submitted"
    assert (workspace / "Submitted" / "1002_Acme_Analyst").is_dir()


def test_other_board_key_matches_its_folder(workspace):
    make(workspace, [(OTHER_URL, "Pending Review")])
    folder(workspace, "Pending-Review", job_key(OTHER_URL))
    assert run(workspace, "--apply").returncode == 0
    assert status(workspace, 1)["Status"] == "Submitted"


def test_no_folder_becomes_deleted(workspace):
    make(workspace, [(url(1003), "Resume Built")])
    assert run(workspace, "--apply").returncode == 0
    row = status(workspace, 1)
    assert row["Status"] == "Deleted"
    assert "no folder found" in row["Notes"]


def test_applications_folder_is_left_alone(workspace):
    make(workspace, [(url(1004), "Resume Built")])
    path = folder(workspace, "Applications", 1004)
    r = run(workspace, "--apply")
    assert r.returncode == 0
    assert status(workspace, 1)["Status"] == "Resume Built"
    assert status(workspace, 1)["Notes"] is None
    assert path.is_dir()


def test_open_row_with_folder_already_in_submitted(workspace):
    make(workspace, [(url(1005), "Pending Review")])
    folder(workspace, "Submitted", 1005)
    assert run(workspace, "--apply").returncode == 0
    assert status(workspace, 1)["Status"] == "Submitted"
    assert (workspace / "Submitted" / "1005_Acme_Analyst").is_dir()


def test_terminal_rows_are_never_written(workspace):
    make(workspace, [(url(1006), "Skipped-LowFit"), (url(1007), "Deleted")])
    before = (workspace / "Job_Tracker.xlsx").read_bytes()
    assert run(workspace, "--apply").returncode == 0
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == before


def test_dry_run_is_the_default_and_writes_nothing(workspace):
    make(workspace, [(url(1008), "Pending Review"), (url(1009), "Matched")])
    folder(workspace, "Pending-Review", 1008)
    before = (workspace / "Job_Tracker.xlsx").read_bytes()
    for args in ((), ("--dry-run",)):
        r = run(workspace, *args)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "DRY RUN" in r.stdout
        assert "Rows -> Submitted: 1" in r.stdout and "Rows -> Deleted:   1" in r.stdout
        assert (workspace / "Job_Tracker.xlsx").read_bytes() == before
        assert (workspace / "Pending-Review" / "1008_Acme_Analyst").is_dir()
        assert not (workspace / "Submitted" / "1008_Acme_Analyst").exists()


def test_apply_and_dry_run_together_are_refused(workspace):
    make(workspace, [(url(1010), "Found")])
    assert run(workspace, "--apply", "--dry-run").returncode == 2


def test_second_apply_changes_nothing(workspace):
    make(workspace, [(url(1011), "Pending Review"), (url(1012), "Found")])
    folder(workspace, "Pending-Review", 1011)
    assert run(workspace, "--apply").returncode == 0
    after = (workspace / "Job_Tracker.xlsx").read_bytes()
    r = run(workspace, "--apply")
    assert r.returncode == 0 and "no changes planned" in r.stdout
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == after


def test_unresolved_folder_exits_1_but_applies_the_rest(workspace):
    make(workspace, [(url(1013), "Pending Review")])
    folder(workspace, "Pending-Review", 1013)
    folder(workspace, "Pending-Review", 5555)  # no row has this id
    r = run(workspace, "--apply")
    assert r.returncode == 1
    assert "unresolved (no-match)" in r.stdout and "Unresolved folders: 1" in r.stdout
    assert status(workspace, 1)["Status"] == "Submitted"
    assert (workspace / "Pending-Review" / "5555_Acme_Analyst").is_dir()


def test_folder_without_a_leading_id_is_ignored(workspace):
    make(workspace, [(url(1014), "Found")])
    (workspace / "Pending-Review" / "Acme_Analyst").mkdir()
    r = run(workspace)
    assert "Unresolved folders: 0" in r.stdout


def test_duplicate_rows_are_left_untouched_and_exit_1(workspace):
    t = make(workspace, [(url(1015), "Pending Review"), (url(1015), "Pending Review")])
    folder(workspace, "Pending-Review", 1015)
    r = run(workspace, "--apply")
    assert r.returncode == 1 and "ambiguous-row" in r.stdout
    assert status(workspace, 1)["Status"] == "Pending Review"
    assert status(workspace, 2)["Status"] == "Pending Review"
    assert (workspace / "Pending-Review" / "1015_Acme_Analyst").is_dir()


def test_duplicate_note_goes_on_the_newer_row_once(workspace):
    t = Tracker.create(workspace / "Job_Tracker.xlsx")
    t.add_row({"Job URL": url(1020), "Status": "Submitted", "Date Found": "2026-01-01"})
    t.add_row({"Job URL": url(1020), "Status": "Matched", "Date Found": "2026-02-01"})
    t.save()
    shutil.rmtree(workspace / "Applications")
    folder(workspace, "Submitted", 1020)
    assert run(workspace, "--apply").returncode == 1
    older, newer = status(workspace, 1), status(workspace, 2)
    assert older["Notes"] is None
    assert newer["Notes"].count("previously exists with status=Submitted") == 1
    after = (workspace / "Job_Tracker.xlsx").read_bytes()
    run(workspace, "--apply")
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == after


def test_invariant_failure_exits_2_and_writes_nothing(workspace):
    # A Submitted row without a Submitted folder.
    make(workspace, [(url(1016), "Submitted"), (url(1017), "Pending Review")])
    folder(workspace, "Pending-Review", 1017)
    before = (workspace / "Job_Tracker.xlsx").read_bytes()
    r = run(workspace, "--apply")
    assert r.returncode == 2
    assert "2 Submitted rows == 1 Submitted folders  FAIL" in r.stdout
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == before
    assert (workspace / "Pending-Review" / "1017_Acme_Analyst").is_dir()


def test_missing_tracker_is_fatal(workspace):
    r = run(workspace)
    assert r.returncode == 3 and r.stderr.startswith("FATAL")


def test_stale_tracker_is_fatal_and_nothing_moves(workspace, monkeypatch):
    make(workspace, [(url(1018), "Pending Review")])
    folder(workspace, "Pending-Review", 1018)
    monkeypatch.setenv("OHMYJOB_WORKSPACE", str(workspace))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("workspace", "reconcile"):
        sys.modules.pop(name, None)
    import reconcile as rc

    # Another run saves the tracker after this run loaded it.
    original = rc.Tracker

    class Racing(original):
        def save(self):
            other = original(self.path)
            other.set_cell(1, "Notes", "edited elsewhere")
            other.save()
            super().save()

    rc.Tracker = Racing
    try:
        code = rc.main(["--apply"])
    except SystemExit as exc:
        code = exc.code
    finally:
        rc.Tracker = original
        sys.modules.pop("reconcile", None)
        sys.modules.pop("workspace", None)
    assert code == 3
    assert status(workspace, 1)["Status"] == "Pending Review"
    assert (workspace / "Pending-Review" / "1018_Acme_Analyst").is_dir()


def test_one_job_in_two_folders_is_left_alone(workspace):
    make(workspace, [(url(1019), "Pending Review")])
    folder(workspace, "Pending-Review", 1019)
    (workspace / "Submitted" / "1019_Acme_Analyst").mkdir()
    # two folders for one row: the row stays, and the invariant (0 Submitted rows, 1 Submitted folder) stops the run
    r = run(workspace, "--apply")
    assert r.returncode == 2 and "shared-folder" in r.stdout
    assert status(workspace, 1)["Status"] == "Pending Review"


def test_terminal_row_with_a_reviewed_folder_only_moves_the_folder(workspace):
    make(workspace, [(url(1021), "Submitted")])
    folder(workspace, "Needs-Attention", 1021)
    before = status(workspace, 1)
    r = run(workspace)
    assert "already Submitted, folder moves from Needs-Attention/ to Submitted/" in r.stdout
    assert run(workspace, "--apply").returncode == 0
    assert (workspace / "Submitted" / "1021_Acme_Analyst").is_dir()
    assert status(workspace, 1)["Notes"] == before["Notes"]


def test_created_submitted_dir(workspace):
    make(workspace, [(url(1022), "Pending Review")])
    folder(workspace, "Pending-Review", 1022)
    shutil.rmtree(workspace / "Submitted")
    assert run(workspace, "--apply").returncode == 0
    assert (workspace / "Submitted" / "1022_Acme_Analyst").is_dir()


@pytest.fixture
def rc(workspace, monkeypatch):
    monkeypatch.setenv("OHMYJOB_WORKSPACE", str(workspace))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    for name in ("workspace", "reconcile"):
        sys.modules.pop(name, None)
    import reconcile
    yield reconcile
    for name in ("workspace", "reconcile"):
        sys.modules.pop(name, None)


def test_failed_move_after_save_is_healed_by_the_next_run(workspace, rc, monkeypatch, capsys):
    make(workspace, [(url(1023), "Pending Review"), (url(1024), "Pending Review")])
    folder(workspace, "Pending-Review", 1023)
    folder(workspace, "Pending-Review", 1024)

    def broken(src, dest):
        raise PermissionError("denied")

    monkeypatch.setattr(rc.shutil, "move", broken)
    with pytest.raises(SystemExit) as exc:
        rc.main(["--apply"])
    assert exc.value.code == 3
    assert "FATAL: denied" in capsys.readouterr().err
    assert status(workspace, 1)["Status"] == "Submitted"  # saved, folders not moved
    monkeypatch.undo()
    r = run(workspace)
    assert r.returncode == 0, r.stdout
    assert r.stdout.count("already Submitted, folder moves") == 2
    r = run(workspace, "--apply")
    assert r.returncode == 0, r.stdout
    assert (workspace / "Submitted" / "1023_Acme_Analyst").is_dir()
    assert (workspace / "Submitted" / "1024_Acme_Analyst").is_dir()
    assert "Invariant: 2 Submitted rows == 2 Submitted folders  PASS" in run(workspace).stdout


def test_collision_precheck_refuses_before_any_write(workspace, rc, capsys):
    t = make(workspace, [(url(1025), "Pending Review")])
    src = folder(workspace, "Pending-Review", 1025)
    (workspace / "Submitted" / src.name).mkdir()
    entry = rc.FolderEntry(path=src, root=workspace / "Pending-Review", key="1025")
    row = t.rows()[0]
    before = (workspace / "Job_Tracker.xlsx").read_bytes()
    with pytest.raises(SystemExit) as exc:
        rc.apply_changes(t, [rc.Change(row, "Submitted", "note", entry)])
    assert exc.value.code == 3 and "already exists" in capsys.readouterr().err
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == before
    assert src.is_dir()


def test_digest_guards_apply(workspace):
    make(workspace, [(url(1026), "Pending Review"), (url(1027), "Matched")])
    folder(workspace, "Pending-Review", 1026)
    out = run(workspace).stdout
    digest = out.split("Plan digest: ")[1].split()[0]
    assert run(workspace).stdout == out  # stable
    # a folder is deleted after the dry run: the plan changes
    shutil.rmtree(workspace / "Pending-Review" / "1026_Acme_Analyst")
    before = (workspace / "Job_Tracker.xlsx").read_bytes()
    r = run(workspace, "--apply", "--expect", digest)
    assert r.returncode == 3 and "plan changed" in r.stderr
    assert (workspace / "Job_Tracker.xlsx").read_bytes() == before
    assert run(workspace, "--expect", digest).returncode == 2  # needs --apply


def test_matching_digest_applies(workspace):
    make(workspace, [(url(1028), "Pending Review")])
    folder(workspace, "Pending-Review", 1028)
    digest = run(workspace).stdout.split("Plan digest: ")[1].split()[0]
    assert run(workspace, "--apply", "--expect", digest).returncode == 0
    assert status(workspace, 1)["Status"] == "Submitted"


def test_invariant_failure_names_rows_and_folders_and_says_nothing_applied(workspace):
    make(workspace, [(url(1029), "Submitted"), (url(1030), "Submitted")])
    folder(workspace, "Submitted", 7777)  # a Submitted folder with no row
    r = run(workspace, "--apply")
    assert r.returncode == 2
    assert "NOTHING APPLIED" in r.stdout and "Folders moved" not in r.stdout
    assert "Submitted row without a folder in Submitted/: sheet row 2" in r.stdout
    assert "folder in Submitted/ without a Submitted row: 7777_Acme_Analyst" in r.stdout


def test_report_lines_show_sheet_rows_and_locations(workspace):
    make(workspace, [(url(1031), "Pending Review"), (url(1032), "Pending Review"), (url(1033), "Found")])
    folder(workspace, "Pending-Review", 1031)
    folder(workspace, "Submitted", 1032)
    out = run(workspace).stdout
    assert "sheet row 2: Acme - Analyst: Pending Review -> Submitted (folder in Pending-Review/ moves to Submitted/)" in out
    assert "sheet row 3: Acme - Analyst: Pending Review -> Submitted (already in Submitted/)" in out
    assert "sheet row 4: Acme - Analyst: Found -> Deleted (no folder)" in out


def test_unexpected_error_is_fatal_not_exit_1(workspace, rc, monkeypatch, capsys):
    make(workspace, [(url(1034), "Found")])

    def boom():
        raise PermissionError("no access")

    monkeypatch.setattr(rc, "scan_folders", boom)
    with pytest.raises(SystemExit) as exc:
        rc.main([])
    assert exc.value.code == 3 and "FATAL: PermissionError" in capsys.readouterr().err
