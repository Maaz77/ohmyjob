"""workspace.py: root discovery, path constants, config validation, agent names and guard command patterns."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import workspace as W

SCRIPTS = Path(W.__file__).parent
TEMPLATES = SCRIPTS.parent / "templates"


def run_python(code, workspace=None, cwd=None):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(cwd or "/"), "PYTHONPATH": str(SCRIPTS)}
    if workspace is not None:
        env["OHMYJOB_WORKSPACE"] = str(workspace)
    proc = subprocess.run([sys.executable, "-c", code], cwd=cwd or SCRIPTS, env=env, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def write_config(root, raw):
    (root / "ohmyjob.json").write_text(json.dumps(raw), encoding="utf-8")
    return W.load_config(root)


# --------------------------------------------------------------------------- find_root and the constants


def test_find_root_prefers_the_testing_variable_even_without_a_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("OHMYJOB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/elsewhere")
    assert W.find_root() == tmp_path.resolve()


def test_find_root_uses_the_claude_project_dir_when_it_holds_the_marker(tmp_path, monkeypatch):
    monkeypatch.delenv("OHMYJOB_WORKSPACE", raising=False)
    (tmp_path / "ohmyjob.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.chdir("/")
    assert W.find_root() == tmp_path.resolve()


def test_find_root_walks_up_from_a_claude_project_dir_inside_the_workspace(workspace, tmp_path, monkeypatch):
    monkeypatch.delenv("OHMYJOB_WORKSPACE")
    inner = workspace / "Applications" / "4399000001_Lumenfield-Logistics_Operations-Analyst"
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(inner))
    monkeypatch.chdir(tmp_path)  # the hook process can run anywhere
    assert W.find_root() == workspace.resolve()


def test_find_root_skips_a_claude_project_dir_without_the_marker_and_walks_up(tmp_path, monkeypatch):
    monkeypatch.delenv("OHMYJOB_WORKSPACE", raising=False)
    plain = tmp_path / "plain-project"
    plain.mkdir()
    workspace = tmp_path / "ws"
    (workspace / "Applications" / "deep").mkdir(parents=True)
    (workspace / "ohmyjob.json").write_text("{}")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(plain))
    monkeypatch.chdir(workspace / "Applications" / "deep")
    assert W.find_root() == workspace.resolve()


def test_find_root_fails_with_a_hint_outside_a_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("OHMYJOB_WORKSPACE", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(W.WorkspaceError, match="/ohmyjob:setup"):
        W.find_root()


def test_the_path_constants_follow_the_workspace(workspace):
    out = run_python(
        "import workspace as W, json\n"
        "names = 'ROOT CONFIG_FILE PROFILE TRACKER DEFAULT_RESUMES BASE_YAML APPLICATIONS PENDING_REVIEW "
        "NEEDS_ATTENTION SUBMITTED CLAUDE_MD SETTINGS_FILE STATE_DIR ENV_FILE RUN_PREP RUN_APPLY BUILD_DIR "
        "QUESTIONS_FILE REPORT_FILE PLUGIN_ROOT ATS_DIR'.split()\n"
        "print(json.dumps({n: str(getattr(W, n)) for n in names}))",
        workspace=workspace,
    )
    p = {name: Path(value) for name, value in json.loads(out).items()}
    root = workspace.resolve()
    assert p["ROOT"] == root
    assert {
        "CONFIG_FILE": root / "ohmyjob.json", "PROFILE": root / "Profile.md", "TRACKER": root / "Job_Tracker.xlsx",
        "DEFAULT_RESUMES": root / "Default_Resume_Files", "BASE_YAML": root / "Default_Resume_Files" / "base.yaml",
        "APPLICATIONS": root / "Applications", "PENDING_REVIEW": root / "Pending-Review",
        "NEEDS_ATTENTION": root / "Needs-Attention", "SUBMITTED": root / "Submitted", "CLAUDE_MD": root / "CLAUDE.md",
        "SETTINGS_FILE": root / ".claude" / "settings.json", "STATE_DIR": root / ".ohmyjob",
        "ENV_FILE": root / ".ohmyjob" / "env.sh", "RUN_PREP": root / ".ohmyjob" / "run" / "prep",
        "RUN_APPLY": root / ".ohmyjob" / "run" / "apply", "BUILD_DIR": root / ".ohmyjob" / "build",
        "QUESTIONS_FILE": root / ".ohmyjob" / "questions.json", "REPORT_FILE": root / ".ohmyjob" / "last_report.txt",
    } == {name: p[name] for name in (
        "CONFIG_FILE", "PROFILE", "TRACKER", "DEFAULT_RESUMES", "BASE_YAML", "APPLICATIONS", "PENDING_REVIEW",
        "NEEDS_ATTENTION", "SUBMITTED", "CLAUDE_MD", "SETTINGS_FILE", "STATE_DIR", "ENV_FILE", "RUN_PREP",
        "RUN_APPLY", "BUILD_DIR", "QUESTIONS_FILE", "REPORT_FILE")}
    assert p["PLUGIN_ROOT"] == SCRIPTS.parent
    assert p["ATS_DIR"] == SCRIPTS.parent / "ats"


def test_importing_outside_a_workspace_works_and_points_into_the_current_folder(tmp_path):
    out = run_python("import workspace as W; print(W.ROOT); print(W.TRACKER)", cwd=tmp_path)
    assert out.splitlines() == [str(tmp_path.resolve()), str(tmp_path.resolve() / "Job_Tracker.xlsx")]


def test_importing_workspace_loads_no_heavy_dependency():
    out = run_python(
        "import sys, workspace\n"
        "heavy = {'yaml', 'openpyxl', 'jobspy', 'pandas', 'numpy', 'rapidfuzz', 'simplemma', 'jinja2'}\n"
        "print(sorted(heavy & set(sys.modules)))"
    )
    assert out == "[]"


# --------------------------------------------------------------------------- config


def test_the_template_is_the_defaults():
    assert json.loads((TEMPLATES / "ohmyjob.json").read_text(encoding="utf-8")) == W.DEFAULTS


def test_every_setting_has_a_check():
    assert set(W.CHECKS) == {f"{s}.{k}" for s, keys in W.DEFAULTS.items() for k in keys}


def test_every_default_passes_its_own_check():
    assert W.validate_config(W.DEFAULTS) == W.DEFAULTS


def test_the_fixture_config_loads(workspace):
    config = W.load_config()
    assert config["search"]["search_query"] == ["Operations Analyst", "Project Coordinator"]
    assert config["search"]["country"] == "germany"
    assert config["search"]["languages"] == ["en", "de"]
    assert config["pipeline"] == {"max_parallel_workers": 3, "resume_max_pages": 1, "resume_paper": "a4"}


def test_missing_keys_take_their_defaults_and_defaults_are_not_shared(tmp_path):
    config = write_config(tmp_path, {"search": {"location": " Berlin "}})
    assert config["search"]["location"] == "Berlin"
    assert config["search"]["sources"] == ["linkedin", "indeed"]
    assert config["pipeline"] == W.DEFAULTS["pipeline"]
    config["search"]["sources"].append("google")
    assert W.DEFAULTS["search"]["sources"] == ["linkedin", "indeed"]


def test_an_empty_object_and_ignored_schema_keys_are_fine(tmp_path):
    assert write_config(tmp_path, {}) == W.DEFAULTS
    assert write_config(tmp_path, {"$schema": "x", "_note": "y"}) == W.DEFAULTS


def test_a_missing_or_broken_file_is_a_config_error(tmp_path):
    with pytest.raises(W.ConfigError, match="not found. Run /ohmyjob:setup"):
        W.load_config(tmp_path)
    (tmp_path / "ohmyjob.json").write_text("{not json")
    with pytest.raises(W.ConfigError, match="not valid JSON"):
        W.load_config(tmp_path)
    (tmp_path / "ohmyjob.json").write_text("[]")
    with pytest.raises(W.ConfigError, match="JSON object"):
        W.load_config(tmp_path)


def test_a_bare_string_search_query_is_refused_not_split_into_letters(tmp_path):
    with pytest.raises(W.ConfigError, match=r'"search.search_query" must be a list of strings'):
        write_config(tmp_path, {"search": {"search_query": "Registered Nurse"}})
    with pytest.raises(W.ConfigError, match="empty string"):
        write_config(tmp_path, {"search": {"search_query": ["Nurse", "  "]}})


def test_sources_are_normalised_and_an_unknown_board_lists_the_valid_ones(tmp_path):
    config = write_config(tmp_path, {"search": {"sources": ["Indeed", " linkedin", "indeed", "FREEHIRE"]}})
    assert config["search"]["sources"] == ["indeed", "linkedin", "freehire"]
    with pytest.raises(W.ConfigError) as exc:
        write_config(tmp_path, {"search": {"sources": ["linkedin", "monster"]}})
    message = str(exc.value)
    assert '"search.sources" has "monster"' in message
    assert all(board in message for board in W.SOURCES)
    with pytest.raises(W.ConfigError, match="at least one"):
        write_config(tmp_path, {"search": {"sources": []}})


def test_country_is_normalised_and_an_unknown_one_lists_the_valid_ones(tmp_path):
    assert write_config(tmp_path, {"search": {"country": " Germany "}})["search"]["country"] == "germany"
    assert write_config(tmp_path, {"search": {"country": "United States"}})["search"]["country"] == "usa"
    assert write_config(tmp_path, {"search": {"country": "Czechia"}})["search"]["country"] == "czech republic"
    assert write_config(tmp_path, {"search": {"country": ""}})["search"]["country"] == ""
    with pytest.raises(W.ConfigError) as exc:
        write_config(tmp_path, {"search": {"country": "Atlantis"}})
    assert '"search.country" has "Atlantis"' in str(exc.value)
    assert "germany" in str(exc.value) and "türkiye" in str(exc.value)


def test_employment_types_are_checked_and_may_be_empty(tmp_path):
    assert write_config(tmp_path, {"search": {"employment_types": ["FullTime"]}})["search"]["employment_types"] == ["fulltime"]
    assert write_config(tmp_path, {"search": {"employment_types": []}})["search"]["employment_types"] == []
    with pytest.raises(W.ConfigError, match="full-time"):
        write_config(tmp_path, {"search": {"employment_types": ["full-time"]}})


@pytest.mark.parametrize("key, bad", [
    ("recency_days", -1), ("recency_days", 3651), ("recency_days", True), ("recency_days", 1.5), ("recency_days", "1"),
    ("results_per_term", 0), ("results_per_term", None),
    ("uk_sponsor_only", 1), ("git_snapshot", "no"),
    ("location", 5), ("country", 5),
    ("title_filter", "loose"), ("title_filter", None),
    ("languages", []), ("languages", ["english"]), ("languages", "en"),
    ("title_aliases", ["ops"]), ("title_aliases", {"ops": 3}), ("title_aliases", {"ops": []}), ("title_only_aliases", {"": "x"}),
    ("exclude_companies", "Example Staffing"), ("filter_title_terms", [1]),
])
def test_bad_search_values_are_refused_with_their_key(tmp_path, key, bad):
    with pytest.raises(W.ConfigError, match=f'"search.{key}"'):
        write_config(tmp_path, {"search": {key: bad}})


@pytest.mark.parametrize("key, bad", [
    ("max_parallel_workers", 0), ("max_parallel_workers", "5"), ("resume_max_pages", -1), ("resume_max_pages", False),
    ("resume_paper", "a5"), ("resume_paper", 4),
])
def test_bad_pipeline_values_are_refused_with_their_key(tmp_path, key, bad):
    with pytest.raises(W.ConfigError, match=f'"pipeline.{key}"'):
        write_config(tmp_path, {"pipeline": {key: bad}})


def test_good_edge_values_are_kept(tmp_path):
    config = write_config(tmp_path, {
        "search": {"recency_days": 0, "title_filter": "OFF", "languages": ["DE", "fr"],
                   "title_aliases": {"rn": ["registered nurse", "nurse"]}},
        "pipeline": {"resume_max_pages": 0, "resume_paper": "Letter"},
    })
    assert config["search"]["recency_days"] == 0
    assert config["search"]["title_filter"] == "off"
    assert config["search"]["languages"] == ["de", "fr"]
    assert config["search"]["title_aliases"] == {"rn": ["registered nurse", "nurse"]}
    assert config["pipeline"] == {"max_parallel_workers": 5, "resume_max_pages": 0, "resume_paper": "letter"}


def test_unknown_sections_and_settings_are_refused_with_the_known_ones(tmp_path):
    with pytest.raises(W.ConfigError, match='"serach" is not a known section'):
        write_config(tmp_path, {"serach": {}})
    with pytest.raises(W.ConfigError, match=r'"search.seach_query" is not a known setting.*search_query'):
        write_config(tmp_path, {"search": {"seach_query": ["x"]}})
    with pytest.raises(W.ConfigError, match='"pipeline" must be an object'):
        write_config(tmp_path, {"pipeline": None})


def test_the_valid_values_match_the_pinned_jobspy():
    from jobspy.model import Country, JobType, Site, country_aliases

    assert sorted(W.JOBSPY_SOURCES) == sorted(s.value for s in Site)
    assert sorted(W.COUNTRIES) == sorted(c.value for c in Country)
    assert W.COUNTRY_ALIASES == country_aliases
    assert sorted(W.EMPLOYMENT_TYPES) == sorted(j.value for j in JobType)


# --------------------------------------------------------------------------- slots


def test_slots_is_the_worker_limit_below_the_subagent_cap_by_one(workspace, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", raising=False)
    assert W.slots() == 3  # the fixture asks for 3 workers
    config = W.validate_config({"pipeline": {"max_parallel_workers": 50}})
    assert W.slots(config) == 19  # default cap 20
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "60")
    assert W.slots(config) == 50
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "1")
    assert W.slots(config) == 1  # never below one
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "many")
    assert W.slots(config) == 19


# --------------------------------------------------------------------------- file_prefix


def test_file_prefix_is_the_first_name_of_base_yaml(workspace):
    assert W.file_prefix() == "Alex"
    assert W.file_prefix(workspace) == "Alex"


@pytest.mark.parametrize("first, prefix", [
    ("José", "Jose"), ("Mary Ann", "Mary-Ann"), ("O'Neil", "O-Neil"), ("Zoë_Marie", "Zoe-Marie"),
    ("李", "Resume"), ("", "Resume"), (None, "Resume"),
])
def test_file_prefix_is_an_ascii_slug(workspace, first, prefix):
    (workspace / "Default_Resume_Files" / "base.yaml").write_text(
        json.dumps({"name": {"first": first, "last": "X"}}), encoding="utf-8")
    assert W.file_prefix() == prefix


@pytest.mark.parametrize("content", [None, "", "just text", "name: Alex", "name: [a, b]", "name: {last: X}", "name: {first: ["])
def test_file_prefix_falls_back_to_resume(workspace, content):
    base = workspace / "Default_Resume_Files" / "base.yaml"
    if content is None:
        base.unlink()
    else:
        base.write_text(content, encoding="utf-8")
    assert W.file_prefix() == "Resume"


# --------------------------------------------------------------------------- names and guard patterns


def test_agent_names_are_scoped_to_the_plugin():
    assert W.PREP_AGENTS == {"ohmyjob:prep-master", "ohmyjob:screen-worker", "ohmyjob:resume-worker"}
    assert W.PREP_WORKERS == {W.SCREEN_WORKER, W.RESUME_WORKER}
    assert W.APPLICATION_AGENTS == {"ohmyjob:application-master", "ohmyjob:application-worker"}


def test_each_master_may_start_only_its_own_workers():
    assert W.MASTER_WORKERS == {
        "ohmyjob:prep-master": {"ohmyjob:screen-worker", "ohmyjob:resume-worker"},
        "ohmyjob:application-master": {"ohmyjob:application-worker"},
    }
    assert set(W.MASTER_WORKERS) | set().union(*W.MASTER_WORKERS.values()) == W.PREP_AGENTS | W.APPLICATION_AGENTS


def test_questions_blocker_text():
    assert W.QUESTIONS_BLOCKER == "questions for the candidate"


@pytest.mark.parametrize("command", [
    "ohmyjob prep start", "ohmyjob prep next", "ohmyjob prep record", "ohmyjob prep end",
    "ohmyjob prep assign-screen", "ohmyjob prep assign-build --max 5",
])
def test_prep_master_may_run(command):
    assert W.PREP_MASTER_CMD_RE.match(command)


@pytest.mark.parametrize("command", [
    "ohmyjob prep build 4399000001", "ohmyjob prep start now", "ohmyjob prep next; rm -rf x", "ohmyjob prep next && ls",
    "ohmyjob prep next | cat", "ohmyjob prep next\nls", "ohmyjob prep assign-build --max x", "ohmyjob apply start",
    "bash ohmyjob prep next", "ohmyjob  prep next", "ohmyjob prep", "ohmyjob prep next $(id)",
])
def test_prep_master_may_not_run(command):
    assert not W.PREP_MASTER_CMD_RE.match(command)


def test_resume_worker_may_run_only_a_build_with_a_job_key():
    assert W.RESUME_BUILD_CMD_RE.match("ohmyjob prep build 4399000001").group(1) == "4399000001"
    assert W.RESUME_BUILD_CMD_RE.match("ohmyjob prep build 969845171025937").group(1) == "969845171025937"
    for command in ["ohmyjob prep build", "ohmyjob prep build abc", "ohmyjob prep build 12 34", "ohmyjob prep build 12;ls",
                    "ohmyjob prep build ١٢", "ohmyjob prep build 12\nls", "ohmyjob prep next"]:
        assert not W.RESUME_BUILD_CMD_RE.match(command), command


@pytest.mark.parametrize("command", [
    "ohmyjob apply start", "ohmyjob apply start --continue", "ohmyjob apply preflight --tab 12",
    "ohmyjob apply preflight --tab 12 --home-only", "ohmyjob apply assign", "ohmyjob apply assign --tabs 11",
    "ohmyjob apply assign --tabs 11,12,13", "ohmyjob apply assign --continue", "ohmyjob apply assign --tabs 11,12 --continue",
    "ohmyjob apply next", "ohmyjob apply retry-plan", "ohmyjob apply finish", "ohmyjob apply group-plan",
    "ohmyjob apply cleanup-plan", "ohmyjob apply end", "ohmyjob apply adopt-plan", "ohmyjob apply adopted",
])
def test_application_master_may_run(command):
    assert W.APPLICATION_MASTER_CMD_RE.match(command)


@pytest.mark.parametrize("command", [
    "ohmyjob apply questions", "ohmyjob apply answer --json '[]'", "ohmyjob apply preflight", "ohmyjob apply preflight --tab H",
    "ohmyjob apply assign --tabs 11,", "ohmyjob apply assign --tabs a", "ohmyjob apply start --continue now",
    "ohmyjob apply next; ls", "ohmyjob apply next && ls", "ohmyjob apply end\nls", "ohmyjob prep next",
    "ohmyjob scrape", "ohmyjob apply", "ohmyjob apply finish `id`",
])
def test_application_master_may_not_run(command):
    assert not W.APPLICATION_MASTER_CMD_RE.match(command)
