"""freehire_source with the one network call (_fetch / urlopen) replaced. Rewritten from the first scraper's tests."""
import http.client
import io
import re
import urllib.error

import pytest

import freehire_source as mod
import workspace as W
from job_key import job_key


def item(**overrides):
    base = {
        "public_slug": "data-analyst-acme-1a2b",
        "source": "greenhouse",
        "url": "https://job-boards.greenhouse.io/acme/jobs/123?utm_source=freehire.me",
        "title": "Operations Analyst",
        "company": "Acme",
        "location": "Leeds, UK",
        "description": "## About\nKeep the network running.",
        "collections": [],
        "posted_at": "2026-10-27T00:00:00Z",
        "enrichment": {"employment_type": "full_time"},
    }
    base.update(overrides)
    return base


def page(items, total=None):
    return {"data": items, "meta": {"total": len(items) if total is None else total, "limit": 100, "offset": 0}}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)


@pytest.fixture
def api(monkeypatch):
    """Replace the one network call. api.respond(fn) sets fn(params) -> response; api.calls lists the params of the
    searches, api.probes those of the one-result probes that explain an empty search (limit=1)."""

    class Api:
        calls = []
        probes = []
        responder = staticmethod(lambda params: page([]))

        def respond(self, fn):
            self.responder = staticmethod(fn)

    fake = Api()
    fake.calls = []
    fake.probes = []

    def fetch(params, probe=False):
        (fake.probes if params.get("limit") == 1 else fake.calls).append(dict(params))
        return fake.responder(params)

    monkeypatch.setattr(mod, "_fetch", fetch)
    return fake


def search(terms, location="France", **kwargs):
    return mod.search_freehire_jobs(terms, location, **kwargs)


# --------------------------------------------------------------------------- country


@pytest.mark.parametrize("location,code", [
    ("United Kingdom", "GB"), ("UK", "GB"), ("France", "FR"), ("Ireland", "IE"), ("Dublin, Ireland", "IE"),
    ("Lyon, Auvergne-Rhone-Alpes, France", "FR"), ("Germany", "DE"), ("  france  ", "FR"),
    ("England", "GB"), ("London, England", "GB"), ("Edinburgh, Scotland", "GB"), ("Belfast, Northern Ireland", "GB"),
    ("Great Britain", "GB"), ("Slovenia", "SI"), ("USA", "US"), ("Türkiye", "TR"), ("Czechia", "CZ"),
])
def test_country_named_in_the_location(location, code):
    assert mod.freehire_country(location) == code


@pytest.mark.parametrize("location", ["Atlantis", "London", "Worldwide", "", None])
def test_no_country(location):
    assert mod.freehire_country(location) is None


def test_the_config_country_is_the_fallback():
    assert mod.freehire_country("Lyon", "france") == "FR"
    assert mod.freehire_country("Lyon, Spain", "france") == "ES"  # the location wins
    assert mod.freehire_country("Lyon", "") is None


def test_every_country_the_config_accepts_has_a_code():
    for name in W.COUNTRIES:
        assert mod.COUNTRY_CODES[name], name
    for alias, name in W.COUNTRY_ALIASES.items():
        assert mod.COUNTRY_CODES[alias] == mod.COUNTRY_CODES[name], alias
    assert all(re.fullmatch(r"[A-Z]{2}", code) for code in mod.COUNTRY_CODES.values())


# --------------------------------------------------------------------------- one result -> one row


def test_maps_a_result_to_a_row():
    row = mod._row(item())
    assert row == {
        "title": "Operations Analyst", "company": "Acme", "url": "https://job-boards.greenhouse.io/acme/jobs/123",
        "apply_url": "", "source": "freehire", "location": "Leeds, UK", "posted": "2026-10-27",
        "job_type": "fulltime", "job_level": "", "description": "#### About\nKeep the network running.",
        "listed_via": "greenhouse", "note": "Source: freehire/greenhouse",
    }
    assert job_key(row["url"]).isdigit() and len(job_key(row["url"])) == 15


