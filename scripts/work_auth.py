"""Work authorization blocker of stage 2 (pj.py calls check() before it starts a screen worker).

A posting is blocked ONLY when both hold: it states a work authorization requirement in so many words, and the
`# Work Authorization` section of Profile.md says the candidate does not meet it for the job's country. Everything else
is "do not block": silence, positive or hedged wording, a country that cannot be told, a country or a section the
profile does not mention, a line that does not start with a known status. A missing answer is unknown, never "no".

Choices, and why:
- The candidate's side is a structured section of Profile.md, one line per country: `- <Country>: <status>. own words`
  with the status one of `citizen`, `permanent resident`, `authorized` (may work there without sponsorship) or
  `needs sponsorship`. A line `- EU: ...` covers every EU country without a line of its own. `- Security clearance: none`
  says there is no clearance. The free-text Follow Up Questions stay what they are (answers for forms): their
  per-country wording cannot be read without guessing, and a guess would be a false block.
- The job's country is the one country that the `Location` of job.md names (a country name, or a two-letter code in the
  last part of a "city, region, code" location). Remote, blank, two countries or none: unknown. ohmyjob.json is not used:
  a bare city ("Berlin") is as unclear as no location, and the search country says where the board was asked, not where
  the job is.
- The posting is read as data with fixed English and German phrases (search.languages is not consulted). A posting
  in any other language is never blocked. A sentence that is hedged ("unless", "case by case", "preferred", a question)
  counts for nothing, and any other sentence that talks of sponsorship in positive words cancels the sponsorship and
  right-to-work phrases of the posting: a posting that contradicts itself is not explicit.
- Clearance: blocked only when the candidate says `none`; a held clearance is not compared (levels differ by country).
  Only security, secret and named clearances count: "customs clearance" is not one.
"""
from __future__ import annotations

import re
from typing import NamedTuple, Optional

from freehire_source import COUNTRY_CODES

PREFIX = "Work authorization block:"  # starts the Notes entry of every blocked job: the skips can be counted by it
SECTION = "Work Authorization"
QUOTE = 200  # characters of a quoted posting sentence
NEEDS, AUTH, RESIDENT, CITIZEN = 0, 1, 2, 3  # what the candidate has, and what a posting asks for
CLEARANCE = "clearance"
STATUS_RE = re.compile(r"(citizen|permanent resident|authori[sz]ed|needs? sponsorship)\b", re.I)
EU = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT",
      "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE"}
CODES = set(COUNTRY_CODES.values()) | EU


class Verdict(NamedTuple):
    blocked: bool
    text: str  # the Notes entry of a block, else the gap line that tells the candidate what was not checked


# --------------------------------------------------------------------------- the job's country


REMOTE_RE = re.compile(r"remote|anywhere|worldwide|global|home ?office|telearbeit|t[eé]l[eé]travail|teletrabajo", re.I)


def job_country(location: str) -> Optional[str]:
    """The ISO code of the one country the location names, else None (remote, blank, none or several countries)."""
    if REMOTE_RE.search(location or ""):
        return None
    parts = [p.strip().casefold() for p in re.split(r"[,;/|]", location or "") if p.strip()]
    found = {COUNTRY_CODES[p] for p in parts if p in COUNTRY_CODES}
    # "Berlin, BE, DE": a code counts only as the third part or later ("Wilmington, DE" is Delaware as likely as Germany)
    found |= {parts[-1].upper()} if len(parts) >= 3 and parts[-1].upper() in CODES else set()
    return found.pop() if len(found) == 1 else None


# --------------------------------------------------------------------------- the candidate


def profile_rows(profile: str) -> dict[str, list[tuple[Optional[int], str]]]:
    """{country code, "EU" or "clearance": [(level or None, the line's text)]} from the Work Authorization section."""
    rows: dict[str, list] = {}
    inside = False
    for line in profile.splitlines():
        if line.startswith("# "):
            inside = line[2:].strip().casefold() == SECTION.casefold()
            continue
        match = re.match(r"^\s*[-*]\s+(.+?)\s*:\s*(.+?)\s*$", line.replace("**", "")) if inside else None
        if not match:
            continue
        name, value = match.group(1).casefold(), match.group(2)
        key = "clearance" if "clearance" in name else "EU" if name in ("eu", "european union") else COUNTRY_CODES.get(name)
        if key is None:
            continue
        status = STATUS_RE.match(value)
        word = status.group(1).lower() if status else ""
        level = None if not status else CITIZEN if word == "citizen" else RESIDENT if word.startswith("perm") else AUTH if word.startswith("auth") else NEEDS
        rows.setdefault(key, []).append((level, f"{match.group(1)}: {value}"))
    return rows


def candidate_level(rows: dict, code: str) -> Optional[tuple[int, str]]:
    """(level, line) the profile states for this country (its own line before an EU line), else None."""
    mine = rows.get(code) or (rows.get("EU") if code in EU else None) or []
    levels = {level for level, _ in mine}
    return (mine[0][0], mine[0][1]) if len(levels) == 1 and None not in levels else None


