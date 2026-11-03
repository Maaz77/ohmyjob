"""The ohmyjob workspace contract: where things live, the config, the agent names and the guard command patterns.

Every other script imports its paths, config and names from here and never redefines them.
Standard library only at import time: the hooks import this module on every tool call.

The path constants are computed once, at import, from find_root(). Outside a workspace they point into the current
folder, so that importing never fails; find_root() and load_config() are the strict checks. Code that needs another
workspace sets OHMYJOB_WORKSPACE and runs in a subprocess (or passes `root` to the functions that take one).
"""
from __future__ import annotations

import copy
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Optional

CONFIG_NAME = "ohmyjob.json"  # config and workspace marker: the hooks do nothing without it


class WorkspaceError(Exception):
    """There is no workspace here."""


class ConfigError(WorkspaceError):
    """ohmyjob.json is missing or holds a value the pipeline cannot use."""


# --------------------------------------------------------------------------- where things live


def find_root() -> Path:
    """The workspace folder.

    $OHMYJOB_WORKSPACE (tests, bin/ohmyjob and hook.sh) is used as it is. Else the nearest folder holding ohmyjob.json
    at or above $CLAUDE_PROJECT_DIR (Claude may have been started in a subfolder of the workspace), else at or above
    the current folder.
    """
    forced = os.environ.get("OHMYJOB_WORKSPACE")
    if forced:
        return Path(forced).resolve()
    for start in (os.environ.get("CLAUDE_PROJECT_DIR"), Path.cwd()):
        if start:
            here = Path(start).resolve()
            for folder in (here, *here.parents):
                if (folder / CONFIG_NAME).is_file():
                    return folder
    raise WorkspaceError(
        f"No {CONFIG_NAME} in {Path.cwd()} or above. Start Claude in your job-search folder and run /ohmyjob:setup."
    )


def _root_or_cwd() -> Path:
    try:
        return find_root()
    except WorkspaceError:
        return Path.cwd().resolve()


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ATS_DIR = PLUGIN_ROOT / "ats"
TEMPLATES_DIR = PLUGIN_ROOT / "templates"

ROOT = _root_or_cwd()
CONFIG_FILE = ROOT / CONFIG_NAME
PROFILE = ROOT / "Profile.md"
TRACKER = ROOT / "Job_Tracker.xlsx"
DEFAULT_RESUMES = ROOT / "Default_Resume_Files"  # base.yaml + <Role>.yaml + <Role>.pdf; the pipeline never edits them
BASE_YAML = DEFAULT_RESUMES / "base.yaml"
APPLICATIONS = ROOT / "Applications"
PENDING_REVIEW = ROOT / "Pending-Review"
NEEDS_ATTENTION = ROOT / "Needs-Attention"
SUBMITTED = ROOT / "Submitted"
CLAUDE_MD = ROOT / "CLAUDE.md"
SETTINGS_FILE = ROOT / ".claude" / "settings.json"
STATE_DIR = ROOT / ".ohmyjob"
ENV_FILE = STATE_DIR / "env.sh"  # written by setup: OHMYJOB_PY, OHMYJOB_UV, OHMYJOB_DATA, OHMYJOB_LUALATEX
RUN_PREP = STATE_DIR / "run" / "prep"
RUN_APPLY = STATE_DIR / "run" / "apply"
BUILD_DIR = STATE_DIR / "build"  # one folder per resume build
QUESTIONS_FILE = STATE_DIR / "questions.json"
REPORT_FILE = STATE_DIR / "last_report.txt"

# --------------------------------------------------------------------------- agents and guard commands

PREP_MASTER = "ohmyjob:prep-master"
SCREEN_WORKER = "ohmyjob:screen-worker"
RESUME_WORKER = "ohmyjob:resume-worker"
APPLICATION_MASTER = "ohmyjob:application-master"
APPLICATION_WORKER = "ohmyjob:application-worker"
PREP_AGENTS = frozenset({PREP_MASTER, SCREEN_WORKER, RESUME_WORKER})
PREP_WORKERS = frozenset({SCREEN_WORKER, RESUME_WORKER})
APPLICATION_AGENTS = frozenset({APPLICATION_MASTER, APPLICATION_WORKER})

# The subagent types (Agent tool, `subagent_type`) each master may start. `tools: Agent(...)` in an agent file does not
# restrict plugin agents, so the PreToolUse guards are the only enforcement: they all read this table.
MASTER_WORKERS = {
    PREP_MASTER: frozenset({SCREEN_WORKER, RESUME_WORKER}),
    APPLICATION_MASTER: frozenset({APPLICATION_WORKER}),
}

# The only Bash commands the guards let an agent run (matched against the stripped command).
PREP_MASTER_CMD_RE = re.compile(r"^ohmyjob prep (start|next|record|end|assign-(screen|build)( --max \d+)?)$", re.ASCII)
RESUME_BUILD_CMD_RE = re.compile(r"^ohmyjob prep build (\d+)$", re.ASCII)  # group 1: the job key
APPLICATION_MASTER_CMD_RE = re.compile(
    r"^ohmyjob apply (start( --continue)?|preflight --tab \d+( --home-only)?|assign( --tabs \d+(,\d+)*)?( --continue)?"
    r"|next|retry-plan|finish|group-plan|cleanup-plan|end|adopt-plan|adopted)$",
    re.ASCII,
)

