"""scripts/bootstrap.sh in a temp workspace and a temp data dir.

The real `uv sync` and the real warm-up build with the system lualatex run once (module fixture `real`). Every
other test puts fakes on PATH: uv (makes a venv whose python is the test interpreter), claude (prints a version),
a lualatex that always fails, and a curl that serves fake installer scripts. The real installers never run.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "scripts" / "bootstrap.sh"
REAL_LUALATEX = shutil.which("lualatex")
SYSTEM_PATH = ":".join(dict.fromkeys(["/usr/bin", "/bin", *([str(Path(REAL_LUALATEX).parent)] if REAL_LUALATEX else [])]))


def _texmfhome() -> str:
    """The user TeX tree of the real HOME: the tests use a fake HOME, and the system TeX may keep packages there."""
    try:
        out = subprocess.run(["kpsewhich", "-var-value=TEXMFHOME"], capture_output=True, text=True, timeout=60)
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


TEXMFHOME = _texmfhome()

FAKE_UV = """#!/bin/sh
# fake uv: `uv sync` makes $UV_PROJECT_ENVIRONMENT/bin/python, a wrapper around the test interpreter
echo "uv $*" >> "$FAKE_LOG"
[ "$1" = sync ] || exit 0
mkdir -p "$UV_PROJECT_ENVIRONMENT/bin"
printf '#!/bin/sh\\nexec %s "$@"\\n' "$FAKE_PYTHON" > "$UV_PROJECT_ENVIRONMENT/bin/python"
chmod +x "$UV_PROJECT_ENVIRONMENT/bin/python"
"""
FAKE_CLAUDE = """#!/bin/sh
echo "${FAKE_CLAUDE_VERSION:-2.1.317} (Claude Code)"
"""
FAKE_CURL = """#!/bin/sh
# fake curl: serves the installer script for the URL it is given and logs the call
echo "curl $*" >> "$FAKE_LOG"
case "$*" in
  *astral.sh*) cat "$FAKE_UV_INSTALLER" ;;
  *yihui.org*) cat "$FAKE_TINYTEX_INSTALLER" ;;
  *) exit 22 ;;
esac
"""
FAKE_TLMGR = """#!/bin/sh
# fake tlmgr: `install` only works after `update --self`, like a fresh TinyTeX whose tlmgr is older than its repository
echo "tlmgr $*" >> "$FAKE_LOG"
if [ "$1" = install ] && ! grep -q '^tlmgr update --self$' "$FAKE_LOG"; then
  echo "tlmgr: please update tlmgr itself: tlmgr update --self" >&2
  exit 255
