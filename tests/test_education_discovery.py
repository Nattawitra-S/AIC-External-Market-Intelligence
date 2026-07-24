"""
tests/test_education_discovery.py
==================================
Tests for ETL/education_discovery.py -- publication-page release discovery.

All tests use local HTML fixture strings, mirroring the real page's
structure (verified by fetching the live page during design). No live
network access occurs in this test file.

Run:
    pytest tests/test_education_discovery.py -v
"""
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from ETL.education_discovery import (
    parse_release_links,
    select_newest_complete_release,
    validate_download_url,
    discover_current_education_urls,
)


def _block(month_year: str, kind_label: str, href: str) -> str:
    """One repeating <h3>...</h3><ul>...<a class="file type-xlsx" href=...></a>...</ul> block,
    matching the real page's structure exactly."""
    return f"""
        <h3>{month_year} {kind_label}</h3>
        <ul aria-label="Files and links">
          <li>
            <a class="file type-xlsx" href="{href}">
              <span class="label">Download <span>XLSX</span></span>
            </a>
          </li>
        </ul>
    """


TWO_COMPLETE_RELEASES_HTML = "<html><body>" + "".join([
    _block("May 2026", "Latest data", "/download/20217/x/45084/may-2026-latest-data/xlsx"),
    _block("May 2026", "All data", "/download/20217/x/45085/may-2026-all-data/xlsx"),
    _block("December 2025", "Latest data", "/download/20217/x/44565/december-2025-latest-data/xlsx"),
    _block("December 2025", "All data", "/download/20217/x/44305/december-2025-all-data/xlsx"),
]) + "</body></html>"

INCOMPLETE_NEWEST_HTML = "<html><body>" + "".join([
    # June 2026: only "Latest data" published so far -- incomplete, must be skipped.
    _block("June 2026", "Latest data", "/download/20217/x/46001/june-2026-latest-data/xlsx"),
    _block("May 2026", "Latest data", "/download/20217/x/45084/may-2026-latest-data/xlsx"),
    _block("May 2026", "All data", "/download/20217/x/45085/may-2026-all-data/xlsx"),
]) + "</body></html>"

NO_COMPLETE_RELEASE_HTML = "<html><body>" + "".join([
    _block("June 2026", "Latest data", "/download/20217/x/46001/june-2026-latest-data/xlsx"),
    _block("May 2026", "All data", "/download/20217/x/45085/may-2026-all-data/xlsx"),
]) + "</body></html>"

CROSS_DOMAIN_HTML = "<html><body>" + "".join([
    _block("May 2026", "Latest data", "https://evil.example.com/download/x/latest-data/xlsx"),
    _block("May 2026", "All data", "/download/20217/x/45085/may-2026-all-data/xlsx"),
]) + "</body></html>"

NON_HTTPS_HTML = "<html><body>" + "".join([
    _block("May 2026", "Latest data", "http://www.education.gov.au/download/20217/x/45084/may-2026-latest-data/xlsx"),
    _block("May 2026", "All data", "/download/20217/x/45085/may-2026-all-data/xlsx"),
]) + "</body></html>"


class TestParseReleaseLinks(unittest.TestCase):

    def test_parses_all_release_links(self):
        links = parse_release_links(TWO_COMPLETE_RELEASES_HTML)
        self.assertEqual(len(links), 4)
        kinds = {(l.year, l.month, l.kind) for l in links}
        self.assertEqual(kinds, {
            (2026, 5, "latest_data"), (2026, 5, "all_data"),
            (2025, 12, "latest_data"), (2025, 12, "all_data"),
        })

    def test_ignores_non_matching_headings(self):
        html = "<html><body><h3>May 2026 Summary infographic</h3>" \
               '<a class="file type-pdf" href="/download/x/pdf">PDF</a></body></html>'
        links = parse_release_links(html)
        self.assertEqual(links, [])


