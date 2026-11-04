"""workspace.py: root discovery, path constants, config validation, the template marker, agent names and guard
command patterns."""
import copy
import json
import subprocess
import sys
from pathlib import Path

import re

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


@pytest.fixture
def project(tmp_path, workspace):
    """A project folder as setup leaves it: the workspace is its subfolder ohmyjob/."""
    folder = tmp_path / "project"
    folder.mkdir()
    workspace.rename(folder / "ohmyjob")
    (folder / "notes").mkdir()
    return folder


INNER = ["ohmyjob", "ohmyjob/Applications", "ohmyjob/Applications/4399000001_Lumenfield-Logistics_Operations-Analyst"]


@pytest.mark.parametrize("start", [".", "notes"] + INNER)
def test_find_workspace_finds_the_ohmyjob_folder_of_the_project(project, start):
    """The project folder holds ohmyjob/; a start inside ohmyjob/, under it or elsewhere in the project finds it."""
    assert W.find_workspace(project / start) == (project / "ohmyjob").resolve()
    assert W.find_workspace(str(project / start)) == (project / "ohmyjob").resolve()


def test_find_workspace_finds_nothing_outside_a_workspace(tmp_path):
    project = tmp_path / "project"
    (project / "ohmyjob" / "Applications").mkdir(parents=True)  # an ohmyjob/ folder without ohmyjob.json
    other = tmp_path / "other"
    other.mkdir()
    (other / "ohmyjob").write_text("a file, not the folder")
    (tmp_path / "file-start").write_text("x")
    for start in (project, project / "ohmyjob", project / "ohmyjob" / "Applications", other, tmp_path / "file-start",
                  tmp_path / "does" / "not" / "exist"):
        assert W.find_workspace(start) is None, start
    for start in (None, "", "relative", "ohmyjob", "."):
        assert W.find_workspace(start) is None, start


def test_find_workspace_prefers_a_folder_with_its_own_marker_to_its_ohmyjob_subfolder(tmp_path):
    outer = tmp_path / "outer"
    (outer / "ohmyjob" / "deep").mkdir(parents=True)
    (outer / "ohmyjob.json").write_text("{}")
    (outer / "ohmyjob" / "ohmyjob.json").write_text("{}")
    assert W.find_workspace(outer) == outer.resolve()
    assert W.find_workspace(outer / "ohmyjob" / "deep") == (outer / "ohmyjob").resolve()


def test_find_root_uses_the_same_rule_from_the_claude_project_dir_and_the_cwd(project, tmp_path, monkeypatch):
    monkeypatch.delenv("OHMYJOB_WORKSPACE")
    expected = (project / "ohmyjob").resolve()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    monkeypatch.chdir(tmp_path)
    assert W.find_root() == expected
    monkeypatch.delenv("CLAUDE_PROJECT_DIR")
    for start in INNER + ["notes", "."]:
        monkeypatch.chdir(project / start)
        assert W.find_root() == expected, start


def test_the_paths_follow_a_workspace_found_from_the_project_folder(project):
    out = run_python("import workspace as W; print(W.ROOT); print(W.PROFILE)", cwd=project)
    root = (project / "ohmyjob").resolve()
    assert out.splitlines() == [str(root), str(root / "Profile.md")]