# --------------------------------------------------------------------------- the posting

_NEG = r"(?:unable to|not able to|cannot|can ?not|can't|won't|will not|do not|don't|does not|doesn't|not in a position to|not currently able to)"
_VISA = r"(?:visas?|visa sponsorship|sponsorship|work permits?|work authori[sz]ation|work visas?|employment visas?)"
_PEND = r"(?:\w+\s+){0,2}"
# "sponsor" with no object ("we do not sponsor.") or a visa-like one, never "we do not sponsor events"
_SPONSOR = r"sponsor(?:\s+(?:visas?|work|employment|any|anyone|candidates?|applicants?|employees?|workers?|immigration|for|this|that|h-?1b|international)\b|\s*(?:[.,;:!)]|$))"
_MODAL = r"(?:must|need to|needs to|required to|have to|should)"
NO_SPONSOR = [  # level AUTH: the candidate must work there without sponsorship
    rf"\b{_NEG}\s+(?:currently\s+|presently\s+|now\s+)?(?:{_SPONSOR}|(?:offer|provide|support)\s+{_PEND}{_VISA})",
    r"\bno\s+(?:(?:visa|work permit|work authori[sz]ation|employment visa|work visa|immigration)\s+)?sponsorship\b"
    r"(?!\s+(?:is\s+)?(?:required|needed|necessary))",
    r"\bsponsorship\s+(?:is\s+|are\s+|will\s+be\s+)?(?:not\s+(?:available|offered|provided|possible|supported)|unavailable)\b",
    r"\bnot\s+(?:eligible|available)\s+for\s+(?:\w+\s+){0,2}sponsorship",
    rf"\b{_MODAL}\b[^.]{{0,60}}?\bnot\s+{_PEND}(?:require|need)\w*\s+{_PEND}sponsorship",
    r"\b(?:must|able to|eligible to|authori[sz]ed to|right to work)\b[^.]{0,80}?\bwithout\s+(?:requiring\s+|needing\s+|the need for\s+)?"
    r"(?:(?:visa|employer|work visa)\s+)?sponsorship",
    rf"\b{_MODAL}\b(?:\s+\w+){{0,3}}?\s+(?:have|hold|possess)\b(?:\s+\w+){{0,3}}?\s+(?:right|legal right|permission|authori[sz]ation|eligibility)\s+to\s+work\b",
    rf"\b{_MODAL}\b(?:\s+\w+){{0,3}}?\s+be\s+(?:(?:legally|currently|already|fully)\s+)*(?:eligible|authori[sz]ed|entitled|permitted|allowed)\s+to\s+work\b",
]
RIGHT_TO_WORK_REQUIRED = r"\b(?:right to work|work authori[sz]ation|work permit|work visa)\b[^.]{0,30}?\b(?:is\s+|are\s+)?(?:required|essential|mandatory|a must)\b"
GERMAN = [  # level AUTH, German
    r"\bkein\w*\s+(?:visa|visum|arbeitserlaubnis|arbeitsgenehmigung)\w*[- ]?(?:sponsoring|sponsorship|unterst[uü]tzung|unterstuetzung|sponsern)",
    r"\bsponsoring\b[^.]{0,20}?\bnicht\s+(?:m[oö]glich|angeboten|verf[uü]gbar)",
]
GERMAN_REQUIRED = r"\barbeits(?:erlaubnis|genehmigung)\b[^.]{0,40}?\b(?:erforderlich|voraussetzung|notwendig|zwingend|mitbringen)\b"
CITIZEN_ONLY = [r"\bcitizens?\s+only\b", r"(?<!not\s)\bonly\s+(?:[\w.]+\s+){0,3}?citizens?\b",
                r"\bmust\s+be\s+(?:an?\s+)?(?:[\w.\-]+\s+){0,2}?citizen\b"]
CITIZEN_REQUIRED = [r"\b(?:citizenship|citizen)\b[^.]{0,20}?\b(?:is\s+)?(?:required|mandatory)\b"]  # only without a "no" or "not"
RESIDENT_ONLY = [r"\b(?:must|need to|required to|have to)\b[^.]{0,40}?\b(?:permanent residen\w+|green card)\b"]
RESIDENT_REQUIRED = [r"\b(?:permanent residen\w+|green card)\b[^.]{0,20}?\b(?:required|mandatory)\b"]
CLEARANCE_RE = r"\b(?:security clearance|(?:top secret|secret|public trust|ts|dv|sc|ctc|nato)\s+clearance|ts/sci|top secret)\b"
CLEARANCE_NEED = r"\b(?:required|requires|must (?:have|hold|possess)|mandatory|active|current|valid|essential)\b"
CLEARANCE_NOT = (r"\b(?:obtain\w*|eligib\w*|able to (?:get|obtain)|ability|willing\w*|prefer\w*|desirable|plus|nice|bonus|ideal\w*|"
                 r"advantage|apply\w*|process|sponsor\w*|undergo|pass)\b")
