"""The first scraper patched three things in JobSpy 1.1.82. JobSpy 1.4.1 (pinned) needs none of them: each test shows it.

1. A very recent LinkedIn card renders its date as <time class="job-search-card__listdate--new">, which 1.1.82 missed
   (date_posted came back None). 1.4.1 reads the first <time> whatever its class.
2. 1.1.82 swallowed a failed detail-page request without a trace. 1.4.1 logs a warning and returns {} (also for the
   signup wall), so no patch is needed to see a throttled run.
3. The search term tag on JobSpy's progress log lines was cosmetic (a log format of the old app); this script runs
   JobSpy with verbose=0 and prints its own summary, so it is not kept.
"""
import logging

from bs4 import BeautifulSoup
from jobspy.linkedin import LinkedIn
from jobspy.model import ScraperInput


def card(time_class):
    html = f"""<li><div>
      <h3 class="base-search-card__title">Operations Analyst</h3>
      <h4 class="base-search-card__subtitle"><a href="https://www.linkedin.com/company/acme?trk=x">Acme</a></h4>
      <span class="job-search-card__location">Berlin, Germany</span>
      <time class="{time_class}" datetime="2026-11-02">1 hour ago</time>
    </div></li>"""
    return BeautifulSoup(html, "html.parser").find("div")


def scraper(**fields):
    s = LinkedIn()
    s.scraper_input = ScraperInput(**fields)
    return s


def test_new_style_date_class_is_read_without_a_patch():
    for css in ("job-search-card__listdate", "job-search-card__listdate--new"):
        job = scraper()._process_job(card(css), "4399000001")
        assert str(job.date_posted) == "2026-11-02", css
        assert job.job_url == "https://www.linkedin.com/jobs/view/4399000001"


def test_a_failed_detail_page_is_logged_by_jobspy_itself(caplog):
    class Session:
        def get(self, url):
            raise OSError("connection reset")

    s = scraper(fetch_description=True)
    s.session = Session()
    with caplog.at_level(logging.WARNING):
        assert s._fetch_details("4399000001") == {}
    assert any("4399000001" in r.getMessage() and "connection reset" in r.getMessage() for r in caplog.records)


def test_the_signup_wall_is_logged_too(caplog):
    class Response:
        status_code = 200
        url = "https://www.linkedin.com/signup"
        text = ""

    class Session:
        def get(self, url):
            return Response()

    s = scraper(fetch_description=True)
    s.session = Session()
    with caplog.at_level(logging.WARNING):
        assert s._fetch_details("4399000001") == {}
    assert any("redirected" in r.getMessage() for r in caplog.records)
