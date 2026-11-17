"""scripts/undo_find_jobs.py: removes the latest run's Found/Deleted rows and folders, backs the tracker up in .ohmyjob/backup/."""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from tracker import Tracker  # noqa: E402

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def run(workspace, *args):
    env = {**os.environ, "OHMYJOB_WORKSPACE": str(workspace)}
    return subprocess.run([sys.executable, str(SCRIPTS / "undo_find_jobs.py"), *args], capture_output=True, text=True, env=env)


def test_apply_removes_the_run_and_backs_up_into_state_dir(workspace):
    apps = workspace / "Applications"
    for d in list(apps.iterdir()):
        __import__("shutil").rmtree(d)
    t = Tracker.create(workspace / "Job_Tracker.xlsx")
    for i, (st, d) in enumerate([("Found", "2026-11-16 09:00"), ("Found", "2026-11-17 09:00"), ("Deleted", "2026-11-17 09:00"), ("Submitted", "2026-11-17 09:00")]):
        t.add_row({"Job URL": f"https://www.linkedin.com/jobs/view/100000000{i}", "Status": st, "Company": "C", "Job Title": "T", "Date Found": d})
        (apps / f"100000000{i}_C_T").mkdir()
    t.save()
    dry = run(workspace)
    assert "Rows removed     : 2" in dry.stdout and len(list(apps.iterdir())) == 4
    digest = dry.stdout.split("Plan digest: ")[1].split()[0]
    out = run(workspace, "--apply", "--expect", digest)
    assert out.returncode == 0, out.stdout + out.stderr
    assert sorted(p.name for p in apps.iterdir()) == ["1000000000_C_T", "1000000003_C_T"]
    assert len(Tracker(workspace / "Job_Tracker.xlsx").rows()) == 2
    assert len(list((workspace / ".ohmyjob" / "backup").glob("Job_Tracker-*.pre-undo.xlsx"))) == 1
    assert not (workspace / "Job_Tracker.xlsx.pre-undo").exists()
