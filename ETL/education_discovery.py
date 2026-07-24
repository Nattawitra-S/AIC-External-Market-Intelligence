"""
ETL/education_discovery.py
===========================
Auto-discovers the current Department of Education "All data" (Basic) and
"Latest data" (Detailed) XLSX download URLs from the public publication
page, instead of hardcoding a URL that embeds a resource ID that changes
with every monthly release.

Deliberately pure standard-library HTML parsing (html.parser) -- the page
structure is simple and consistent enough not to need BeautifulSoup/lxml
(both happen to already be installed as transitive deps in this project's
environment, but adding them as a declared dependency isn't justified for
parsing a handful of predictable <h3>...</h3> headings).

Page structure (verified by fetching the real page):

    <h3>May 2026 Latest data</h3>
    <ul aria-label="Files and links">
      <li>
        <a class="file type-xlsx" href="/download/20217/.../45084/may-2026-latest-data/xlsx">
          ...
        </a>
      </li>
    </ul>
    <h3>May 2026 All data</h3>
    <ul aria-label="Files and links">
      <li>
        <a class="file type-xlsx" href="/download/20217/.../45085/may-2026-all-data/xlsx">
          ...
        </a>
      </li>
    </ul>

The page lists multiple releases (current + at least one prior month), so
selection must pick the newest release that has BOTH links present --
never independently pick the newest Basic and newest Detailed link, since
that could silently mix two different reporting months together.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

PUBLICATION_PAGE_URL = (
    "https://www.education.gov.au/international-education-data-and-research/"
    "international-student-monthly-summary-and-data-tables"
)
ALLOWED_HOST = "www.education.gov.au"

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}

_HEADING_RE = re.compile(
    r"^(?P<month>[A-Za-z]+)\s+(?P<year>\d{4})\s+(?P<kind>Latest data|All data)$"
)


@dataclass(frozen=True)
class ReleaseLink:
    year: int
    month: int
    kind: str  # "all_data" | "latest_data"
    href: str


class _ReleaseLinkParser(HTMLParser):
    """
    Walks the page looking for <h3>Month Year Kind</h3> headings, each
    immediately followed (within the same repeating block) by a single
    <a class="file type-xlsx" href="..."> download link. Any other <a>
    tags encountered before that one (there normally aren't any within a
    block, but this is defensive) are ignored rather than mistaken for the
    download link.
    """

    def __init__(self):
        super().__init__()
        self.links: list[ReleaseLink] = []
        self._in_h3 = False
        self._h3_text = ""
        self._pending: dict | None = None

    def handle_starttag(self, tag, attrs):
        attrs_d = dict(attrs)
        if tag == "h3":
            self._in_h3 = True
            self._h3_text = ""
        elif tag == "a" and self._pending is not None:
            classes = (attrs_d.get("class") or "").split()
            href = attrs_d.get("href")
            if href and "type-xlsx" in classes:
                self.links.append(ReleaseLink(
                    year=self._pending["year"],
                    month=self._pending["month"],
                    kind=self._pending["kind"],
                    href=href,
                ))
                self._pending = None

    def handle_endtag(self, tag):
        if tag == "h3" and self._in_h3:
            self._in_h3 = False
            m = _HEADING_RE.match(self._h3_text.strip())
            if m:
                month_num = _MONTH_NAMES.get(m.group("month").lower())
                self._pending = (
                    {
                        "year": int(m.group("year")),
                        "month": month_num,
                        "kind": "all_data" if m.group("kind") == "All data" else "latest_data",
                    }
                    if month_num else None
                )
            else:
                self._pending = None

    def handle_data(self, data):
        if self._in_h3:
            self._h3_text += data


def parse_release_links(html_text: str) -> list[ReleaseLink]:
    """Pure parsing function -- no network I/O -- so it's directly testable
    against local HTML fixtures."""
    parser = _ReleaseLinkParser()
    parser.feed(html_text)
    return parser.links


def validate_download_url(url: str) -> None:
    """
    Only accept https://www.education.gov.au/download/.../xlsx links.
    Raises ValueError on any violation. Applied to every URL before it is
    ever downloaded.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Rejected non-HTTPS discovery URL: {url}")
    if parsed.netloc != ALLOWED_HOST:
        raise ValueError(f"Rejected off-host discovery URL (expected {ALLOWED_HOST}): {url}")
    if not parsed.path.startswith("/download/"):
        raise ValueError(f"Rejected non-/download/ discovery URL: {url}")
    last_segment = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if last_segment.lower() != "xlsx":
        raise ValueError(f"Rejected non-xlsx discovery URL: {url}")


@dataclass(frozen=True)
class DiscoveredRelease:
    year: int
    month: int
    basic_url: str
    detailed_url: str


def select_newest_complete_release(
    links: list[ReleaseLink], base_url: str = PUBLICATION_PAGE_URL
) -> DiscoveredRelease:
    """
    Group links by (year, month). A release is 'complete' only if it has
    BOTH an all_data and a latest_data link. Select the newest COMPLETE
    release. If the newest period on the page is incomplete (e.g. Latest
    data published before All data), it is skipped in favour of the next
    most recent complete release, not treated as an error by itself --
    only the absence of ANY complete release is an error.
    """
    by_period: dict[tuple[int, int], dict[str, str]] = {}
    for link in links:
        by_period.setdefault((link.year, link.month), {})[link.kind] = link.href

    complete = [
        (period, kinds) for period, kinds in by_period.items()
        if "all_data" in kinds and "latest_data" in kinds
    ]
    if not complete:
        raise RuntimeError(
            "No complete (Basic + Detailed) release pair found on the publication page. "
            f"Found periods: {sorted(by_period.keys())}"
        )

    (year, month), kinds = max(complete, key=lambda item: item[0])

    basic_url = urljoin(base_url, kinds["all_data"])
    detailed_url = urljoin(base_url, kinds["latest_data"])
    validate_download_url(basic_url)
    validate_download_url(detailed_url)

    return DiscoveredRelease(year=year, month=month, basic_url=basic_url, detailed_url=detailed_url)


def discover_current_education_urls(timeout: int = 60) -> DiscoveredRelease:
    """
    Fetch the publication page and return the newest complete release's
    Basic + Detailed download URLs. Raises clearly on any failure (network
    error, no headings found, no complete pair) -- never falls back to a
    stale hardcoded URL.
    """
    import requests

    resp = requests.get(
        PUBLICATION_PAGE_URL,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 AIC-ETL/1.0"},
    )
    resp.raise_for_status()

    links = parse_release_links(resp.text)
    if not links:
        raise RuntimeError(
            f"No 'All data'/'Latest data' headings found on {PUBLICATION_PAGE_URL} "
            "-- page structure may have changed."
        )
    return select_newest_complete_release(links, base_url=PUBLICATION_PAGE_URL)