def test_the_seniority_label_becomes_the_job_level():
    # the first scraper returned before it read the label, so its seniority filter never ran
    assert mod._row(item(enrichment={"employment_type": "full_time", "seniority": " Principal "}))["job_level"] == "principal"
    for odd in (None, "", 5, ["senior"], {"level": "senior"}):
        assert mod._row(item(enrichment={"seniority": odd}))["job_level"] == ""


def test_html_entities_are_decoded():
    row = mod._row(item(company="Harnham &amp; Co", title="Ops &amp; Data Analyst", location="Reading &amp; Slough"))
    assert (row["company"], row["title"], row["location"]) == ("Harnham & Co", "Ops & Data Analyst", "Reading & Slough")


def test_a_sponsor_licensed_employer_is_noted_in_a_uk_search_only():
    assert mod._row(item(collections=["yc", "uk-skilled-worker-sponsor"]), "GB")["note"] == \
        "Source: freehire/greenhouse; UK licensed sponsor"
    assert mod._row(item(collections=["uk-skilled-worker-sponsor"]), "FR")["note"] == "Source: freehire/greenhouse"


def test_markdown_headings_in_the_description_go_down_two_levels():
    row = mod._row(item(description="# Title\n\n## About\nBuild.\n### Goals\n- a\n#### Deep\n##### Deeper\n#hashtag\n####### x"))
    assert row["description"] == "### Title\n\n#### About\nBuild.\n##### Goals\n- a\n###### Deep\n##### Deeper\n#hashtag\n####### x"


def test_headings_after_other_line_breaks_are_demoted_too():
    row = mod._row(item(description="Intro\r## About\rText\x0b## More\x85# Top\r\n## Last"))
    assert row["description"].splitlines() == ["Intro", "#### About", "Text", "#### More", "### Top", "#### Last"]


@pytest.mark.parametrize("employment,job_type", [("full_time", "fulltime"), ("part_time", "parttime"), ("contract", "contract")])
def test_employment_types_map_to_jobspy_names(employment, job_type):
    assert mod._row(item(enrichment={"employment_type": employment}))["job_type"] == job_type


def test_a_missing_employment_type_is_kept_without_a_type():
    for enrichment in ({}, None, {"employment_type": None}):
        row = mod._row(item(enrichment=enrichment))
        assert row is not None and row["job_type"] == ""


@pytest.mark.parametrize("employment", ["internship", "fellowship"])
def test_unwanted_employment_types_are_dropped(employment):
    assert mod._row(item(enrichment={"employment_type": employment})) is None


@pytest.mark.parametrize("overrides", [
    {"company": ""}, {"company": None}, {"title": " "}, {"url": ""},
    {"url": "https://www.linkedin.com/jobs/view/not-a-number"},
])
def test_a_result_that_cannot_be_tracked_is_dropped(overrides):
    assert mod._row(item(**overrides)) is None


@pytest.mark.parametrize("source", ["whatjobs-uk", "whatjobs-fr", "whatjobs", "adzuna", "jobleads"])
def test_aggregator_sources_are_dropped_even_if_the_api_sends_them(source):
    assert mod._row(item(source=source)) is None


@pytest.mark.parametrize("source", ["reed", "himalayas", "arbeitnow", "greenhouse", "workday"])
def test_other_sources_are_kept(source):
    assert mod._row(item(source=source)) is not None


def test_a_linkedin_address_with_a_job_id_keeps_that_id():
    assert job_key(mod._row(item(url="https://www.linkedin.com/jobs/view/4426974960"))["url"]) == "4426974960"


# --------------------------------------------------------------------------- the request


def test_uk_sponsor_only_is_off_by_default_and_asks_for_licensed_sponsors_when_on(api):
    search(["Data Analyst"], "United Kingdom")
    assert "collections" not in api.calls[0]
    search(["Data Analyst"], "United Kingdom", uk_sponsor_only=True)
    assert api.calls[1] == {
        "q": 'title:"Data Analyst"', "countries": "GB", "source_exclude": "adzuna,jobleads,whatjobs,whatjobs-uk",
        "description_format": "markdown", "sort": "created_at", "order": "desc", "limit": 100, "offset": 0,
        "open_within_days": 1, "collections": "uk-skilled-worker-sponsor",
    }