def test_the_path_constants_follow_the_workspace(workspace):
    out = run_python(
        "import workspace as W, json\n"
        "names = 'ROOT CONFIG_FILE PROFILE TRACKER DEFAULT_RESUMES APPLICATIONS PENDING_REVIEW "
        "NEEDS_ATTENTION SUBMITTED CLAUDE_MD SETTINGS_FILE STATE_DIR ENV_FILE RUN_PREP RUN_APPLY BUILD_DIR "
        "CATALOG_DIR QUESTIONS_FILE REPORT_FILE PLUGIN_ROOT ATS_DIR'.split()\n"
        "print(json.dumps({**{n: str(getattr(W, n)) for n in names}, 'has_base': hasattr(W, 'BASE_YAML')}))",
        workspace=workspace,
    )
    p = json.loads(out)
    assert p.pop("has_base") is False  # one full yaml per resume: there is no base.yaml any more
    p = {name: Path(value) for name, value in p.items()}
    root = workspace.resolve()
    assert p["ROOT"] == root
    assert {
        "CONFIG_FILE": root / "ohmyjob.json", "PROFILE": root / "Profile.md", "TRACKER": root / "Job_Tracker.xlsx",
        "DEFAULT_RESUMES": root / "Default_Resume_Files",
        "APPLICATIONS": root / "Applications", "PENDING_REVIEW": root / "Pending-Review",
        "NEEDS_ATTENTION": root / "Needs-Attention", "SUBMITTED": root / "Submitted", "CLAUDE_MD": root / "CLAUDE.md",
        "SETTINGS_FILE": root / ".claude" / "settings.json", "STATE_DIR": root / ".ohmyjob",
        "ENV_FILE": root / ".ohmyjob" / "env.sh", "RUN_PREP": root / ".ohmyjob" / "run" / "prep",
        "RUN_APPLY": root / ".ohmyjob" / "run" / "apply", "BUILD_DIR": root / ".ohmyjob" / "build",
        "CATALOG_DIR": root / ".ohmyjob" / "catalog",
        "QUESTIONS_FILE": root / ".ohmyjob" / "questions.json", "REPORT_FILE": root / ".ohmyjob" / "last_report.txt",
    } == {name: p[name] for name in (
        "CONFIG_FILE", "PROFILE", "TRACKER", "DEFAULT_RESUMES", "APPLICATIONS", "PENDING_REVIEW",
        "NEEDS_ATTENTION", "SUBMITTED", "CLAUDE_MD", "SETTINGS_FILE", "STATE_DIR", "ENV_FILE", "RUN_PREP",
        "RUN_APPLY", "BUILD_DIR", "CATALOG_DIR", "QUESTIONS_FILE", "REPORT_FILE")}
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


MOCK_SEARCH = {"sources": ["linkedin", "indeed"], "search_query": ["Operations Analyst"], "location": "Berlin, Germany",
               "country": "germany"}
TEMPLATE_LINE = "// OHMYJOB-TEMPLATE: replace the mock values with yours, then delete this line"
REQUIRED = ("sources", "search_query", "location", "country")


def test_the_template_is_json_with_comments_the_mock_values_and_the_marker():
    text = (TEMPLATES / "ohmyjob.json").read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == TEMPLATE_LINE
    template = json.loads(W.strip_comments(text))
    assert template == {"search": MOCK_SEARCH}
    assert "_template" not in text and '"_' not in text  # no hint keys
    expected = copy.deepcopy(W.DEFAULTS)
    expected["search"].update(MOCK_SEARCH)
    assert W.validate_config(template) == expected  # the mock template loads as it is
    assert W.has_template_marker(TEMPLATES / "ohmyjob.json")
    edited = "\n".join(line for line in lines if W.TEMPLATE_MARKER not in line)
    assert W.validate_config(json.loads(W.strip_comments(edited))) == expected


def test_the_comment_block_lists_only_the_required_fields_and_every_valid_value():
    comments = "\n".join(l for l in (TEMPLATES / "ohmyjob.json").read_text(encoding="utf-8").splitlines() if l.startswith("//"))
    for field in REQUIRED:
        assert re.search(rf"//\s+{field}\s", comments), field
    for optional in set(W.DEFAULTS["search"]) - set(REQUIRED):
        assert not re.search(rf"//\s+{optional}\s", comments), optional
    for values in (W.SOURCES, W.COUNTRIES):  # drift guard: a new constant value must reach the comment
        for value in values:
            assert re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", comments), value


def test_comments_are_full_lines_only_and_hint_keys_are_refused(tmp_path):
    text = '// a comment\n  // another\n{"search": {"sources": ["linkedin"],\n// inside\n "location": "x // y"}}\n'
    (tmp_path / "ohmyjob.json").write_text(text)
    config = W.load_config(tmp_path)
    assert config["search"]["location"] == "x // y"  # only a line that STARTS with // is a comment
    for raw in ({"_note": "x"}, {"search": {"_sources": "x"}}, {"_template": "x"}):
        with pytest.raises(W.ConfigError, match="not a known"):
            W.validate_config(raw)