fi
"""
# a lualatex that works anywhere: writes a 1-page "PDF" and the log line the build reads
FAKE_LUALATEX = """#!/bin/sh
echo "lualatex $*" >> "$FAKE_LOG"
printf '%%PDF-1.5 fake\n' > resume.pdf
echo "Output written on resume.pdf (1 page, 14 bytes)."
"""


def write_exe(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class Env:
    """A temp workspace + data dir + fake tool dir, and a way to run bootstrap in them."""

    def __init__(self, tmp: Path, home: Path | None = None, data_name: str = "data dir"):
        self.tmp = tmp
        self.ws = tmp / "ws"
        self.data = tmp / data_name  # a space by default: only the TinyTeX installer cannot take one
        self.fake = tmp / "fakebin"
        self.home = home or tmp / "home"  # shared between tests: keeps the font cache warm
        self.log = tmp / "fake.log"
        self.home.mkdir(exist_ok=True)
        self.log.write_text("")
        self.env = {
            "PATH": f"{self.fake}:{SYSTEM_PATH}",
            "HOME": str(self.home),
            "FAKE_LOG": str(self.log),
            "FAKE_PYTHON": sys.executable,
            "LC_ALL": "C",
            **({"TEXMFHOME": TEXMFHOME} if TEXMFHOME else {}),
        }
        # an installed uv would hide a missing one: tests with the fake uv on PATH use `with_uv`
        write_exe(self.fake / "claude", FAKE_CLAUDE)
        write_exe(self.fake / "lualatex", FAKE_LUALATEX)  # never depend on the machine's TeX

    def with_uv(self) -> "Env":
        write_exe(self.fake / "uv", FAKE_UV)
        return self

    def failing_lualatex(self) -> "Env":
        write_exe(self.fake / "lualatex", "#!/bin/sh\nexit 1\n")
        return self

    def run(self, *args: str, data: bool = True, extra_env: dict | None = None) -> subprocess.CompletedProcess:
        cmd = ["bash", str(BOOTSTRAP)]
        if data:
            cmd += ["--data", str(self.data)]
        cmd += ["--workspace", str(self.ws), *args]
        env = {**self.env, **(extra_env or {})}
        return subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=self.tmp, timeout=600)

    def calls(self, prefix: str) -> list[str]:
        return [line for line in self.log.read_text().splitlines() if line.startswith(prefix)]

    def snapshot(self, skip: tuple = ()) -> dict:
        out = {}
        for base in (self.ws, self.data):
            for path in sorted(base.rglob("*")):
                rel = str(path)
                if path.is_file() and not rel.endswith(skip):
                    out[rel] = (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        return out


@pytest.fixture(scope="module")
def shared_home(tmp_path_factory):
    return tmp_path_factory.mktemp("home")


@pytest.fixture
def env(tmp_path, shared_home):
    return Env(tmp_path, shared_home).with_uv()


@pytest.fixture
def env_plain(tmp_path, shared_home):
    """Like `env`, with a data folder without a space (the TinyTeX installer needs that)."""
    return Env(tmp_path, shared_home, data_name="datadir").with_uv()


def installer_scripts(env: Env) -> None:
    """Fake uv and TinyTeX installers, served by the fake curl."""
    uv_installer = env.tmp / "uv_installer.sh"
    uv_installer.write_text(
        '[ -n "$UV_UNMANAGED_INSTALL" ] || exit 9\n'  # the receipt / PATH-editing install is never used
        'mkdir -p "$UV_UNMANAGED_INSTALL"\n'
        f"cp '{env.tmp / 'uv_source'}' \"$UV_UNMANAGED_INSTALL/uv\"\n"
        'chmod +x "$UV_UNMANAGED_INSTALL/uv"\n'
    )
    write_exe(env.tmp / "uv_source", FAKE_UV)
    tex_bin = '"$TINYTEX_DIR/.TinyTeX/bin/x86_64-linux"'
    tinytex_installer = env.tmp / "tinytex_installer.sh"
    tinytex_installer.write_text(
        '[ "$1" = "" ] && [ "$2" = "--no-path" ] || exit 9\n'  # no symlinks into ~/bin, /usr/local/bin, /etc/paths.d
        f"mkdir -p {tex_bin}\n"
        f"cp '{env.tmp / 'lua_source'}' {tex_bin}/lualatex\n"
        f"cp '{env.tmp / 'tlmgr_source'}' {tex_bin}/tlmgr\n"
        f"chmod +x {tex_bin}/lualatex {tex_bin}/tlmgr\n"
    )
    write_exe(env.tmp / "tlmgr_source", FAKE_TLMGR)
    write_exe(env.tmp / "lua_source", FAKE_LUALATEX)
    write_exe(env.fake / "curl", FAKE_CURL)
    env.env["FAKE_UV_INSTALLER"] = str(uv_installer)
    env.env["FAKE_TINYTEX_INSTALLER"] = str(tinytex_installer)


def ok(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.rstrip().endswith("bootstrap: OK")


def error_line(result: subprocess.CompletedProcess) -> str:
    assert result.returncode == 3, result.stdout + result.stderr
    lines = [line for line in result.stdout.splitlines() if line.startswith("ohmyjob: ")]
    assert len(lines) == 1, result.stdout
    return lines[0]


# --------------------------------------------------------------------------- the real run (real uv, real lualatex)

@pytest.fixture(scope="module")
def real(tmp_path_factory):
    if not REAL_LUALATEX or not shutil.which("uv"):
        pytest.skip("needs a real lualatex and uv on PATH")
    tmp = tmp_path_factory.mktemp("real")
    e = Env(tmp)
    e.env = {**os.environ, "FAKE_LOG": str(e.log)}  # real PATH: real uv, real claude, real lualatex
    result = e.run()
    ok(result)  # the output of bootstrap is in the failure message of every test that uses `real`
    return e, result


def test_real_run_builds_everything(real):
    e, result = real
    ok(result)
    lock = (ROOT / "uv.lock").read_bytes()
    assert (e.data / "venv" / "uv.lock.sha256").read_text().strip() == hashlib.sha256(lock).hexdigest()
    assert (e.data / "venv" / "bin" / "python").exists()
    assert "lualatex: " in result.stdout and "system lualatex works" in result.stdout
    assert not (e.data / "tinytex").exists()
    assert (e.data / "chrome-extension" / "manifest.json").exists()
    for name in ("Applications", "Pending-Review", "Needs-Attention", "Submitted", "Default_Resume_Files", ".ohmyjob"):
        assert (e.ws / name).is_dir()
    assert json.loads((e.ws / "ohmyjob.json").read_text()) == json.loads((ROOT / "templates" / "ohmyjob.json").read_text())
    assert (e.ws / "CLAUDE.md").read_text() == (ROOT / "templates" / "CLAUDE.md").read_text()
    assert (e.ws / ".gitignore").read_text() == (ROOT / "templates" / "gitignore").read_text()


def test_real_run_tracker_and_dispatcher(real):
    e, _ = real
    import openpyxl

    sheet = openpyxl.load_workbook(e.ws / "Job_Tracker.xlsx")["Jobs"]
    assert [c.value for c in sheet[1]][:3] == ["Job URL", "Status", "Company"]
    assert sheet.max_row == 1
    # bin/ohmyjob accepts the env.sh that bootstrap wrote
    out = subprocess.run([str(ROOT / "bin" / "ohmyjob"), "resume", "--help"], capture_output=True, text=True,
                         cwd=e.ws, env={**os.environ, "OHMYJOB_WORKSPACE": str(e.ws)})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "ohmyjob:" not in out.stdout.split("usage")[0]


def test_real_run_env_sh_format(real):
    e, _ = real
    lines = (e.ws / ".ohmyjob" / "env.sh").read_text().splitlines()
    assignments = dict(re.fullmatch(r"(OHMYJOB_[A-Z]+)='([^']*)'", line).groups() for line in lines if not line.startswith("#"))
    assert set(assignments) == {"OHMYJOB_PY", "OHMYJOB_UV", "OHMYJOB_DATA", "OHMYJOB_LUALATEX"}
    assert assignments["OHMYJOB_DATA"] == str(e.data)
    assert assignments["OHMYJOB_PY"] == f"{e.data}/venv/bin/python"
    assert os.path.basename(assignments["OHMYJOB_UV"]) == "uv"
    assert os.path.basename(assignments["OHMYJOB_LUALATEX"]) == "lualatex"


def test_real_run_is_idempotent(real):
    e, _ = real
    before = e.snapshot(skip=("env.sh",))
    env_before = (e.ws / ".ohmyjob" / "env.sh").read_text()
    again = e.run()
    ok(again)
    assert e.snapshot(skip=("env.sh",)) == before
    assert (e.ws / ".ohmyjob" / "env.sh").read_text() == env_before
    assert "venv is current" in again.stdout and "extension is current" in again.stdout
    assert ".claude/settings.json is current" in again.stdout


# --------------------------------------------------------------------------- fakes: consent flags

def test_uv_missing_needs_consent(tmp_path, shared_home):
    e = Env(tmp_path, shared_home)  # no uv anywhere
    result = e.run()
    assert "--install-uv" in error_line(result)
    assert not e.ws.joinpath("ohmyjob.json").exists()
    assert not e.data.joinpath("venv").exists()


def test_uv_install_with_flag(tmp_path, shared_home):
    e = Env(tmp_path, shared_home)
    installer_scripts(e)
    result = e.run("--install-uv")
    ok(result)
    assert (e.data / "bin" / "uv").exists()
    assert len(e.calls("curl")) == 1 and "astral.sh" in e.calls("curl")[0]
    env_sh = (e.ws / ".ohmyjob" / "env.sh").read_text()
    assert f"OHMYJOB_UV='{e.data}/bin/uv'" in env_sh
    # second run finds the installed uv in the data dir: no download
    ok(e.run("--install-uv"))
    assert len(e.calls("curl")) == 1


def test_uv_sync_arguments(env):
    ok(env.run())
    syncs = env.calls("uv sync")
    assert syncs == [f"uv sync --frozen --no-dev --quiet --project {ROOT}"]


def test_quote_in_a_tool_path_is_refused_before_env_sh(tmp_path, shared_home):
    e = Env(tmp_path, shared_home)
    quirky = tmp_path / "it's"
    quirky.mkdir()
    write_exe(quirky / "uv", FAKE_UV)
    e.env["PATH"] = f"{quirky}:{e.env['PATH']}"
    assert "env.sh" in error_line(e.run())
    assert not (e.ws / ".ohmyjob" / "env.sh").exists()


def test_existing_uv_is_never_downloaded(env):
    installer_scripts(env)
    ok(env.run("--install-uv", "--install-tinytex"))
    assert env.calls("curl") == []


def test_tinytex_needs_consent_when_system_latex_fails(env):
    env.failing_lualatex()
    result = env.run()
    assert "--install-tinytex" in error_line(result)
    assert not (env.data / "tinytex").exists()


def test_tinytex_install_with_flag(env_plain):
    env = env_plain
    installer_scripts(env)
    env.failing_lualatex()
    write_exe(env.tmp / "systex" / "tlmgr", FAKE_TLMGR.replace('echo "tlmgr', 'echo "SYSTEM tlmgr'))
    env.env["PATH"] = f"{env.tmp / 'systex'}:{env.env['PATH']}"
    result = env.run("--install-tinytex")
    ok(result)
    tlmgr = env.calls("tlmgr")
    assert len(tlmgr) == 2
    listed = [l.strip() for l in (ROOT / "scripts" / "tex-packages.txt").read_text().splitlines() if l.strip() and not l.startswith("#")]
    assert tlmgr[0] == "tlmgr update --self"  # first: a stale tlmgr cannot install
    tlmgr = tlmgr[1:]
    assert tlmgr[0] == "tlmgr install " + " ".join(listed)
    for pkg in ("fancyhdr", "pgf", "tikzfill", "pdfcol", "environ", "trimspaces", "fontawesome6"):
        assert pkg in listed
    assert not env.calls("SYSTEM")  # the tlmgr on PATH (a system tree) is never used
    lua = f"{env.data}/tinytex/.TinyTeX/bin/x86_64-linux/lualatex"
    assert f"OHMYJOB_LUALATEX='{lua}'" in (env.ws / ".ohmyjob" / "env.sh").read_text()
    # second run: TinyTeX is already there and works; nothing is downloaded or installed again
    ok(env.run("--install-tinytex"))
    assert len(env.calls("curl")) == 1 and len(env.calls("tlmgr")) == 2


def test_tinytex_refuses_a_data_path_with_space_or_quote(tmp_path, shared_home):
    for name in ("data dir", "it's"):
        sub = tmp_path / name.replace(" ", "_").replace("'", "q")
        sub.mkdir()
        e = Env(sub, shared_home, data_name=name).with_uv()
        installer_scripts(e)
        e.failing_lualatex()
        result = e.run("--install-tinytex")
        assert "TinyTeX" in error_line(result) or "single quote" in error_line(result)
        assert e.calls("curl") == [] and not (e.data / "tinytex").exists()


def test_non_latex_tests_use_the_fake_lualatex(env):
    """The fake sits first on PATH, so no test depends on the machine's TeX (a runner without lualatex passes too)."""
    ok(env.run())
    assert f"OHMYJOB_LUALATEX='{env.fake}/lualatex'" in (env.ws / ".ohmyjob" / "env.sh").read_text()
    assert env.calls("lualatex")


