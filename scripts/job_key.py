"""The id that ties a job's tracker row to its folder: the only id rule in the pipeline.

job_key(url) is the number that leads a job's folder name and that the tracker's Job URL reduces to. A LinkedIn
address keeps its job id (a /jobs/view/<digits> on any other site is not one). Any other http(s) address gets "9"
plus 14 digits of a hash of its host, path and query (utm_* tracking parameters left out); a fragment counts only
when it is a hash route (#/job/111), not an anchor or a text fragment. That is 15 digits, so it cannot meet a
LinkedIn id (10 digits).

A posting pasted as text, with no web address of its own, gets one made by pasted_url(): "paste:" plus 14 digits of a
hash of its text. job_key() turns that into "8" plus the 14 digits: 15 digits like a hashed address, but with another
first digit, so it meets neither a hashed address nor a LinkedIn id. The address is what the tracker row and job.md
hold, so reconcile and prep pair the folder with the row by the one rule above.

Do not change the key of an address that is already in use: folders and tracker rows carry it, and reconcile marks
an open row whose folder it cannot pair as Deleted. The pinned vectors in tests/test_job_key.py guard this.
"""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

LINKEDIN_ID_RE = re.compile(r"/jobs/view/(\d+)")
PASTED_RE = re.compile(r"paste:(\d{14})")

# Query parameters (lower case) that change between two scrapes of the same posting without naming it: click ids and
# tracking tags. strip_tracking drops them besides every utm_* parameter, so a posting keeps one address, and so one
# job_key, from run to run. Add a name only when it never identifies a job on any site; when a board's scrape shows
# a volatile parameter that is missing here, add it and scope it by host if another site uses it as an id.
VOLATILE_PARAMS = frozenset({
    "gclid", "gclsrc", "gbraid", "wbraid", "dclid",  # Google Ads
    "fbclid", "igshid", "msclkid", "yclid", "twclid", "ttclid", "li_fat_id",  # other ad networks
    "mc_cid", "mc_eid", "_hsenc", "_hsmi", "mkt_tok", "_ga", "_gl",  # mail and analytics links
    "gh_src", "lever-source", "lever-origin",  # Greenhouse and Lever source tags
})
# Tracking names that only LinkedIn means as tracking (another site may use refId or trk as the id of a posting), so
# strip_tracking drops them on LinkedIn hosts only: "per board".
LINKEDIN_PARAMS = frozenset({"trk", "trkinfo", "trackingid", "refid", "lipi", "midtoken", "midsig", "trkemail"})


def _host(url) -> str:
    try:
        return (urlsplit((url or "").strip()).hostname or "").rstrip(".")  # "linkedin.com." is the same host
    except ValueError:
        return ""


def is_linkedin_url(url) -> bool:
    host = _host(url)
    return host == "linkedin.com" or host.endswith(".linkedin.com")


def strip_tracking(url: str) -> str:
    """The address without its utm_* parameters and the other VOLATILE_PARAMS (freehire appends utm_source=freehire.me),
    plus LINKEDIN_PARAMS on a LinkedIn host.

    The rest of the query is kept as it is written, so the employer's link still works. An address with nothing
    to remove comes back untouched.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    pieces = parts.query.split("&")
    linkedin = is_linkedin_url(url)
    kept = [piece for piece in pieces if not _is_tracking(piece, linkedin)]
    if len(kept) == len(pieces):
        return url
    return parts._replace(query="&".join(kept)).geturl()


def _is_tracking(piece: str, linkedin: bool = False) -> bool:
    name = piece.split("=", 1)[0].lower()
    return name.startswith("utm_") or name in VOLATILE_PARAMS or (linkedin and name in LINKEDIN_PARAMS)


def pasted_url(text) -> str:
    """The address of a posting pasted as text: the same text (case and spacing aside) always gives the same address."""
    canonical = " ".join(str(text or "").casefold().split())
    return "paste:" + f"{int(hashlib.sha1(canonical.encode()).hexdigest()[:12], 16) % 10**14:014d}"


def is_pasted_url(url) -> bool:
    return PASTED_RE.fullmatch((url or "").strip()) is not None


def job_key(url) -> str:
    """The id that ties a tracker row to its folder: the leading digits of the folder name."""
    text = (url or "").strip()
    pasted = PASTED_RE.fullmatch(text)
    if pasted:
        return "8" + pasted.group(1)
    is_http = text.lower().startswith(("http://", "https://"))
    match = LINKEDIN_ID_RE.search(text)
    if match and (not is_http or is_linkedin_url(text)):
        return match.group(1)
    host = _host(text)
    if is_http and host and not is_linkedin_url(text):
        parts = urlsplit(text)
        query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith("utm_"))
        canonical = f"{host}{parts.path.rstrip('/')}?{urlencode(query)}"
        if re.match(r"!?/.", parts.fragment):  # a hash route (#/job/111, #!/job/111), not an anchor or a text fragment
            canonical += f"#{parts.fragment}"
        return "9" + f"{int(hashlib.sha1(canonical.encode()).hexdigest()[:12], 16) % 10**14:014d}"
    return text.rstrip("/").split("?")[0].lower()
