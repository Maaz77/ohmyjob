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
WORKSPACE_DIR = "ohmyjob"  # the workspace folder setup creates in the project folder (the one Claude starts in)


class WorkspaceError(Exception):
    """There is no workspace here."""


class ConfigError(WorkspaceError):
    """ohmyjob.json is missing or holds a value the pipeline cannot use."""


class FileReadError(WorkspaceError, OSError):
    """A workspace file (Profile.md, a resume yaml) exists but cannot be read: no permission, not UTF-8 text.

    The message names the file, so a caller that catches WorkspaceError prints one FATAL line. It is an OSError too, so
    code that already handles an unreadable file the OSError way keeps working.
    """


def _unreadable(path: str | os.PathLike, exc: Exception) -> str:
    """One line that names the file and says what to do."""
    if isinstance(exc, UnicodeDecodeError):
        return f"{path} is not UTF-8 text (byte {exc.start}). Save it as UTF-8, then run the command again."
    reason = (exc.strerror if isinstance(exc, OSError) else None) or str(exc) or type(exc).__name__
    return f"{path} cannot be read ({reason}). Make it a readable file, then run the command again."


_MISSING = (FileNotFoundError, NotADirectoryError, IsADirectoryError)  # no such file (a folder is none either)


# --------------------------------------------------------------------------- where things live


def find_workspace(start: str | os.PathLike | None) -> Path | None:
    """The workspace for a start folder, by the one discovery rule (hook.sh, bin/ohmyjob and the hooks use it too).

    Walk up from `start` (symlinks resolved). At each folder D: D itself when D/ohmyjob.json exists, else D/ohmyjob when
    D/ohmyjob/ohmyjob.json exists. So the project folder (which holds ohmyjob/), any folder inside ohmyjob/ and any
    other subfolder of the project all lead to the same workspace. None when nothing is found, and for an empty start or
    one that is not an absolute path.
    """
    if not start or not os.path.isabs(start):
        return None
    try:
        here = Path(start).resolve()
    except (OSError, RuntimeError):
        return None
    for folder in (here, *here.parents):
        if os.path.isfile(folder / CONFIG_NAME):
            return folder
        if os.path.isfile(folder / WORKSPACE_DIR / CONFIG_NAME):
            return folder / WORKSPACE_DIR
    return None