def test_the_sponsor_filter_is_for_the_uk_only(api):
    search(["Data Analyst"], "France", uk_sponsor_only=True)
    assert "collections" not in api.calls[0]
    assert api.calls[0]["countries"] == "FR"
    assert api.calls[0]["source_exclude"] == "adzuna,jobleads,whatjobs,whatjobs-fr"


def test_days_and_zero_days(api):
    search(["A"], open_within_days=0)
    search(["A"], open_within_days=7)
    assert "open_within_days" not in api.calls[0] and api.calls[1]["open_within_days"] == 7


def test_one_search_per_term_and_quotes_cannot_break_the_query(api):
    search(["B", "A", 'C "D"'])
    assert sorted(call["q"] for call in api.calls) == ['title:"A"', 'title:"B"', 'title:"C  D"']


# --------------------------------------------------------------------------- search


def test_returns_rows_and_counts_what_it_read(api):
    api.respond(lambda params: page([item(), item(url="https://jobs.ashbyhq.com/acme/9f", title="Data Analyst"),
                                     item(company="", url="https://jobs.example.com/2")]))
    found = search(["Data Analyst"])
    assert [r["title"] for r in found.rows] == ["Operations Analyst", "Data Analyst"]
    assert found.fetched == 3 and found.error is None and found.warnings == []


def test_a_job_found_by_two_terms_is_kept_once(api):
    api.respond(lambda params: page([item()]))
    assert len(search(["A", "B"]).rows) == 1


def test_every_page_is_read(api):
    def respond(params):
        start = params["offset"]
        return page([item(url=f"https://jobs.example.com/{start + n}") for n in range(min(100, 250 - start))], total=250)

    api.respond(respond)
    found = search(["A"])
    assert [c["offset"] for c in api.calls] == [0, 100, 200] and len(found.rows) == 250


def test_stops_at_the_api_limit_and_says_so(api):
    api.respond(lambda p: page([item(url=f"https://jobs.example.com/{p['offset'] + n}") for n in range(100)], total=25_000))
    found = search(["A"])
    assert api.calls[-1]["offset"] == 9_900 and len(found.rows) == 10_000
    assert any("the API stops at 10000" in w for w in found.warnings)


def test_an_unknown_location_skips_freehire_without_a_request(api):
    found = search(["A"], "Atlantis")
    assert found.rows == [] and api.calls == [] and found.error is None
    assert "no country found" in found.warnings[0]


def test_an_answer_without_data_is_an_error_not_a_quiet_day(api):
    api.respond(lambda params: {"results": [item()], "meta": {"total": 1}})
    found = search(["A", "B"])
    assert found.rows == [] and "no 'data' in the answer" in found.error
    assert found.error.count("freehire failed") == 2


def test_one_odd_result_does_not_cost_the_rest_of_the_term(api):
    good = [item(url=f"https://jobs.example.com/{n}") for n in range(3)]
    odd = [item(posted_at=1730000000), item(enrichment={"employment_type": ["full_time"]}), "not a dict"]
    api.respond(lambda params: page([good[0], *odd, good[1], good[2]]))
    found = search(["A"])
    assert len(found.rows) == 3 and found.error is None
    assert sum("could not be read" in w for w in found.warnings) == 3


def test_a_failing_term_is_reported_and_the_others_still_run(api):
    def respond(params):
        if params["q"] == 'title:"B"':
            raise urllib.error.URLError("boom")
        return page([item(url="https://jobs.example.com/" + params["q"].split('"')[1])])

    api.respond(respond)
    found = search(["A", "B", "C"])
    assert len(found.rows) == 2 and "term='B'" in found.error and "term='A'" not in found.error


def test_rows_read_before_a_later_page_fails_are_kept(api):
    def respond(params):
        if params["offset"] == 0:
            return page([item(url=f"https://jobs.example.com/{n}") for n in range(100)], total=250)
        raise ValueError("bad json")

    api.respond(respond)
    found = search(["A"])
    assert len(found.rows) == 100 and "after 100 results" in found.error


def test_nothing_raises(api):
    def respond(params):
        raise RuntimeError("boom")

    api.respond(respond)
    assert search(["A"]).rows == []


# --------------------------------------------------------------------------- _fetch


class FakeResponse(io.BytesIO):
    pass