def test_every_setting_has_a_check():
    assert set(W.CHECKS) == {f"{s}.{k}" for s, keys in W.DEFAULTS.items() for k in keys}


def test_every_default_passes_its_own_check():
    assert W.validate_config(W.DEFAULTS) == W.DEFAULTS
    assert W.validate_config({}) == W.DEFAULTS


def test_the_fixture_config_loads(workspace):
    config = W.load_config()
    assert config["search"]["search_query"] == ["Operations Analyst", "Project Coordinator"]
    assert config["search"]["country"] == "germany"
    assert config["search"]["languages"] == ["en", "de"]
    assert "pipeline" not in config


def test_missing_keys_take_their_defaults_and_defaults_are_not_shared(tmp_path):
    config = write_config(tmp_path, {"search": {"location": " Berlin ", "country": "germany"}})
    assert config["search"]["location"] == "Berlin"
    assert config["search"]["sources"] == ["linkedin"]
    config["search"]["sources"].append("google")
    assert W.DEFAULTS["search"]["sources"] == ["linkedin"]


def test_removed_settings_are_unknown_keys(tmp_path):
    for key in ("title_aliases", "title_only_aliases", "git_snapshot", "resume_max_pages", "max_parallel_workers"):
        with pytest.raises(W.ConfigError, match=f"search.{key}"):
            write_config(tmp_path, {"search": {key: 1}})
    with pytest.raises(W.ConfigError, match='"pipeline" is not a known section'):
        write_config(tmp_path, {"pipeline": {"resume_paper": "a4"}})
    for key in ("$schema", "template", "Template"):
        with pytest.raises(W.ConfigError, match=f'"{re.escape(key)}" is not a known section'):
            write_config(tmp_path, {key: "x"})


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
    config = write_config(tmp_path, {"search": {"sources": ["Indeed", " linkedin", "indeed", "FREEHIRE"], "country": "uk"}})
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
    assert write_config(tmp_path, {"search": {"country": "", "sources": ["linkedin", "freehire"]}})["search"]["country"] == ""
    with pytest.raises(W.ConfigError) as exc:
        write_config(tmp_path, {"search": {"country": "Atlantis"}})
    assert '"search.country" has "Atlantis"' in str(exc.value)
    assert "germany" in str(exc.value) and "türkiye" in str(exc.value)


@pytest.mark.parametrize("board", ["indeed", "glassdoor"])
@pytest.mark.parametrize("country", ["", "  "])
def test_a_country_is_required_when_a_board_searches_the_usa_without_one(tmp_path, board, country):
    with pytest.raises(W.ConfigError, match=f'"search.country" is empty.*{board}'):
        write_config(tmp_path, {"search": {"sources": ["linkedin", board], "country": country}})
    assert write_config(tmp_path, {"search": {"sources": ["linkedin", board], "country": "usa"}})["search"]["country"] == "usa"
    assert write_config(tmp_path, {"search": {"sources": ["linkedin", "google"]}})["search"]["country"] == ""


@pytest.mark.parametrize("code", ["ja", "zh", "xx", "ko", "en-US", "english"])
def test_a_language_that_the_text_matching_cannot_handle_is_refused_at_load(tmp_path, code):
    with pytest.raises(W.ConfigError, match=f'"search.languages" has "{code.lower()}"'):
        write_config(tmp_path, {"search": {"country": "germany", "languages": ["en", code]}})


def test_employment_types_are_checked_and_may_be_empty(tmp_path):
    assert write_config(tmp_path, {"search": {"country": "usa", "employment_types": ["FullTime"]}})["search"]["employment_types"] == ["fulltime"]
    assert write_config(tmp_path, {"search": {"country": "usa", "employment_types": []}})["search"]["employment_types"] == []
    with pytest.raises(W.ConfigError, match="full-time"):
        write_config(tmp_path, {"search": {"country": "usa", "employment_types": ["full-time"]}})