def find_root() -> Path:
    """The workspace folder.

    $OHMYJOB_WORKSPACE (tests, bin/ohmyjob and the hooks) is used as it is. Else find_workspace() from
    $CLAUDE_PROJECT_DIR (the folder Claude was started in), else from the current folder.
    """
    forced = os.environ.get("OHMYJOB_WORKSPACE")
    if forced:
        return Path(forced).resolve()
    for start in (os.environ.get("CLAUDE_PROJECT_DIR"), Path.cwd()):
        root = find_workspace(start)
        if root is not None:
            return root
    raise WorkspaceError(
        f"No {CONFIG_NAME} in {Path.cwd()} or above, nor in an {WORKSPACE_DIR}/ folder there. "
        "Start Claude in your project folder and run /ohmyjob:setup."
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
# One full resume per file: Template.yaml/.pdf, <Field>.yaml + <Field>.pdf, ready-made <Field>.pdf; never edited by
# the pipeline.
DEFAULT_RESUMES = ROOT / "Default_Resume_Files"
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
CATALOG_DIR = STATE_DIR / "catalog"  # the screening catalog: the text of every usable resume (prep start writes it)
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


# --------------------------------------------------------------------------- Profile.md and the templates

# Setup writes the templates with mock data and this marker on a line the user deletes after editing: the first line
# of Profile.md and of Template.yaml, and the `// OHMYJOB-TEMPLATE` comment line of ohmyjob.json. A file that still holds it is not the
# user's own: the commands refuse to use it (docs/CONTRACTS.md, "Templates and the mock guard").
TEMPLATE_MARKER = "OHMYJOB-TEMPLATE"

_LEVEL1_HEADING_RE = re.compile(r"^#\s+(.*?)\s*$")  # "# Resume"; "## ..." is a subsection
_NAME_LINE_RE = re.compile(r"^\s*[-*+]\s+(?:\*\*)?name(?:\s*:\s*\*\*|\*\*\s*:|\s*:)\s*(.*)$", re.IGNORECASE)


def has_template_marker(path: str | os.PathLike) -> bool:
    """True when the file holds TEMPLATE_MARKER anywhere (a raw text search of its bytes, nothing is parsed or decoded);
    False when there is no such file. A file that exists but cannot be read raises FileReadError (which names it)."""
    try:
        return TEMPLATE_MARKER.encode("ascii") in Path(path).read_bytes()
    except _MISSING:
        return False
    except OSError as exc:
        raise FileReadError(_unreadable(path, exc)) from None


def template_files(root: Optional[Path] = None) -> list[Path]:
    """The files of ohmyjob.json and Profile.md (in this order) of the workspace (`root`, else find_root()) that still
    hold the template marker: an empty list when the user has edited both. An unreadable one raises FileReadError."""
    root = root or find_root()
    return [path for path in (root / CONFIG_NAME, root / "Profile.md") if has_template_marker(path)]


def file_prefix(root: Optional[Path] = None) -> str:
    """The ASCII slug of the first name in Profile.md, else "Resume": tailored files are <prefix>_<Company>.pdf/.yaml.

    The name is the line `- Name: <First> <Last>` (also `- **Name:** ...`) inside the level-1 section `# Resume` of
    Profile.md; the first word of it is the first name. No Profile.md: "Resume". A Profile.md that cannot be read or is
    not UTF-8 text raises FileReadError (which names it): a silent "Resume" would name the files of one run differently.
    """
    path = (root or find_root()) / "Profile.md"
    try:
        text = path.read_text(encoding="utf-8")
    except _MISSING:
        return "Resume"
    except (OSError, UnicodeDecodeError) as exc:
        raise FileReadError(_unreadable(path, exc)) from None
    section = None
    for line in text.splitlines():
        heading = _LEVEL1_HEADING_RE.match(line)
        if heading:
            section = heading.group(1).strip().lower()
            continue
        match = _NAME_LINE_RE.match(line) if section == "resume" else None
        if match:
            words = match.group(1).split()
            if not words:
                return "Resume"
            ascii_name = unicodedata.normalize("NFKD", words[0]).encode("ascii", "ignore").decode()
            return re.sub(r"[^A-Za-z0-9]+", "-", ascii_name).strip("-") or "Resume"
    return "Resume"


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
        "sources": ["linkedin"],  # order = priority when two boards list the same job
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
        "languages": ["en"],
        "uk_sponsor_only": False,
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
    "search.languages": _languages,
    "search.uk_sponsor_only": _flag,
}


def validate_config(raw: Any) -> dict:
    """The config with every default filled in and every value checked; ConfigError names the first bad key.

    Unknown keys are errors. The template marker is a `//` comment line (see load_config), not a key.
    """
    if not isinstance(raw, dict):
        raise ConfigError(f"{CONFIG_NAME}: the file must hold a JSON object")
    for key in raw:
        if key not in DEFAULTS:
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


def strip_comments(text: str) -> str:
    """ohmyjob.json is JSON with full-line `//` comments: drop the lines whose first non-blank characters are `//`."""
    return "\n".join("" if line.lstrip().startswith("//") else line for line in text.splitlines())


def load_config(root: Optional[Path] = None) -> dict:
    """ohmyjob.json of the workspace (`root`, else find_root()), validated and merged over DEFAULTS."""
    path = (root or find_root()) / CONFIG_NAME
    try:
        raw = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise ConfigError(f"{path} not found. Run /ohmyjob:setup.") from None
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None
    except (OSError, UnicodeDecodeError) as exc:  # no permission, a folder, not UTF-8: one line that names the file
        raise ConfigError(_unreadable(path, exc)) from None
    return validate_config(raw)


def slots() -> int:
    """Workers that may run at once: Claude Code's subagent cap (CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS, else 20) minus
    one for the master."""
    try:
        cap = int(os.environ.get("CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS") or 20)
    except ValueError:
        cap = 20
    return max(1, cap - 1)