def test_working_system_latex_means_no_tinytex(env):
    installer_scripts(env)
    ok(env.run("--install-tinytex"))
    assert env.calls("curl") == [] and not (env.data / "tinytex").exists()


# --------------------------------------------------------------------------- version gate and arguments

@pytest.mark.parametrize("version,passes", [("2.1.305", False), ("2.0.999", False), ("2.1.306", True), ("2.2.0", True), ("10.0.1", True)])
def test_version_gate(env, version, passes):
    result = env.run(extra_env={"FAKE_CLAUDE_VERSION": version})
    if passes:
        ok(result)
    else:
        assert "too old" in error_line(result) and version in result.stdout
        assert not env.ws.joinpath("ohmyjob.json").exists()


def test_missing_claude_only_warns(env):
    (env.fake / "claude").unlink()
    result = env.run()
    ok(result)
    assert "warning" in result.stdout


def test_arguments(env):
    assert "--data" in error_line(env.run(data=False))
    assert "absolute" in error_line(subprocess.run(
        ["bash", str(BOOTSTRAP), "--data", "rel", "--workspace", str(env.ws)], capture_output=True, text=True, env=env.env))
    assert "unknown option" in error_line(env.run("--nope"))


# --------------------------------------------------------------------------- settings merge

def read_settings(env: Env) -> dict:
    return json.loads((env.ws / ".claude" / "settings.json").read_text())


