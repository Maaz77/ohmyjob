"""scrape --verbose: progress lines on stderr; without it, logging stays untouched."""
import logging

import pytest

import scrape
from test_scrape_run import frame, post, setup  # noqa: F401  (setup is a fixture)


@pytest.fixture(autouse=True)
def clean_root_logger():
    handlers, level = list(logging.root.handlers), logging.root.level
    yield
    for h in logging.root.handlers[:]:
        if h not in handlers:
            logging.root.removeHandler(h)
            h.close()
    logging.root.setLevel(level)


def test_quiet_run_installs_no_handler(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    before = list(logging.root.handlers)
    assert scrape.run(root, configure(sources=["linkedin"])) == 0
    cap = capsys.readouterr()
    assert logging.root.handlers == before and cap.err == ""
    assert calls[0]["verbose"] == 0
    assert "Added             : 1 new job(s)" in cap.out


def test_verbose_run_logs_stages_to_stderr(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post())
    assert scrape.run(root, configure(sources=["linkedin"]), verbose=True) == 0
    cap = capsys.readouterr()
    assert calls[0]["verbose"] == 2
    assert "INFO | LinkedIn | LINKEDIN_SCRAPE_STARTED | term=Operations Analyst" in cap.err
    assert "INFO | LinkedIn | LINKEDIN_SCRAPE_FINISHED | term=Operations Analyst | status=success | results=1" in cap.err
    assert "INFO | LinkedIn | LINKEDIN_SCRAPE_SUMMARY | terms=1 | results=1 | before filtering" in cap.err
    assert "INFO | LinkedIn | BOARD_START" in cap.err
    assert "SCRAPE_STARTED" not in cap.out and "Added             : 1 new job(s)" in cap.out


def test_dropped_job_names_its_reason(setup, capsys):
    root, boards, calls, configure = setup
    boards["linkedin"] = frame(post(title="Chef de Cuisine", url="https://www.linkedin.com/jobs/view/222"))
    scrape.run(root, configure(sources=["linkedin"]), verbose=True)
    err = capsys.readouterr().err
    assert "INFO | TITLE_FILTERED | title=Chef de Cuisine | company=Acme GmbH | reason=title_does_not_match_search_term" in err
