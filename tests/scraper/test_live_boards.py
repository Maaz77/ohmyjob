"""Asks the real boards. Run by hand with `uv run --frozen pytest -m live tests/scraper`; never in the normal run."""
import jobspy
import pytest

import scrape

pytestmark = pytest.mark.live


@pytest.mark.parametrize("board", ["linkedin", "indeed", "google"])
def test_board_returns_rows_scrape_can_read(board):
    search = {"search_query": ["Data Analyst"], "location": "Berlin, Germany", "country": "germany",
              "recency_days": 3, "results_per_term": 3}
    rows, error = scrape.scrape_board(board, search)
    assert error is None and rows
    assert all(r["url"].startswith("http") and r["title"] for r in rows)
    assert jobspy.__name__ == "jobspy"
