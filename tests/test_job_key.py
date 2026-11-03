"""job_key: one id rule for folders, tracker rows and reconcile.

Folders and tracker rows already written carry these numbers: change an expected key only by changing the rule
on purpose, and then migrate the folders and rows with it.
"""
import re

import pytest

from job_key import VOLATILE_PARAMS, is_linkedin_url, job_key, strip_tracking

# (address, key)
VECTORS = [
    ("https://www.linkedin.com/jobs/view/4399000001", "4399000001"),
    ("https://www.linkedin.com/jobs/view/4399000001/?trk=abc", "4399000001"),
    ("https://job-boards.greenhouse.io/lumenfield/jobs/5500000001?utm_source=freehire.me", "962385302647144"),
    ("https://job-boards.greenhouse.io/lumenfield/jobs/5500000001", "962385302647144"),
    ("http://Job-Boards.Greenhouse.io/lumenfield/jobs/5500000001/?utm_medium=api", "962385302647144"),
    ("https://boards.example.com/jobs?gh_jid=111", "981533705460833"),
    ("https://boards.example.com/jobs?gh_jid=222", "939333802241954"),
    ("https://www.linkedin.com/jobs/view/shared-1", "https://www.linkedin.com/jobs/view/shared-1"),
    ("http://[bad", "http://[bad"),
    ("", ""),
    (None, ""),
    # a /jobs/view/<digits> on another site is not a LinkedIn id, even when the host name starts with linkedin.com
    ("https://careers.harborline.example/jobs/view/4012345678", "911609198600877"),
    ("https://linkedin.com.evil.example/jobs/view/4012345678", "944453070388713"),
    ("https://www.google.com/url?q=https://www.linkedin.com/jobs/view/4399000001", "942286506445931"),
    # LinkedIn text that is not an http(s) address keeps the old rule
    ("www.linkedin.com/jobs/view/123", "123"),
    ("[x](https://www.linkedin.com/jobs/view/123)", "123"),
    ("/jobs/view/55/", "55"),
    # a fragment counts only as a route; the path and query keep their case; the port is ignored
    ("https://jobs.harborline.example/#/job/111", "993021893835671"),
    ("https://jobs.harborline.example/#/job/222", "924915189238773"),
    ("https://jobs.harborline.example/#apply", "945138553016489"),
    ("https://jobs.harborline.example/", "945138553016489"),
    ("https://x.example/Job/AbC", "958926814763608"),
    ("https://x.example/job/abc", "992459561708697"),
    ("https://x.example:8443/jobs/1", "909996467427345"),
    ("https://x.example/jobs/1", "909996467427345"),
    ("https://x.example/jobs?a=1;b=2", "960489066544832"),
    # a raw and a percent-encoded spelling of the same path are two keys (the twin check catches the repeat)
    ("https://x.example/jobs/ingénieur", "994551334338863"),
    ("https://x.example/jobs/ing%C3%A9nieur", "972409566531035"),
    # a host with a trailing dot is the same host
    ("https://www.linkedin.com./jobs/view/123", "123"),
    ("https://x.example./jobs/1", "909996467427345"),
    # only a hash route counts as a fragment: not an anchor, not a text fragment, not a lone "/"
    ("https://acme.example/jobs/1", "965929327310180"),
    ("https://acme.example/jobs/1#:~:text=a/b", "965929327310180"),
    ("https://acme.example/", "936409385765802"),
    ("https://acme.example/#/", "936409385765802"),
    ("https://acme.example/jobs/1#/job/9", "930741171221095"),
    ("https://acme.example/jobs/1#!/job/9", "923043738546844"),
    # another job board
    ("https://de.indeed.com/viewjob?jk=4be1a09c37d5f268", "969845171025937"),
]

OTHER_SITE_URLS = [
    "https://careers.harborline.example/jobs/view/4012345678",
    "https://linkedin.com.evil.example/jobs/view/4012345678",
    "https://other.example/jobs/view/12",
    "https://jobs.harborline.example/#/job/111",
    "https://www.google.com/url?q=https://www.linkedin.com/jobs/view/4399000001",
    "https://boards.example.com/jobs?gh_jid=111",
    "https://de.indeed.com/viewjob?jk=4be1a09c37d5f268",
]