# A worker that stops on questions it cannot answer from the brief reports this blocker.
QUESTIONS_BLOCKER = "questions for the candidate"


def file_prefix(root: Optional[Path] = None) -> str:
    """The ASCII slug of the first name in base.yaml, else "Resume": tailored files are <prefix>_<Company>.pdf/.yaml."""
    import yaml  # not at module level: the hooks import this module on every call

    path = (root or find_root()) / "Default_Resume_Files" / "base.yaml"
    try:
        first = (yaml.safe_load(path.read_text(encoding="utf-8")) or {})["name"]["first"]
    except (OSError, yaml.YAMLError, KeyError, TypeError):
        return "Resume"
    if not isinstance(first, str):
        return "Resume"
    ascii_name = unicodedata.normalize("NFKD", first).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-") or "Resume"


# --------------------------------------------------------------------------- config (ohmyjob.json)

# The JobSpy 1.4.1 values (tests/test_workspace.py checks them against the installed package), plus freehire.
JOBSPY_SOURCES = (
    "linkedin", "indeed", "zip_recruiter", "glassdoor", "google", "bayt", "naukri", "bdjobs", "hellowork",
)
SOURCES = JOBSPY_SOURCES + ("freehire",)
EMPLOYMENT_TYPES = (
    "fulltime", "parttime", "contract", "temporary", "internship", "perdiem", "other", "volunteer",
)
COUNTRIES = (
    "argentina", "australia", "austria", "bahrain", "belgium", "brazil", "canada", "chile", "china",
    "colombia", "costa rica", "czech republic", "denmark", "ecuador", "egypt", "finland", "france",
    "germany", "greece", "hong kong", "hungary", "india", "indonesia", "ireland", "israel", "italy",
    "japan", "kuwait", "luxembourg", "malaysia", "mexico", "morocco", "netherlands", "new zealand",
    "nigeria", "norway", "oman", "pakistan", "panama", "peru", "philippines", "poland", "portugal", "qatar",
    "romania", "saudi arabia", "singapore", "south africa", "south korea", "spain", "sweden", "switzerland",
    "taiwan", "thailand", "türkiye", "ukraine", "united arab emirates", "uk", "usa", "uruguay", "venezuela",
    "vietnam",
)
COUNTRY_ALIASES = {
    "czechia": "czech republic", "turkey": "türkiye", "uae": "united arab emirates",
    "united kingdom": "uk", "us": "usa", "united states": "usa",
}
# The languages simplemma 2.0 can lemmatize (tests/test_workspace.py checks the tuple against the installed package;
# workspace.py itself stays standard-library only). Any other code fails in the middle of a scrape.
LANGUAGES = (
    "ar", "ast", "bg", "ca", "cs", "cy", "da", "de", "el", "en", "enm", "eo", "es", "et", "fa", "fi", "fr", "ga", "gd",
    "gl", "grc", "gv", "hbs", "he", "hi", "hu", "hy", "id", "is", "it", "ka", "la", "lb", "lt", "lv", "mk", "ml", "ms",
    "nb", "nl", "nn", "pl", "pt", "ro", "ru", "se", "sk", "sl", "sq", "sv", "sw", "tl", "tr", "uk",
)
COUNTRY_BOARDS = ("indeed", "glassdoor")  # JobSpy searches the USA on these when no country is given
MAX_RECENCY_DAYS = 3650  # a typo must not stop the run

DEFAULTS: dict[str, dict[str, Any]] = {
    "search": {
        "sources": ["linkedin", "indeed"],  # order = priority when two boards list the same job
        "search_query": [],
        "location": "",
        "country": "",
        "recency_days": 1,
        "results_per_term": 30,
        "employment_types": ["fulltime", "parttime", "contract"],  # [] = every type
        "exclude_companies": [],
        "exclude_seniority_level": [],
        "filter_title_terms": [],
        "title_filter": "strict",
        "title_aliases": {},
        "title_only_aliases": {},
        "languages": ["en"],
        "uk_sponsor_only": False,
        "git_snapshot": False,
    },
    "pipeline": {
        "max_parallel_workers": 5,
        "resume_max_pages": 1,  # 0 = no page limit
        "resume_paper": "a4",
    },
}


def _fail(key: str, problem: str) -> None:
    raise ConfigError(f'{CONFIG_NAME}: "{key}" {problem}')


def _strings(allowed: Optional[tuple] = None, lower: bool = False, nonempty: bool = False) -> Callable:
    def check(key: str, value: Any) -> list:
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            _fail(key, 'must be a list of strings, for example ["a", "b"]')
        items: list[str] = []
        for text in value:
            text = text.strip().lower() if lower else text.strip()
            if not text:
                _fail(key, "must not hold an empty string")
            if allowed is not None and text not in allowed:
                _fail(key, f'has "{text}", which is not valid. Valid values: {", ".join(allowed)}')
            if text not in items:
                items.append(text)
        if nonempty and not items:
            _fail(key, "must hold at least one value")
        return items

    return check