def http_error(code, retry_after=None):
    headers = {"Retry-After": retry_after} if retry_after else {}
    return urllib.error.HTTPError("https://freehire.me/x", code, "err", headers, None)


def urlopen_giving(outcomes):
    def urlopen(request, timeout):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return urlopen


def test_a_probe_waits_ten_seconds_and_is_not_retried(monkeypatch):
    timeouts, waits = [], []

    def urlopen(request, timeout):
        timeouts.append(timeout)
        raise urllib.error.URLError("hangs")

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(mod.time, "sleep", waits.append)
    with pytest.raises(urllib.error.URLError):
        mod._fetch({"q": "x"}, probe=True)
    assert timeouts == [10] and waits == []


def test_a_search_waits_thirty_seconds_and_is_retried_once(monkeypatch):
    timeouts = []

    def urlopen(request, timeout):
        timeouts.append(timeout)
        raise urllib.error.URLError("hangs")

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.URLError):
        mod._fetch({"q": "x"})
    assert timeouts == [30, 30]


def test_returns_json_sends_the_params_and_identifies_itself_as_ohmyjob(monkeypatch):
    seen = {}

    def urlopen(request, timeout):
        seen["url"], seen["agent"] = request.full_url, request.get_header("User-agent")
        return FakeResponse(b'{"data": []}')

    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen)
    assert mod._fetch({"q": 'title:"Data Analyst"', "limit": 100}) == {"data": []}
    assert seen["url"].startswith(mod.API_URL + "?q=title%3A%22Data+Analyst%22&limit=100")
    assert seen["agent"] == mod.USER_AGENT == "ohmyjob"


def test_one_retry_after_a_429_waits_for_retry_after_capped(monkeypatch):
    waits = []
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen_giving([http_error(429, "7"), FakeResponse(b'{"ok": 1}')]))
    monkeypatch.setattr(mod.time, "sleep", waits.append)
    assert mod._fetch({}) == {"ok": 1} and waits == [7.0]
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen_giving([http_error(503, "999"), FakeResponse(b"{}")]))
    mod._fetch({})
    assert waits[-1] == mod.MAX_RETRY_WAIT_SECONDS


@pytest.mark.parametrize("retry_after", ["-5", "nan", "soon", ""])
def test_a_useless_retry_after_falls_back_to_the_default_wait(monkeypatch, retry_after):
    waits = []
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen_giving([http_error(429, retry_after or None), FakeResponse(b"{}")]))
    monkeypatch.setattr(mod.time, "sleep", waits.append)
    mod._fetch({})
    assert waits == [mod.RETRY_WAIT_SECONDS]


def test_gives_up_after_one_retry_and_a_400_is_not_retried(monkeypatch):
    calls = []

    def always(code):
        def urlopen(request, timeout):
            calls.append(code)
            raise http_error(code)

        return urlopen

    monkeypatch.setattr(mod.urllib.request, "urlopen", always(500))
    with pytest.raises(urllib.error.HTTPError):
        mod._fetch({})
    monkeypatch.setattr(mod.urllib.request, "urlopen", always(400))
    with pytest.raises(urllib.error.HTTPError):
        mod._fetch({})
    assert calls == [500, 500, 400]


@pytest.mark.parametrize("error", [
    urllib.error.URLError("down"), http.client.RemoteDisconnected("closed"), ConnectionResetError("reset"),
    http.client.IncompleteRead(b"par"), TimeoutError("slow"),
])
def test_a_network_error_or_dropped_connection_is_retried_once_then_raised(monkeypatch, error):
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen_giving([error, FakeResponse(b'{"ok": 3}')]))
    assert mod._fetch({}) == {"ok": 3}
    monkeypatch.setattr(mod.urllib.request, "urlopen", urlopen_giving([error, error]))
    with pytest.raises(type(error)):
        mod._fetch({})


# --------------------------------------------------------------------------- ignored settings


def test_names_from_both_ignored_lists():
    meta = {"ignored_params": [{"param": "reality"}, {"param": "seniorty", "did_you_mean": "seniority"}],
            "ignored_values": {"seniority": ["wizard", "x"], "countries": ["zz"]}}
    assert mod._ignored_names(meta) == ["reality", "seniorty (did you mean seniority?)", "seniority=wizard",
                                        "seniority=x", "countries=zz"]


