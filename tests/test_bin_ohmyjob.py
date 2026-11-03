"""bin/ohmyjob: finds the workspace, reads .ohmyjob/env.sh, runs the right script with the plugin's Python, and
re-syncs dependencies only for `prep start` and `apply start`."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT_FILES = {"prep": "pj.py", "apply": "aa.py", "scrape": "scrape.py", "reconcile": "reconcile.py", "resume": "resume_build.py"}

# Stands in for the real scripts: reports how it was called.
STUB_SCRIPT = (
    "import json, os, sys\n"
    "print(json.dumps({'script': os.path.basename(__file__), 'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
    "                  **{k: os.environ.get(k) for k in ('OHMYJOB_WORKSPACE', 'OHMYJOB_DATA', 'OHMYJOB_UV', 'OHMYJOB_LUALATEX')}}))\n"
    "sys.exit(int(os.environ.get('STUB_EXIT', '0')))\n"
)
LOCK = "version = 1\n# the plugin's lock\n"


@pytest.fixture
def plugin(tmp_path):
    """<plugin>/bin/ohmyjob (a copy), stub scripts, and a uv.lock."""
    root = tmp_path / "plugin"
    (root / "bin").mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy2(REPO / "bin" / "ohmyjob", root / "bin" / "ohmyjob")
    for name in SCRIPT_FILES.values():
        (root / "scripts" / name).write_text(STUB_SCRIPT)
    (root / "uv.lock").write_text(LOCK)
    return root


@pytest.fixture
def uv(tmp_path):
    """A stand-in for uv that logs how it was called (OHMYJOB_UV_LOG); it fails when STUB_UV_FAIL is set."""
    stub = tmp_path / "uv-bin" / "uv"
    stub.parent.mkdir()
    stub.write_text(
        '#!/bin/sh\n'
        'echo "chatter that must not reach stdout"\n'
        'echo "$UV_PROJECT_ENVIRONMENT|$*" >> "$OHMYJOB_UV_LOG"\n'
        '[ -z "$STUB_UV_FAIL" ] || exit 1\n'
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return stub


def run(plugin, *args, cwd, **env_extra):
    env = {"PATH": "/usr/bin:/bin", "OHMYJOB_PY": sys.executable, **env_extra}
    return subprocess.run([str(plugin / "bin" / "ohmyjob"), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def report(proc):
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return json.loads(proc.stdout)


# --------------------------------------------------------------------------- dispatch


@pytest.mark.parametrize("word, script", SCRIPT_FILES.items())
def test_each_word_runs_its_script_with_the_rest_of_the_arguments(plugin, workspace, word, script):
    out = report(run(plugin, word, "next", "--max", "3", cwd=workspace))
    assert out["script"] == script
    assert out["argv"] == ["next", "--max", "3"]


def test_the_exit_code_of_the_script_is_the_exit_code(plugin, workspace):
    assert run(plugin, "prep", "next", cwd=workspace, STUB_EXIT="7").returncode == 7


@pytest.mark.parametrize("args", [(), ("bogus",), ("--help",)])
def test_an_unknown_word_prints_the_usage(plugin, workspace, args):
    proc = run(plugin, *args, cwd=workspace)
    assert proc.returncode == 2
    assert proc.stdout.startswith("usage: ohmyjob <prep|apply|scrape|reconcile|resume>")


def test_a_script_missing_from_the_plugin_is_fatal(plugin, workspace):
    (plugin / "scripts" / "aa.py").unlink()
    proc = run(plugin, "apply", "next", cwd=workspace)
    assert proc.returncode == 3
    assert proc.stdout.startswith("ohmyjob: ") and "aa.py is missing" in proc.stdout


# --------------------------------------------------------------------------- the workspace


def test_the_workspace_is_found_from_a_folder_inside_it(plugin, workspace):
    inner = workspace / "Applications" / "4399000001_Lumenfield-Logistics_Operations-Analyst"
    out = report(run(plugin, "prep", "next", cwd=inner))
    assert Path(out["OHMYJOB_WORKSPACE"]).resolve() == workspace.resolve()


def test_the_environment_beats_the_walk_up_from_the_cwd(plugin, workspace, tmp_path):
    """The Bash tool sets no CLAUDE_PROJECT_DIR: the cwd is what production walks up from."""
    other = tmp_path / "other"
    other.mkdir()
    (other / "ohmyjob.json").write_text("{}")
    out = report(run(plugin, "prep", "next", cwd=other))
    assert out["OHMYJOB_WORKSPACE"] == str(other)
    out = report(run(plugin, "prep", "next", cwd=other, OHMYJOB_WORKSPACE=str(workspace)))
    assert out["OHMYJOB_WORKSPACE"] == str(workspace)


def test_a_cwd_that_is_not_inside_a_workspace_is_the_one_line_error(plugin, tmp_path):
    (tmp_path / "plain").mkdir()
    proc = run(plugin, "prep", "next", cwd=tmp_path / "plain")
    assert proc.returncode == 3 and proc.stdout.startswith("ohmyjob: no ohmyjob.json")


def test_claude_project_dir_is_not_a_start_folder(plugin, workspace, tmp_path):
    """The Bash tool never sets CLAUDE_PROJECT_DIR, so the walk starts from the cwd only; a stray value changes nothing."""
    (tmp_path / "plain").mkdir()
    for project in (str(workspace), "relative", "/", "//", "/nonexistent/deep/folder/"):
        proc = run(plugin, "prep", "next", cwd=tmp_path / "plain", CLAUDE_PROJECT_DIR=project)
        assert proc.returncode == 3 and proc.stdout.startswith("ohmyjob: no ohmyjob.json"), project
    other = tmp_path / "other"
    other.mkdir()
    (other / "ohmyjob.json").write_text("{}")
    out = report(run(plugin, "prep", "next", cwd=other, CLAUDE_PROJECT_DIR=str(workspace)))
    assert Path(out["OHMYJOB_WORKSPACE"]).resolve() == other.resolve()


@pytest.fixture
def project(tmp_path, workspace):
    """A project folder as setup leaves it: the workspace is its subfolder ohmyjob/."""
    folder = tmp_path / "project"
    folder.mkdir()
    workspace.rename(folder / "ohmyjob")
    (folder / "notes").mkdir()
    return folder


@pytest.mark.parametrize("start", [".", "notes", "ohmyjob", "ohmyjob/Applications",
                                   "ohmyjob/Applications/4399000001_Lumenfield-Logistics_Operations-Analyst"])
def test_the_workspace_is_the_ohmyjob_folder_of_the_project(plugin, project, start):
    """Claude starts in the project folder; a shell can also be inside ohmyjob/ or any folder under it."""
    out = report(run(plugin, "prep", "next", cwd=project / start))
    assert Path(out["OHMYJOB_WORKSPACE"]).resolve() == (project / "ohmyjob").resolve()


def test_a_project_folder_without_the_workspace_marker_is_no_workspace(plugin, tmp_path):
    project = tmp_path / "project"
    (project / "ohmyjob" / "Applications").mkdir(parents=True)  # an ohmyjob/ folder without ohmyjob.json
    for cwd in (project, project / "ohmyjob", project / "ohmyjob" / "Applications"):
        proc = run(plugin, "prep", "next", cwd=cwd)
        assert proc.returncode == 3 and proc.stdout.startswith("ohmyjob: no ohmyjob.json"), cwd
    other = tmp_path / "other"
    other.mkdir()
    (other / "ohmyjob").write_text("a file, not the folder")
    assert run(plugin, "prep", "next", cwd=other).returncode == 3


def test_a_folder_with_its_own_marker_wins_over_its_ohmyjob_subfolder(plugin, tmp_path):
    outer = tmp_path / "outer"
    (outer / "ohmyjob").mkdir(parents=True)
    (outer / "ohmyjob.json").write_text("{}")
    (outer / "ohmyjob" / "ohmyjob.json").write_text("{}")
    assert Path(report(run(plugin, "prep", "next", cwd=outer))["OHMYJOB_WORKSPACE"]).resolve() == outer.resolve()
    inner = outer / "ohmyjob"
    assert Path(report(run(plugin, "prep", "next", cwd=inner))["OHMYJOB_WORKSPACE"]).resolve() == inner.resolve()


def test_outside_a_workspace_it_is_fatal_with_the_hint(plugin, tmp_path):
    (tmp_path / "plain").mkdir()
    proc = run(plugin, "prep", "next", cwd=tmp_path / "plain")
    assert proc.returncode == 3
    assert proc.stdout.startswith("ohmyjob: no ohmyjob.json") and "/ohmyjob:setup" in proc.stdout


# --------------------------------------------------------------------------- env.sh


@pytest.fixture
def data_dir(tmp_path):
    """A data folder (with a space in its path) whose venv/bin/python runs the test interpreter."""
    python = tmp_path / "da ta" / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    python.chmod(0o755)
    return python.parents[2]


def _run_env_sh(plugin, workspace, text):
    (workspace / ".ohmyjob").mkdir(exist_ok=True)
    (workspace / ".ohmyjob" / "env.sh").write_text(text)
    return subprocess.run([str(plugin / "bin" / "ohmyjob"), "scrape"], cwd=workspace, env={"PATH": "/usr/bin:/bin"},
                          capture_output=True, text=True, timeout=60)


def _good(data_dir, extra=""):
    return f"OHMYJOB_DATA='{data_dir}'\nOHMYJOB_PY='{data_dir}/venv/bin/python'\n{extra}"


def test_env_sh_gives_the_python_and_its_variables_reach_the_script(plugin, workspace, data_dir):
    out = report(_run_env_sh(plugin, workspace, _good(data_dir, "OHMYJOB_UV=/bin/uv\nOHMYJOB_LUALATEX=/tex/lualatex\n")))
    assert (out["OHMYJOB_DATA"], out["OHMYJOB_UV"], out["OHMYJOB_LUALATEX"]) == (str(data_dir), "/bin/uv", "/tex/lualatex")


def test_env_sh_is_parsed_not_sourced(plugin, workspace, tmp_path, data_dir):
    """The agents can write env.sh and Bash(ohmyjob *) is pre-allowed: nothing in the file may run or expand."""
    marker = tmp_path / "ran"
    for bad in (f"touch {marker}\n", f"OHMYJOB_DATA=$(touch {marker})\n", f"OHMYJOB_DATA=`touch {marker}`\n",
                "OHMYJOB_DATA=$NEVER_SET/data\n", "OHMYJOB_DATA=a b\n", "OHMYJOB_DATA='it''s'\n", "false\n",
                "OTHER='x'\n", "OHMYJOB_DATA=\"x\"\n", f"OHMYJOB_DATA=x; touch {marker}\n"):
        proc = _run_env_sh(plugin, workspace, _good(data_dir, bad))
        assert proc.returncode == 3, bad
        assert proc.stdout.startswith("ohmyjob: ") and "env.sh" in proc.stdout and proc.stdout.count("\n") == 1, bad
        assert not marker.exists(), bad


@pytest.mark.parametrize("program", ["/usr/bin/find", "/bin/sh", "/usr/bin/env", "python", "/usr/bin/python3"])
def test_env_sh_cannot_name_another_program_as_the_interpreter(plugin, workspace, tmp_path, data_dir, program):
    """`ohmyjob prep -exec sh -c '...' \\;` would run a command if OHMYJOB_PY were /usr/bin/find."""
    marker = tmp_path / "ran"
    (workspace / ".ohmyjob").mkdir(exist_ok=True)
    (workspace / ".ohmyjob" / "env.sh").write_text(f"OHMYJOB_DATA='{data_dir}'\nOHMYJOB_PY='{program}'\n")
    proc = subprocess.run([str(plugin / "bin" / "ohmyjob"), "prep", "-exec", "touch", str(marker), ";"], cwd=workspace,
                          env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=60)
    assert (proc.returncode, proc.stdout) == (3, "ohmyjob: environment missing or stale, run /ohmyjob:setup\n")
    assert not marker.exists()


@pytest.mark.parametrize("extra", ["OHMYJOB_UV=/bin/sh\n", "OHMYJOB_UV=/usr/bin/find\n", "OHMYJOB_LUALATEX=/bin/sh\n",
                                   "OHMYJOB_LUALATEX=/usr/bin/xelatex\n"])
def test_env_sh_uv_and_lualatex_must_keep_their_names(plugin, workspace, data_dir, extra):
    proc = _run_env_sh(plugin, workspace, _good(data_dir, extra))
    assert proc.returncode == 3 and proc.stdout.startswith("ohmyjob: ") and "env.sh" in proc.stdout


def test_env_sh_takes_quoted_values_literally_and_allows_comments_and_spaces(plugin, workspace, data_dir):
    text = "# written by setup\n\n" + _good(data_dir, "OHMYJOB_UV='/Users/a b/.local/bin/uv'\nOHMYJOB_LUALATEX=''\n")
    out = report(_run_env_sh(plugin, workspace, text))
    assert (out["OHMYJOB_DATA"], out["OHMYJOB_UV"]) == (str(data_dir), "/Users/a b/.local/bin/uv")


STALE = "ohmyjob: environment missing or stale, run /ohmyjob:setup\n"


def run_clean(plugin, *args, cwd, **env_extra):
    """Like run(), but with no OHMYJOB_PY from the caller: only .ohmyjob/env.sh can say where Python is."""
    env = {"PATH": "/usr/bin:/bin", **env_extra}
    return subprocess.run([str(plugin / "bin" / "ohmyjob"), *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)


def test_without_env_sh_it_prints_the_one_line_and_exits_3(plugin, workspace):
    proc = run_clean(plugin, "prep", "next", cwd=workspace)
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, STALE, "")


@pytest.mark.parametrize("env_sh", [
    "OHMYJOB_PY=/nonexistent/venv/bin/python\n",  # the data folder moved, or the plugin was loaded another way
    "OHMYJOB_PY=\n",
    "OHMYJOB_DATA=/data\n",  # no Python in it at all
])
def test_a_stale_env_sh_prints_the_one_line_and_exits_3(plugin, workspace, env_sh):
    (workspace / ".ohmyjob").mkdir()
    (workspace / ".ohmyjob" / "env.sh").write_text(env_sh)
    proc = run_clean(plugin, "apply", "next", cwd=workspace)
    assert (proc.returncode, proc.stdout, proc.stderr) == (3, STALE, "")


def test_a_python_that_is_not_executable_is_stale_too(plugin, workspace, tmp_path):
    python = tmp_path / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o644)
    proc = run_clean(plugin, "scrape", cwd=workspace, OHMYJOB_PY=str(python))
    assert (proc.returncode, proc.stdout) == (3, STALE)


# --------------------------------------------------------------------------- dependency re-sync


@pytest.fixture
def synced(plugin, tmp_path, uv):
    """OHMYJOB_DATA with a venv folder, and the environment that points at the stub uv."""
    data = tmp_path / "data"
    (data / "venv").mkdir(parents=True)
    log = tmp_path / "uv.log"
    return {"OHMYJOB_DATA": str(data), "OHMYJOB_UV": str(uv), "OHMYJOB_UV_LOG": str(log)}, data, log


def stamp(data):
    return (data / "venv" / "uv.lock.sha256").read_text().strip()


@pytest.mark.parametrize("word", ["prep", "apply"])
def test_start_syncs_when_the_lock_changed_and_stamps_it(plugin, workspace, synced, word):
    env, data, log = synced
    proc = run(plugin, word, "start", cwd=workspace, **env)
    out = report(proc)  # stdout is only the script's JSON: uv's chatter went to stderr
    assert out["argv"] == ["start"]
    assert "chatter that must not reach stdout" in proc.stderr
    assert log.read_text().splitlines() == [f"{data}/venv|sync --frozen --no-dev --quiet --project {plugin}"]
    assert stamp(data) == hashlib.sha256(LOCK.encode()).hexdigest()


def test_a_matching_stamp_means_no_sync(plugin, workspace, synced):
    env, data, log = synced
    report(run(plugin, "prep", "start", cwd=workspace, **env))
    report(run(plugin, "prep", "start", cwd=workspace, **env))
    report(run(plugin, "apply", "start", "--continue", cwd=workspace, **env))
    assert len(log.read_text().splitlines()) == 1
    (plugin / "uv.lock").write_text(LOCK + "# a new dependency\n")  # a plugin update changed the lock
    report(run(plugin, "apply", "start", cwd=workspace, **env))
    assert len(log.read_text().splitlines()) == 2
    assert stamp(data) == hashlib.sha256((LOCK + "# a new dependency\n").encode()).hexdigest()


@pytest.mark.parametrize("args", [
    ("prep", "next"), ("prep", "end"), ("prep", "build", "4399000001"), ("apply", "next"), ("apply", "end"),
    ("apply", "assign", "--tabs", "1,2"), ("scrape", "start"), ("reconcile", "start"), ("resume", "start"), ("prep",),
])
def test_only_prep_start_and_apply_start_sync(plugin, workspace, synced, args):
    env, data, log = synced
    report(run(plugin, *args, cwd=workspace, **env))
    assert not log.exists()
    assert not (data / "venv" / "uv.lock.sha256").exists()


def test_a_failed_sync_is_fatal_and_leaves_no_stamp(plugin, workspace, synced):
    env, data, log = synced
    proc = run(plugin, "prep", "start", cwd=workspace, STUB_UV_FAIL="1", **env)
    assert proc.returncode == 3
    assert proc.stdout.startswith("ohmyjob: uv sync failed") and "/ohmyjob:setup" in proc.stdout
    assert not (data / "venv" / "uv.lock.sha256").exists()


def test_a_changed_lock_without_uv_is_fatal(plugin, workspace, synced):
    env, data, log = synced
    env = {k: v for k, v in env.items() if k != "OHMYJOB_UV"}
    proc = run(plugin, "apply", "start", cwd=workspace, **env)
    assert proc.returncode == 3
    assert proc.stdout.startswith("ohmyjob: uv.lock changed but uv was not found")


def test_without_a_data_folder_start_does_not_sync(plugin, workspace, uv, tmp_path):
    log = tmp_path / "uv.log"
    report(run(plugin, "prep", "start", cwd=workspace, OHMYJOB_UV=str(uv), OHMYJOB_UV_LOG=str(log)))
    assert not log.exists()
