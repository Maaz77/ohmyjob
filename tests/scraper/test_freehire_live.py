"""Checks against the real freehire API: `uv run --frozen pytest -m live tests/scraper`. Skipped in the normal run, which stays offline.

freehire's API is keyless, public and not ours: it can change under us. The unit tests use made-up answers, so only
these show that the fields the adapter relies on are still there. Each test names what it protects; a failure here
means scripts/freehire_source.py needs a look, not that the network is flaky (a network error skips).
"""

import http.client
import urllib.error

import pytest

import freehire_source as fh

pytestmark = pytest.mark.live


def reachable(call, *args, **kwargs):
    """call(...), but a network trouble (no connection, a timeout, a dropped answer) skips the test instead of failing it.
    An HTTP answer (a 404, a 500) is freehire talking: it still raises, and the tests that expect one say so."""
    try:
        return call(*args, **kwargs)
    except urllib.error.HTTPError:
        raise
    except (OSError, http.client.HTTPException) as error:
        pytest.skip(f"freehire not reachable: {error}")


def search(term="engineer", country="GB", days=0, limit=5):
    """The adapter's own request for a term, with a smaller page."""
    params = fh._params(term, country, days, 0, sponsors_only=country == "GB")
    params["limit"] = limit
    return reachable(fh._fetch, params)


@pytest.fixture(scope="module")
def uk_page():
    return search()


@pytest.fixture(scope="module")
def uk_item(uk_page):
    if not uk_page["data"]:
        pytest.skip("no open UK sponsor job for 'engineer' right now")
    return uk_page["data"][0]


def test_the_adapters_own_request_is_understood_in_full(uk_page):
    """Protects every setting _params sends (sponsor collection, source_exclude, sort, ...) is still known."""
    assert isinstance(uk_page["meta"]["total"], int)
    assert "ignored_params" not in uk_page["meta"] and "ignored_values" not in uk_page["meta"]


@pytest.mark.parametrize("country", ["GB", "FR", "IE", "DE"])
@pytest.mark.parametrize("days", [2, 30, 0])
def test_every_request_shape_the_adapter_can_send_is_understood(country, days):
    """Protects the per-country WhatJobs exclusion name across every window the adapter can send."""
    meta = search(country=country, days=days, limit=1)["meta"]

    assert "ignored_params" not in meta and "ignored_values" not in meta


def test_an_unknown_setting_is_still_reported_not_refused():
    """Protects the warning we read: it exists. (If this fails, an ignored filter can never be reported.)"""
    params = fh._params("engineer", "GB", 0, 0, sponsors_only=True)
    params.update(limit=1, bogus_setting="x")

    meta = reachable(fh._fetch, params)["meta"]

    assert meta["ignored_params"] == [{"param": "bogus_setting"}]


def test_a_result_carries_the_fields_the_adapter_reads(uk_item):
    """Protects _row and the seniority check."""
    for field in ("url", "title", "company", "source", "description", "collections", "posted_at", "enrichment"):
        assert field in uk_item, field
    assert isinstance(uk_item["collections"], list) and isinstance(uk_item["enrichment"], dict)


def test_the_seniority_labels_in_the_data_are_the_ones_we_know():
    """Protects exclude_seniority_level: a renamed label ("middle" to "mid") would make a configured exclusion drop nothing."""
    seen = {
        (item.get("enrichment") or {}).get("seniority")
        for item in search(limit=100)["data"] + search(country="FR", limit=100)["data"]
    } - {None}

    assert seen <= set(fh.SENIORITY_LABELS), sorted(seen - set(fh.SENIORITY_LABELS))