class TestSelectNewestCompleteRelease(unittest.TestCase):

    def test_selects_newest_complete_pair(self):
        links = parse_release_links(TWO_COMPLETE_RELEASES_HTML)
        release = select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        self.assertEqual((release.year, release.month), (2026, 5))
        self.assertTrue(release.basic_url.endswith("/45085/may-2026-all-data/xlsx"))
        self.assertTrue(release.detailed_url.endswith("/45084/may-2026-latest-data/xlsx"))

    def test_ignores_older_releases(self):
        links = parse_release_links(TWO_COMPLETE_RELEASES_HTML)
        release = select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        self.assertNotIn("december", release.basic_url.lower())
        self.assertNotIn("december", release.detailed_url.lower())

    def test_rejects_incomplete_newest_selects_next_complete(self):
        links = parse_release_links(INCOMPLETE_NEWEST_HTML)
        release = select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        # June 2026 (incomplete -- only Latest data) must be skipped in
        # favour of the newest COMPLETE release, May 2026.
        self.assertEqual((release.year, release.month), (2026, 5))
        self.assertNotIn("june", release.basic_url.lower())
        self.assertNotIn("june", release.detailed_url.lower())

    def test_no_complete_release_raises(self):
        links = parse_release_links(NO_COMPLETE_RELEASE_HTML)
        with self.assertRaises(RuntimeError) as ctx:
            select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        self.assertIn("No complete", str(ctx.exception))

    def test_resolves_relative_hrefs_to_absolute(self):
        links = parse_release_links(TWO_COMPLETE_RELEASES_HTML)
        release = select_newest_complete_release(links, base_url="https://www.education.gov.au/some/page")
        self.assertTrue(release.basic_url.startswith("https://www.education.gov.au/download/"))
        self.assertTrue(release.detailed_url.startswith("https://www.education.gov.au/download/"))


class TestUrlSafety(unittest.TestCase):

    def test_rejects_cross_domain_url(self):
        links = parse_release_links(CROSS_DOMAIN_HTML)
        with self.assertRaises(ValueError) as ctx:
            select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        self.assertIn("off-host", str(ctx.exception))

    def test_rejects_non_https_url(self):
        links = parse_release_links(NON_HTTPS_HTML)
        with self.assertRaises(ValueError) as ctx:
            select_newest_complete_release(links, base_url="https://www.education.gov.au/x")
        self.assertIn("non-HTTPS", str(ctx.exception))

    def test_validate_download_url_accepts_valid_url(self):
        # Should not raise.
        validate_download_url(
            "https://www.education.gov.au/download/20217/x/45085/may-2026-all-data/xlsx"
        )

    def test_validate_download_url_rejects_non_download_path(self):
        with self.assertRaises(ValueError):
            validate_download_url("https://www.education.gov.au/some/other/path/xlsx")

    def test_validate_download_url_rejects_non_xlsx_suffix(self):
        with self.assertRaises(ValueError):
            validate_download_url("https://www.education.gov.au/download/20217/x/45085/report/pdf")


class TestDiscoverCurrentEducationUrls(unittest.TestCase):
    """discover_current_education_urls() does real network I/O internally
    (imports `requests` inside the function) -- mock at the `requests.get`
    level so no live page is ever fetched during tests."""

    def test_discover_uses_requests_and_parses_result(self):
        from unittest.mock import patch, MagicMock

        fake_response = MagicMock()
        fake_response.text = TWO_COMPLETE_RELEASES_HTML
        fake_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=fake_response) as mock_get:
            release = discover_current_education_urls()

        self.assertTrue(mock_get.called)
        called_url = mock_get.call_args[0][0]
        self.assertEqual(called_url, "https://www.education.gov.au/international-education-data-and-research/international-student-monthly-summary-and-data-tables")
        self.assertEqual((release.year, release.month), (2026, 5))

    def test_discover_raises_on_http_error(self):
        from unittest.mock import patch, MagicMock
        import requests

        fake_response = MagicMock()
        fake_response.raise_for_status.side_effect = requests.exceptions.HTTPError("404")

        with patch("requests.get", return_value=fake_response):
            with self.assertRaises(requests.exceptions.HTTPError):
                discover_current_education_urls()

    def test_discover_raises_when_no_headings_found(self):
        from unittest.mock import patch, MagicMock

        fake_response = MagicMock()
        fake_response.text = "<html><body>no relevant content here</body></html>"
        fake_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=fake_response):
            with self.assertRaises(RuntimeError) as ctx:
                discover_current_education_urls()
        self.assertIn("No 'All data'/'Latest data' headings found", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