HEDGE = (r"\b(?:unless|except\w*|case[- ]by[- ]case|exceptional\w*|may consider|will consider|depending|in some cases|for the right|"
         r"if (?:you|the|your|a|an)|prefer\w*|desirable|ideally|nice to have|a plus|bonus|advantage|welcome|encourage\w*)\b")
ALT_OTHER = r"\b(?:permits?|visas?|authori[sz]\w*|eligib\w*|right to work|work rights?|settled)\b"
ALT_RESIDENT = r"\b(?:permanent|resident\w*|residency|green card|lawful)\b"
GERMAN_NOT = r"\b(?:nicht|kein\w*|ohne|unterst[uü]tz\w*|beantrag\w*)\b"


def _search(patterns: list[str], sentence: str) -> Optional[re.Match]:
    for pattern in patterns:
        match = re.search(pattern, sentence, re.I)
        if match:
            return match
    return None


def sentences(text: str) -> list[str]:
    text = text.replace("’", "'").replace("**", "").replace("\\", "")
    return [" ".join(s.split()) for s in re.split(r"(?<=[.!?;])(?<!\b\w\.)\s+|\n+|\s[•·]\s", text) if s.strip()]


def requirements(description: str) -> list[tuple]:
    """[(level or CLEARANCE, quoted text)] for every sentence of the description that states a requirement outright."""
    found, positive = [], False
    for sentence in sentences(description):
        hits = []
        negated = re.search(r"\b(?:no|not|without|never)\b|n't", sentence, re.I)
        german_negated = re.search(GERMAN_NOT, sentence, re.I)
        for match in (_search(NO_SPONSOR, sentence),
                      None if negated else re.search(RIGHT_TO_WORK_REQUIRED, sentence, re.I),
                      _search(GERMAN, sentence),
                      None if german_negated else re.search(GERMAN_REQUIRED, sentence, re.I)):
            if match:
                hits.append((AUTH, match))
        citizen = _search(CITIZEN_ONLY, sentence) or (None if negated else _search(CITIZEN_REQUIRED, sentence))
        resident = _search(RESIDENT_ONLY, sentence) or (None if negated else _search(RESIDENT_REQUIRED, sentence))
        if (citizen or resident) and not re.search(ALT_OTHER, sentence, re.I):
            if citizen and not re.search(ALT_RESIDENT, sentence, re.I):
                hits.append((CITIZEN, citizen))
            else:
                hits.append((RESIDENT, citizen or resident))
        clearance = re.search(CLEARANCE_RE, sentence, re.I)
        if clearance and not negated and re.search(CLEARANCE_NEED, sentence, re.I) and not re.search(CLEARANCE_NOT, sentence, re.I):
            hits.append((CLEARANCE, clearance))
        if "?" in sentence or re.search(HEDGE, sentence, re.I):
            hits = []  # a question or a hedged sentence states nothing outright
        if not hits and re.search(r"sponsor|visa (?:support|assistance)", sentence, re.I):
            positive = True  # talks of sponsorship in positive, hedged or unclear words
        for level, match in hits:
            start = max(0, match.start() - 80)
            found.append((level, sentence[start:start + QUOTE]))
    return [item for item in found if not (positive and item[0] == AUTH)]


# --------------------------------------------------------------------------- the verdict


def check(location: str, description: str, profile: str) -> Optional[Verdict]:
    """None: the posting states no requirement (or contradicts itself), so nothing is said. Verdict(True, note): blocked.
    Verdict(False, gap): it states one, but the profile cannot decide it (the gap line tells the candidate)."""
    needs = requirements(description)
    if not needs:
        return None
    code, rows = job_country(location), profile_rows(profile)
    unknown = None
    for level, quote in needs:
        if code is None:
            why = f'the country of the job is not clear from its location ("{location or "none"}")'
        elif level == CLEARANCE:
            lines = rows.get("clearance") or []
            if len(lines) == 1 and re.match(r"(none|no)\b", lines[0][1].split(":", 1)[1].strip(), re.I):
                return Verdict(True, f'{PREFIX} the posting says "{quote}"; Profile.md says "{lines[0][1][:QUOTE]}" (job country {code}).')
            why = 'Profile.md does not say "Security clearance: none"'
        else:
            mine = candidate_level(rows, code)
            if mine is None:
                why = f"Profile.md does not say whether you meet it for {code} (its Work Authorization section)"
            elif mine[0] < level:
                return Verdict(True, f'{PREFIX} the posting says "{quote}"; Profile.md says "{mine[1][:QUOTE]}" (job country {code}).')
            else:
                continue
        unknown = unknown or f'Work authorization not checked: the posting says "{quote}", but {why}; check it yourself.'
    return Verdict(False, unknown) if unknown else None
