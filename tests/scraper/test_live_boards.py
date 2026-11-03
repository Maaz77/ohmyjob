"""Asks the real boards. Run by hand with `uv run --frozen pytest -m live tests/scraper`; never in the normal run."""
import pytest

import scrape
from job_key import job_key
from workspace import JOBSPY_SOURCES

pytestmark = pytest.mark.live

SEARCH = {"search_query": ["Data Analyst"], "location": "Berlin, Germany", "country": "germany",
          "recency_days": 3, "results_per_term": 3}


@pytest.mark.parametrize("board", JOBSPY_SOURCES)
def test_board_returns_rows_scrape_can_read(board):
    rows, error, _warnings = scrape.scrape_board(board, SEARCH)
    assert error is None and rows
    assert all(r["url"].startswith("http") and r["title"] for r in rows)


@pytest.mark.parametrize("board", JOBSPY_SOURCES)
def test_rescrape_keeps_the_keys(board):
    first, _e, _w = scrape.scrape_board(board, SEARCH)
    second, _e, _w = scrape.scrape_board(board, SEARCH)
    keys = {job_key(r["url"]) for r in first}
    assert keys and keys & {job_key(r["url"]) for r in second}