@pytest.mark.parametrize("key, bad", [
    ("recency_days", -1), ("recency_days", 3651), ("recency_days", True), ("recency_days", 1.5), ("recency_days", "1"),
    ("results_per_term", 0), ("results_per_term", None),
    ("uk_sponsor_only", 1),
    ("location", 5), ("country", 5),
    ("title_filter", "loose"), ("title_filter", None),
    ("languages", []), ("languages", ["english"]), ("languages", "en"),
    ("exclude_companies", "Example Staffing"), ("filter_title_terms", [1]),
])
def test_bad_search_values_are_refused_with_their_key(tmp_path, key, bad):
    with pytest.raises(W.ConfigError, match=f'"search.{key}"'):
        write_config(tmp_path, {"search": {key: bad}})


def test_good_edge_values_are_kept(tmp_path):
    config = write_config(tmp_path, {
        "search": {"country": "usa", "recency_days": 0, "title_filter": "OFF", "languages": ["DE", "fr"]},
    })
    assert config["search"]["recency_days"] == 0
    assert config["search"]["title_filter"] == "off"
    assert config["search"]["languages"] == ["de", "fr"]


def test_unknown_sections_and_settings_are_refused_with_the_known_ones(tmp_path):
    with pytest.raises(W.ConfigError, match='"serach" is not a known section'):
        write_config(tmp_path, {"serach": {}})
    with pytest.raises(W.ConfigError, match=r'"search.seach_query" is not a known setting.*search_query'):
        write_config(tmp_path, {"search": {"seach_query": ["x"]}})


def test_the_valid_values_match_the_pinned_jobspy():
    from jobspy.model import Country, JobType, Site, country_aliases

    assert sorted(W.JOBSPY_SOURCES) == sorted(s.value for s in Site)
    assert sorted(W.COUNTRIES) == sorted(c.value for c in Country)
    assert W.COUNTRY_ALIASES == country_aliases
    assert sorted(W.EMPLOYMENT_TYPES) == sorted(j.value for j in JobType)


def test_the_languages_match_the_pinned_simplemma():
    from simplemma.strategies.dictionaries.dictionary_factory import SUPPORTED_LANGUAGES

    assert sorted(W.LANGUAGES) == sorted(SUPPORTED_LANGUAGES)


# --------------------------------------------------------------------------- slots


def test_slots_is_the_subagent_cap_minus_one(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", raising=False)
    assert W.slots() == 19  # default cap 20
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "60")
    assert W.slots() == 59
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "1")
    assert W.slots() == 1  # never below one
    monkeypatch.setenv("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS", "many")
    assert W.slots() == 19


# --------------------------------------------------------------------------- file_prefix


def write_profile(root, candidate_lines, before=""):
    (root / "Profile.md").write_text(f"{before}# Resume\n\n{candidate_lines}\n\n# Fit Preferences\n\n- x\n",
                                     encoding="utf-8")


def test_file_prefix_is_the_first_name_of_the_candidate_in_profile_md(workspace):
    assert W.file_prefix() == "Alex"
    assert W.file_prefix(workspace) == "Alex"


@pytest.mark.parametrize("name, prefix", [
    ("José Example", "Jose"), ("Mary-Ann Smith", "Mary-Ann"), ("O'Neil X", "O-Neil"), ("Zoë_Marie X", "Zoe-Marie"),
    ("Alex", "Alex"), ("  Alex   Example  ", "Alex"), ("**Alex** Example", "Alex"), ("李 小龙", "Resume"), ("", "Resume"),
])
def test_file_prefix_is_an_ascii_slug_of_the_first_word(workspace, name, prefix):
    write_profile(workspace, f"- Name: {name}")
    assert W.file_prefix() == prefix


@pytest.mark.parametrize("line", ["- Name: Robin Doe", "- **Name:** Robin Doe", "- **Name**: Robin Doe", "* name : Robin Doe",
                                  "  - NAME:Robin Doe"])
def test_file_prefix_reads_the_usual_ways_to_write_the_name_line(workspace, line):
    write_profile(workspace, f"- Email: robin@example.com\n{line}\n- Phone: 1")
    assert W.file_prefix() == "Robin"