IS_LINKEDIN = [
    ("https://www.linkedin.com/jobs/view/1", True),
    ("https://www.linkedin.com./jobs/view/1", True),
    ("https://linkedin.com/jobs/view/1", True),
    ("https://uk.linkedin.com/jobs/view/1", True),
    ("https://notlinkedin.com/jobs/view/1", False),
    ("https://example.com/linkedin.com", False),
    ("http://[bad", False),
    ("", False),
    (None, False),
]


@pytest.mark.parametrize("url, key", VECTORS)
def test_pinned_key(url, key):
    assert job_key(url) == key


@pytest.mark.parametrize("url", OTHER_SITE_URLS)
def test_key_of_another_site_is_15_digits_starting_with_9(url):
    assert re.fullmatch(r"9\d{14}", job_key(url))


def test_two_sites_with_the_same_number_in_their_path_stay_apart():
    assert job_key("https://one.example/jobs/view/12") != job_key("https://two.example/jobs/view/12")


def test_query_order_scheme_case_and_utm_do_not_change_the_key():
    same = ("https://boards.example.com/jobs?a=1&b=2", "http://BOARDS.example.com/jobs/?b=2&a=1&utm_source=x&UTM_medium=y")
    assert job_key(same[0]) == job_key(same[1])
    assert job_key(same[0]) != job_key("https://boards.example.com/jobs?a=1&b=3")


@pytest.mark.parametrize("url, expected", IS_LINKEDIN)
def test_is_linkedin_url(url, expected):
    assert is_linkedin_url(url) is expected


def test_fixture_folders_carry_the_key_of_their_job_url(workspace):
    folders = sorted((workspace / "Applications").iterdir())
    assert len(folders) == 2
    for folder in folders:
        text = (folder / "job.md").read_text(encoding="utf-8")
        url = re.search(r"^- (?:\*\*)?Job URL:(?:\*\*)? (\S+)$", text, re.M).group(1)
        assert folder.name.split("_")[0] == job_key(url)


# --------------------------------------------------------------------------- strip_tracking


def test_strip_tracking_drops_utm_and_keeps_the_rest_as_written():
    url = "https://jobs.example.org/p/9?utm_source=freehire.me&Dept=Ops&utm_medium=api&lang=de#top"
    assert strip_tracking(url) == "https://jobs.example.org/p/9?Dept=Ops&lang=de#top"


def test_strip_tracking_removes_the_question_mark_when_nothing_is_left():
    assert strip_tracking("https://jobs.example.org/p/9?utm_source=x&UTM_campaign=y") == "https://jobs.example.org/p/9"


@pytest.mark.parametrize("param", sorted(VOLATILE_PARAMS))
def test_strip_tracking_drops_every_volatile_param(param):
    assert strip_tracking(f"https://jobs.example.org/p/9?id=7&{param.upper()}=abc") == "https://jobs.example.org/p/9?id=7"


@pytest.mark.parametrize("url", [
    "https://jobs.example.org/p/9",
    "https://jobs.example.org/p/9?id=7&ref=abc",
    "https://www.linkedin.com/jobs/view/4399000001",
    "http://[bad",
    "",
])
def test_strip_tracking_returns_an_address_with_nothing_to_remove_untouched(url):
    assert strip_tracking(url) is url


def test_the_same_posting_keeps_one_key_across_runs():
    run_one = "https://jobs.example.org/p/9?id=7&gclid=AAA&utm_source=google_jobs_apply"
    run_two = "https://jobs.example.org/p/9?id=7&gclid=BBB&utm_source=google_jobs_apply&fbclid=CCC"
    assert strip_tracking(run_one) == strip_tracking(run_two)
    assert job_key(strip_tracking(run_one)) == job_key(strip_tracking(run_two))
    assert job_key(strip_tracking(run_one)) != job_key("https://jobs.example.org/p/9?id=8")
