"""Language requirement filter: a cheap scrape-time drop (freehire only) and the screener's dealbreaker prefix.

A freehire posting is dropped ONLY when it carries a language label, the `# Languages` section of Profile.md exists,
and the label is not one of the languages that section names. Everything else is "do not drop".

Choices, and why:
- The candidate's side is one structured section of Profile.md, `- <Language>: <own words>`. There is no ohmyjob.json
  key: a config list of codes would be a second source that drifts from the profile.
- There is NO proficiency threshold. A language named in the section passes every filter, whatever level the line claims
  and whatever level the posting asks for: levels are self-assessed and postings overstate them, so a level test would
  block jobs the candidate could do. Level words are informational only.
- Unknown never drops: about a quarter of freehire rows have no label, and JobSpy boards have none, so an empty code is kept.
  The label is the language the ad is WRITTEN in, which is not always the language it requires, so it is only a cheap net;
  the screener reads the description for the real requirement.
- Unlike `# Work Authorization` (a missing line means unknown), this section is read as COMPLETE: a language that is not
  listed is one the candidate does not have, so the candidate must list every language, half-spoken ones included.
- Fail open: the user's rule is that a language the candidate names always survives. A line whose name the map does not
  know (`- Farsi: native.`) could be the very language of a posting, so filtering on the partial set would reject it. Any
  such line therefore turns the whole filter off (profile_languages reports it, scrape.run warns). A known name matches as
  a whole word inside the name part ("Mandarin Chinese", "English (C1)"); a bare code only as the entire name part.
- freehire labels Norwegian `nb`, not `no` (a probe of the live API), so Norwegian maps to `nb` and `no`/`nn` are aliases.
- No section gives an empty set and the feature stays off.
"""
from __future__ import annotations

import re

PREFIX = "Language block:"  # starts the Notes entry of a job the screener blocked on language: the skips can be counted by it
SECTION = "Languages"
CODES = {
    "en": "english", "de": "german deutsch", "fr": "french français francais", "es": "spanish español espanol castellano",
    "it": "italian italiano", "nl": "dutch nederlands flemish", "pt": "portuguese português portugues", "pl": "polish polski",
    "sv": "swedish", "nb": "norwegian norsk bokmål bokmal nynorsk", "da": "danish", "fi": "finnish", "cs": "czech",
    "sk": "slovak", "hu": "hungarian", "ro": "romanian", "bg": "bulgarian", "el": "greek", "tr": "turkish", "ru": "russian",
    "uk": "ukrainian", "ar": "arabic", "he": "hebrew", "hi": "hindi", "zh": "chinese mandarin cantonese", "ja": "japanese",
    "ko": "korean", "vi": "vietnamese", "th": "thai", "id": "indonesian", "ca": "catalan", "hr": "croatian", "sr": "serbian",
    "sl": "slovenian", "et": "estonian", "lv": "latvian", "lt": "lithuanian", "ga": "irish", "cy": "welsh",
    "fa": "persian farsi", "ur": "urdu", "bn": "bengali", "ta": "tamil", "pa": "punjabi", "ms": "malay", "tl": "tagalog filipino",
    "sw": "swahili", "sq": "albanian", "mk": "macedonian", "bs": "bosnian", "is": "icelandic", "lb": "luxembourgish",
    "eu": "basque", "gl": "galician", "af": "afrikaans", "mt": "maltese",
}
NAMES = {name: code for code, names in CODES.items() for name in names.split()}
ALIASES = {"no": "nb", "nn": "nb"}  # freehire labels Norwegian "nb"
LINE_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*:\s*(.+?)\s*$")


def profile_languages(profile: str) -> tuple[set[str], list[str]]:
    """(ISO 639-1 codes the `# Languages` section names, the name part of every line that names no known language).
    No section: (empty, empty). A non-empty second list means the caller must not filter on the codes."""
    spoken: set[str] = set()
    unresolved: list[str] = []
    inside = False
    for line in profile.splitlines():
        if line.startswith("# "):
            inside = line[2:].strip().casefold() == SECTION.casefold()
            continue
        match = LINE_RE.match(line.replace("**", "")) if inside else None
        if not match:
            continue
        name = match.group(1).strip()
        whole = name.casefold()
        found = {NAMES[w] for w in re.findall(r"[^\W\d_]+", whole) if w in NAMES}
        if not found:  # a bare code counts only as the entire name part ("it", "no", "id" are ordinary words elsewhere)
            code = ALIASES.get(whole, whole)
            found = {code} if code in CODES else set()
        spoken |= found
        if not found:
            unresolved.append(name)
    return spoken, unresolved


def ok(posting_language: str, spoken: set[str]) -> bool:
    """True (keep the job) when the label is unknown, the feature is off (no languages), or the label is a spoken language."""
    code = re.split(r"[-_]", (posting_language or "").casefold().strip())[0]
    return not code or not spoken or code in spoken