@pytest.mark.parametrize("meta", [None, [], "x", {}, {"ignored_params": None, "ignored_values": None},
                                  {"ignored_params": ["reality", 3, {}]}, {"ignored_values": ["seniority"]},
                                  {"ignored_values": {"seniority": None}}, {"ignored_params": 5},
                                  {"ignored_params": True}, {"ignored_params": "reality"},
                                  {"ignored_params": {"param": "x"}}, {"ignored_values": 7}])
def test_an_odd_ignored_shape_never_raises_and_costs_no_result(api, meta):
    assert isinstance(mod._ignored_names(meta), list)
    api.respond(lambda params: {"data": [item()], "meta": {"total": 1, **(meta if isinstance(meta, dict) else {})}})
    assert len(search(["A"]).rows) == 1


def test_an_ignored_setting_is_reported_once_per_name(api):
    meta = {"total": 1, "ignored_params": [{"param": "reality"}], "ignored_values": {"seniority": ["wizard"]}}
    api.respond(lambda params: {"data": [item()], "meta": meta})
    found = search(["A", "B"])
    assert len(found.rows) == 1 and len([w for w in found.warnings if "did not use" in w]) == 1
    assert "reality; seniority=wizard" in found.warnings[0]


# --------------------------------------------------------------------------- why zero


def probe_totals(**totals):
    def respond(params):
        if params["limit"] != 1:
            return page([])
        anyone = params["countries"] == "GB" and "collections" not in params
        window = f"d{params['open_within_days']}" if "open_within_days" in params else "all"
        return {"data": [], "meta": {"total": totals[("any_" if anyone else "") + window]}}

    return respond


def test_a_uk_sponsor_search_with_nothing_says_what_a_wider_window_and_any_employer_would_find(api):
    api.respond(probe_totals(d7=3, d30=222, all=714, any_d1=199))
    found = search(["Data Analyst"], "United Kingdom", uk_sponsor_only=True)
    assert found.rows == []
    assert ("term='Data Analyst': nothing found | window=1d | sponsors_only: 7d=3 30d=222 all=714 | any_employer: 1d=199"
            in found.warnings)
    assert sorted((p.get("open_within_days") or 0, "collections" in p) for p in api.probes) == \
        [(0, True), (1, False), (7, True), (30, True)]


def test_without_the_sponsor_filter_there_is_nothing_to_relax(api):
    api.respond(lambda p: page([]) if p["limit"] != 1 else {"data": [], "meta": {"total": 5 if "open_within_days" in p else 9}})
    for where in ("United Kingdom", "France"):
        found = search(["A"], where)
        assert "window=1d | 7d=5 30d=5 all=9" in found.warnings[-1]
    assert all("collections" not in p for p in api.probes)


def test_windows_and_failing_probes(api):
    api.respond(probe_totals(all=40, any_d30=7))
    assert "window=30d | sponsors_only: all=40 | any_employer: 30d=7" in \
        search(["A"], "United Kingdom", open_within_days=30, uk_sponsor_only=True).warnings[-1]
    api.respond(probe_totals(any_all=17))
    assert "window=all | sponsors_only: nothing wider to try | any_employer: all=17" in \
        search(["A"], "United Kingdom", open_within_days=0, uk_sponsor_only=True).warnings[-1]

    def hangs(params):
        if params["limit"] != 1:
            return page([])
        raise urllib.error.URLError("hangs")

    api.probes.clear()
    api.respond(hangs)
    found = search(["A", "B", "C"], "United Kingdom", uk_sponsor_only=True)
    assert len(api.probes) == 1 and len(found.warnings) == 1  # after one failed probe, no more
    assert "7d=? 30d=? all=? | any_employer: 1d=?" in found.warnings[0]


def test_a_failed_search_is_not_explained_as_an_empty_one(api):
    def respond(params):
        raise urllib.error.URLError("down")

    api.respond(respond)
    found = search(["A"])
    assert found.error and found.warnings == [] and api.probes == []


def test_a_search_that_found_something_probes_nothing(api):
    api.respond(lambda params: page([item()]))
    assert search(["A"]).warnings == [] and api.probes == []