def test_file_prefix_reads_only_the_resume_section(workspace):
    # a name line in another section does not count, a subsection of Resume does, and the first name line wins
    write_profile(workspace, "## Contact\n\n- Name: Robin Doe\n- Name: Kim Roe", before="# Fit Preferences\n\n- Name: Pat Poe\n\n")
    assert W.file_prefix() == "Robin"


@pytest.mark.parametrize("content", [
    None, "", "just text", "- Name: Alex Example", "# Candidate\n\n- Name: Alex Example\n",
    "## Resume\n\n- Name: Alex Example\n", "# Resume\n\nAlex Example\n", "# Resume\n\n- Names: Alex\n",
    "# Resume\n\n- Name:\n- Name: Alex\n", "# Fit Preferences\n- x\n# Resumes\n- Name: Alex\n",
])
def test_file_prefix_falls_back_to_resume(workspace, content):
    profile = workspace / "Profile.md"
    if content is None:
        profile.unlink()
    else:
        profile.write_text(content, encoding="utf-8")
    assert W.file_prefix() == "Resume"


def test_file_prefix_does_not_read_any_resume_yaml(workspace):
    for path in (workspace / "Default_Resume_Files").glob("*.yaml"):
        path.write_text("name: {first: Someone, last: Else}\n", encoding="utf-8")
    (workspace / "Default_Resume_Files" / "base.yaml").write_text("name: {first: Someone, last: Else}\n")
    assert W.file_prefix() == "Alex"


# --------------------------------------------------------------------------- the template marker


def test_the_marker_string():
    assert W.TEMPLATE_MARKER == "OHMYJOB-TEMPLATE"


def test_has_template_marker_is_a_raw_text_search(tmp_path):
    path = tmp_path / "Profile.md"
    assert W.has_template_marker(path) is False  # missing
    assert W.has_template_marker(tmp_path / "no" / "such" / "dir" / "Profile.md") is False
    assert W.has_template_marker(tmp_path) is False  # a folder is no file
    for data in (b"<!-- OHMYJOB-TEMPLATE: replace the mock data with yours, then delete this line -->\n# Candidate\n",
                 b"# Candidate\n- Name: Alex Example\n\nnote: OHMYJOB-TEMPLATE in the middle\n",
                 b'{"_template": "OHMYJOB-TEMPLATE: x", "search": {}}', b"not text \xff\x00 OHMYJOB-TEMPLATE \xfe"):
        path.write_bytes(data)
        assert W.has_template_marker(path) is True, data
        assert W.has_template_marker(str(path)) is True
    for data in (b"# Candidate\n- Name: Alex Example\n", b"ohmyjob-template (other case)", b"OHMYJOB TEMPLATE", b""):
        path.write_bytes(data)
        assert W.has_template_marker(path) is False, data


def test_template_files_names_the_files_that_still_hold_the_marker(workspace):
    assert W.template_files() == [] and W.template_files(workspace) == []  # the fixture is an edited workspace
    config = workspace / "ohmyjob.json"
    profile = workspace / "Profile.md"
    profile_text = profile.read_text(encoding="utf-8")
    profile.write_text("<!-- OHMYJOB-TEMPLATE: replace the mock data with yours, then delete this line -->\n"
                       + profile_text, encoding="utf-8")
    assert W.template_files(workspace) == [profile]
    config.write_text((TEMPLATES / "ohmyjob.json").read_text(encoding="utf-8"), encoding="utf-8")
    assert W.template_files(workspace) == [config, profile]
    profile.write_text(profile_text, encoding="utf-8")
    assert W.template_files() == [config]
    profile.unlink()  # a missing file holds no marker
    assert W.template_files() == [config]
    # the marker does not stop the config from loading: the guards decide, per command
    assert W.load_config(workspace)["search"]["search_query"] == ["Operations Analyst"]


# --------------------------------------------------------------------------- unreadable files


def symlink_loop(path):
    """Replace `path` by a symlink to itself: reading it fails (ELOOP) even as root, who reads a chmod 000 file."""
    path.unlink(missing_ok=True)
    path.symlink_to(path.name)
    return path