def _whole(minimum: int, maximum: Optional[int] = None) -> Callable:
    def check(key: str, value: Any) -> int:
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum or (maximum is not None and value > maximum):
            _fail(key, f"must be a whole number from {minimum} to {maximum}" if maximum is not None else f"must be a whole number, {minimum} or more")
        return value

    return check


def _flag(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        _fail(key, "must be true or false")
    return value


def _text(key: str, value: Any) -> str:
    if not isinstance(value, str):
        _fail(key, "must be a string")
    return value.strip()


def _choice(*options: str) -> Callable:
    def check(key: str, value: Any) -> str:
        if not isinstance(value, str) or value.strip().lower() not in options:
            _fail(key, f"must be one of: {', '.join(options)}")
        return value.strip().lower()

    return check


def _country(key: str, value: Any) -> str:
    name = _text(key, value).lower()
    name = COUNTRY_ALIASES.get(name, name)
    if name and name not in COUNTRIES:
        _fail(key, f'has "{value}", which JobSpy does not know. Valid countries: {", ".join(COUNTRIES)}')
    return name


def _languages(key: str, value: Any) -> list:
    codes = _strings(lower=True, nonempty=True)(key, value)
    for code in codes:
        if code not in LANGUAGES:
            _fail(key, f'has "{code}", which the text matching does not support. Supported: {", ".join(LANGUAGES)}')
    return codes


def _aliases(key: str, value: Any) -> dict:
    """{"word": "replacement words"} or {"word": ["replacement words", ...]}."""
    if not isinstance(value, dict):
        _fail(key, 'must be an object like {"ops": "operations"}')
    for word, target in value.items():
        targets = target if isinstance(target, list) else [target]
        if not word.strip() or not targets or not all(isinstance(t, str) and t.strip() for t in targets):
            _fail(key, f'has a bad entry for "{word}": use a non-empty word that maps to text or a list of texts')
    return dict(value)


CHECKS: dict[str, Callable] = {
    "search.sources": _strings(SOURCES, lower=True, nonempty=True),
    "search.search_query": _strings(),
    "search.location": _text,
    "search.country": _country,
    "search.recency_days": _whole(0, MAX_RECENCY_DAYS),
    "search.results_per_term": _whole(1),
    "search.employment_types": _strings(EMPLOYMENT_TYPES, lower=True),
    "search.exclude_companies": _strings(),
    "search.exclude_seniority_level": _strings(),
    "search.filter_title_terms": _strings(),
    "search.title_filter": _choice("strict", "off"),
    "search.title_aliases": _aliases,
    "search.title_only_aliases": _aliases,
    "search.languages": _languages,
    "search.uk_sponsor_only": _flag,
    "search.git_snapshot": _flag,
    "pipeline.max_parallel_workers": _whole(1),
    "pipeline.resume_max_pages": _whole(0),
    "pipeline.resume_paper": _choice("a4", "letter"),
}


def validate_config(raw: Any) -> dict:
    """The config with every default filled in and every value checked; ConfigError names the first bad key.

    Keys starting with "$" or "_" at the top level (for example "$schema") are ignored.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME}: the file must hold a JSON object")
    for key in raw:
        if key not in DEFAULTS and not key.startswith(("$", "_")):
            _fail(key, f"is not a known section. Known sections: {', '.join(DEFAULTS)}")
    config = copy.deepcopy(DEFAULTS)
    for section, known in DEFAULTS.items():
        given = raw.get(section, {})
        if not isinstance(given, dict):
            _fail(section, "must be an object")
        for key, value in given.items():
            if key not in known:
                _fail(f"{section}.{key}", f"is not a known setting. Known settings: {', '.join(known)}")
            config[section][key] = CHECKS[f"{section}.{key}"](f"{section}.{key}", value)
    search = config["search"]
    if not search["country"] and any(board in COUNTRY_BOARDS for board in search["sources"]):
        _fail("search.country", f"is empty, but {' and '.join(COUNTRY_BOARDS)} search the USA without a country. "
                                f"Set a country, or leave {' and '.join(COUNTRY_BOARDS)} out of search.sources")
    return config


def load_config(root: Optional[Path] = None) -> dict:
    """ohmyjob.json of the workspace (`root`, else find_root()), validated and merged over DEFAULTS."""
    path = (root or find_root()) / CONFIG_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"{path} not found. Run /ohmyjob:setup.") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    return validate_config(raw)


def slots(config: Optional[dict] = None) -> int:
    """Workers that may run at once: max_parallel_workers, below Claude Code's subagent cap by one (the master counts)."""
    workers = (config or load_config())["pipeline"]["max_parallel_workers"]
    try:
        cap = int(os.environ.get("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS") or 20)
    except ValueError:
        cap = 20
    return max(1, min(workers, cap - 1))