def test_settings_created_from_template(env):
    ok(env.run())
    settings = read_settings(env)
    template = json.loads((ROOT / "templates" / "workspace-settings.json").read_text())
    assert settings == template
    assert settings["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "20"
    assert "Agent(" not in json.dumps(settings)


def test_settings_merge_keeps_user_keys(env):
    (env.ws / ".claude").mkdir(parents=True)
    mine = {
        "model": "opus",
        "autoMemoryEnabled": True,
        "env": {"MY_VAR": "1", "MAX_MCP_OUTPUT_TOKENS": "90000", "CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS": "5"},
        "enabledPlugins": {"other@market": True, "ohmyjob@ohmyjob": False},
        "permissions": {"allow": ["Bash(git status)"], "deny": ["Read(./secrets/**)"], "defaultMode": "plan"},
        "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]},
    }
    (env.ws / ".claude" / "settings.json").write_text(json.dumps(mine))
    ok(env.run())
    merged = read_settings(env)
    assert merged["model"] == "opus"
    assert merged["autoMemoryEnabled"] is True  # the user's value beats the template's
    assert merged["hooks"] == mine["hooks"]
    assert merged["env"]["MY_VAR"] == "1"
    assert merged["env"]["MAX_MCP_OUTPUT_TOKENS"] == "90000"  # higher than the 60000 the pipeline needs: kept
    assert merged["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "20"  # too low: raised to the floor
    assert merged["enabledPlugins"] == {"other@market": True, "ohmyjob@ohmyjob": False}
    assert merged["permissions"]["allow"] == ["Bash(git status)", "Bash(ohmyjob *)"]
    assert merged["permissions"]["deny"] == ["Read(./secrets/**)", "mcp__claude-in-chrome__javascript_tool"]
    assert merged["permissions"]["defaultMode"] == "plan"
    assert "ohmyjob" in merged["extraKnownMarketplaces"]
    # a second run changes nothing
    before = (env.ws / ".claude" / "settings.json").read_bytes()
    ok(env.run())
    assert (env.ws / ".claude" / "settings.json").read_bytes() == before


def test_settings_left_alone_when_nothing_to_merge(env):
    ok(env.run())
    path = env.ws / ".claude" / "settings.json"
    data = read_settings(env)
    path.write_text(json.dumps(data))  # same content, other formatting
    before = path.read_bytes()
    ok(env.run())
    assert path.read_bytes() == before


def test_invalid_settings_json_is_not_overwritten(env):
    (env.ws / ".claude").mkdir(parents=True)
    path = env.ws / ".claude" / "settings.json"
    path.write_text("{ not json")
    result = env.run()
    assert "settings.json" in error_line(result)
    assert path.read_text() == "{ not json"


@pytest.mark.parametrize("workers,expected", [(5, "20"), (19, "20"), (20, "21"), (30, "31")])
def test_subagent_cap(env, workers, expected):
    env.ws.mkdir(parents=True)
    config = json.loads((ROOT / "templates" / "ohmyjob.json").read_text())
    config["pipeline"]["max_parallel_workers"] = workers
    (env.ws / "ohmyjob.json").write_text(json.dumps(config))
    ok(env.run())
    assert read_settings(env)["env"]["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == expected


# --------------------------------------------------------------------------- never overwrite

def test_existing_files_are_never_overwritten(env):
    env.ws.mkdir(parents=True)
    own = {
        "CLAUDE.md": "my own guide\n",
        ".gitignore": "mine\n",
        "ohmyjob.json": json.dumps({"search": {"sources": ["linkedin"], "search_query": ["Cook"]}}),
    }
    for name, text in own.items():
        (env.ws / name).write_text(text)
    (env.ws / "Applications").mkdir()
    (env.ws / "Applications" / "keep.txt").write_text("x")
    (env.ws / "Profile.md").write_text("# Fit Preferences\n")
    ok(env.run())
    # a tracker that exists is kept byte for byte (a first run created it; a second must not touch it)
    tracker = env.ws / "Job_Tracker.xlsx"
    tracker.write_bytes(b"not really a workbook")
    ok(env.run())
    for name, text in own.items():
        assert (env.ws / name).read_text() == text
    assert tracker.read_bytes() == b"not really a workbook"
    assert (env.ws / "Applications" / "keep.txt").read_text() == "x"
    assert (env.ws / "Profile.md").read_text() == "# Fit Preferences\n"


def test_env_sh_is_rewritten_every_run(env):
    ok(env.run())
    path = env.ws / ".ohmyjob" / "env.sh"
    path.write_text("OHMYJOB_PY='/usr/bin/find'\n")
    ok(env.run())
    text = path.read_text()
    assert "/usr/bin/find" not in text
    assert f"OHMYJOB_PY='{env.data}/venv/bin/python'" in text
    assert "OHMYJOB_LUALATEX='" in text


def test_workspace_cannot_be_the_plugin_folder(env):
    result = subprocess.run(["bash", str(BOOTSTRAP), "--data", str(env.data), "--workspace", str(ROOT)],
                            capture_output=True, text=True, env=env.env)
    assert "plugin folder" in error_line(result)