def deny_reads(monkeypatch, *names):
    """Reading a file with one of these names raises PermissionError, as without read permission (the tests may run as
    root, who reads a chmod 000 file)."""
    def denied(real):
        def read(self, *args, **kwargs):
            if self.name in names:
                raise PermissionError(13, "Permission denied", str(self))
            return real(self, *args, **kwargs)
        return read

    monkeypatch.setattr(Path, "read_bytes", denied(Path.read_bytes))
    monkeypatch.setattr(Path, "read_text", denied(Path.read_text))


def test_file_read_error_is_a_workspace_error_and_an_os_error():
    # callers print one FATAL line for every WorkspaceError; code that skips an unreadable file on OSError keeps working
    assert issubclass(W.FileReadError, W.WorkspaceError) and issubclass(W.FileReadError, OSError)
    assert str(W.FileReadError("x")) == "x"


@pytest.mark.parametrize("make_unreadable", ["permission", "loop"])
def test_an_unreadable_profile_or_resume_is_one_error_that_names_the_file(workspace, monkeypatch, make_unreadable):
    profile = workspace / "Profile.md"
    resume = workspace / "Default_Resume_Files" / "Operations-Analyst.yaml"
    if make_unreadable == "permission":
        deny_reads(monkeypatch, "Profile.md", resume.name)
        reason = "Permission denied"
    else:
        symlink_loop(profile)
        symlink_loop(resume)
        reason = "Too many levels of symbolic links"
    for call in (lambda: W.has_template_marker(profile), lambda: W.template_files(workspace), W.file_prefix):
        with pytest.raises(W.FileReadError) as exc:
            call()
        assert str(exc.value) == f"{profile} cannot be read ({reason}). Make it a readable file, then run the command again."
    with pytest.raises(W.FileReadError, match=f"^{re.escape(str(resume))} cannot be read"):
        W.has_template_marker(resume)


@pytest.mark.parametrize("make_unreadable", ["permission", "loop"])
def test_an_unreadable_config_is_one_error_that_names_the_file(workspace, monkeypatch, make_unreadable):
    config = workspace / "ohmyjob.json"
    if make_unreadable == "permission":
        deny_reads(monkeypatch, "ohmyjob.json")
    else:
        symlink_loop(config)
    with pytest.raises(W.ConfigError, match=f"^{re.escape(str(config))} cannot be read \\(.+\\)\\. Make it a readable file"):
        W.load_config(workspace)
    with pytest.raises(W.FileReadError, match=f"^{re.escape(str(config))} cannot be read"):
        W.template_files(workspace)


def test_text_that_is_not_utf8_is_one_error_that_names_the_file(workspace):
    profile = workspace / "Profile.md"
    profile.write_bytes(b"# Resume\n\n- Name: Jos\xe9 Example\n")  # Latin-1, not UTF-8
    with pytest.raises(W.FileReadError) as exc:
        W.file_prefix()
    assert str(exc.value) == f"{profile} is not UTF-8 text (byte 21). Save it as UTF-8, then run the command again."
    assert W.has_template_marker(profile) is False  # the marker check reads bytes: nothing to decode
    profile.write_bytes(b"<!-- OHMYJOB-TEMPLATE -->\n- Name: Jos\xe9\n")
    assert W.has_template_marker(profile) is True
    config = workspace / "ohmyjob.json"
    config.write_bytes(b'{"search": {"location": "Z\xfcrich"}}')
    with pytest.raises(W.ConfigError, match=f"^{re.escape(str(config))} is not UTF-8 text \\(byte 26\\)"):
        W.load_config()


def test_a_folder_in_place_of_a_file_counts_as_missing_for_the_profile_and_is_an_error_for_the_config(workspace):
    (workspace / "Profile.md").unlink()
    (workspace / "Profile.md").mkdir()
    assert W.file_prefix() == "Resume"
    assert W.has_template_marker(workspace / "Profile.md") is False
    (workspace / "ohmyjob.json").unlink()
    (workspace / "ohmyjob.json").mkdir()
    with pytest.raises(W.ConfigError, match="ohmyjob.json cannot be read"):
        W.load_config()


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
