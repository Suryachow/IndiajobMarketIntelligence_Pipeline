"""Sitemap-based discovery for the generic crawler.

For a careers site whose ATS we have no typed adapter for, we let the *publisher*
tell us where the jobs are:

1. read ``robots.txt`` and follow its ``Sitemap:`` directives (falling back to
   ``/sitemap.xml``);
2. walk sitemap **indexes** (``<sitemapindex>``) down to concrete URL sets;
3. keep the URLs that look like individual postings rather than listing or
   search pages.

Parsing is pure text -> data so it can be unit-tested without network access;
:func:`discover_job_urls` is the thin driver that pairs it with an HTTP client.
"""

from __future__ import annotations

import re
from typing import Callable, List, Set, Tuple
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

_SITEMAP_RE = re.compile(r"^\s*sitemap\s*:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)
_LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)

# Path fragments that indicate an individual posting.
_JOB_HINTS = (
    "/job/", "/jobs/", "/job-", "/jobs-", "/position", "/opening", "/vacanc",
    "/requisition", "/careers/job", "/career/job", "/detail/", "/posting",
)
# Path fragments that indicate a listing, search or other non-posting page.
_LISTING_HINTS = (
    "/search", "/login", "/signin", "/apply-now", "/category/", "/department/",
    "/team/", "/page/", "/rss", "/feed",
)
# A collection *directory* on its own is a listing page, not a posting.
_COLLECTION_PATHS = {
    "/", "/job", "/jobs", "/career", "/careers", "/position", "/positions",
    "/opening", "/openings", "/vacancy", "/vacancies", "/opportunities",
}
# Postings on some ATSs (Lever, Ashby) carry no keyword in the path but end in an
# identifier, e.g. ``jobs.lever.co/meesho/8f3c1d2a`` or ``.../senior-dev-4412``.
_ID_TAIL_RE = re.compile(r"\d")
# A collection directory followed by a role slug is a posting, e.g.
# ``/career/systems-engineer`` or ``/jobs/data-analyst``.
_COLLECTION_SEGMENTS = {
    "career", "careers", "job", "jobs", "position", "positions",
    "opening", "openings", "vacancy", "vacancies", "posting", "postings",
}


def clean_loc(value: str) -> str:
    """Strip whitespace and any CDATA wrapper from a ``<loc>`` value."""
    text = (value or "").strip()
    if text.startswith("<![CDATA["):
        text = text[9:]
    if text.endswith("]]>"):
        text = text[:-3]
    return text.strip()


def parse_robots_sitemaps(text: str) -> List[str]:
    """Return the ``Sitemap:`` URLs advertised in a ``robots.txt`` body."""
    if not text:
        return []
    return [match.group(1).strip() for match in _SITEMAP_RE.finditer(text)]


def parse_sitemap(xml_text: str) -> Tuple[List[str], List[str]]:
    """Return ``(page_urls, child_sitemap_urls)`` from a sitemap document.

    Handles both ``<urlset>`` (a set of pages) and ``<sitemapindex>`` (a set of
    nested sitemaps), and tolerates malformed XML with a regex fallback.
    """
    if not xml_text:
        return [], []
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return _parse_sitemap_regex(xml_text)

    tag = _localname(root.tag)
    pages: List[str] = []
    children: List[str] = []
    if tag == "sitemapindex":
        for node in root:
            if _localname(node.tag) == "sitemap":
                loc = _child_text(node, "loc")
                if loc:
                    children.append(loc)
        return pages, children
    if tag == "urlset":
        for node in root:
            if _localname(node.tag) == "url":
                loc = _child_text(node, "loc")
                if loc:
                    pages.append(loc)
        return pages, children
    return _parse_sitemap_regex(xml_text)


def is_job_like_url(url: str) -> bool:
    """Heuristic: does ``url`` point at an individual posting?"""
    if not url:
        return False
    path = (urlsplit(url).path or "").lower()
    trimmed = path.rstrip("/") or "/"
    if trimmed in _COLLECTION_PATHS:
        return False
    if any(bad in path for bad in _LISTING_HINTS):
        return False
    if any(hint in path for hint in _JOB_HINTS):
        return True
    segments = [seg for seg in trimmed.split("/") if seg]
    # ``/career/systems-engineer`` — a collection directory holding a role slug.
    if len(segments) >= 2 and any(seg in _COLLECTION_SEGMENTS for seg in segments[:-1]):
        return True
    # Keyword-free postings (Lever/Ashby): ``/<company>/<id>``.
    return len(segments) >= 2 and bool(_ID_TAIL_RE.search(segments[-1]))


def filter_job_urls(urls: List[str]) -> List[str]:
    """De-duplicate ``urls`` (order preserving) and keep the job-like ones."""
    out: List[str] = []
    seen: Set[str] = set()
    for url in urls:
        if not url or url in seen:
            continue
        seen.add(url)
        if is_job_like_url(url):
            out.append(url)
    return out


def discover_job_urls(
    fetch: Callable[[str], str],
    careers_url: str,
    max_sitemaps: int = 30,
    max_urls: int = 20000,
) -> Tuple[List[str], List[str]]:
    """Return ``(job_urls, notes)`` discovered through robots.txt + sitemaps.

    ``fetch`` is any callable ``url -> text`` (e.g. ``HttpClient.get_text``).
    """
    notes: List[str] = []
    origin = _origin(careers_url)
    if not origin:
        return [], ["could not derive an origin from the careers URL"]

    try:
        sitemaps = parse_robots_sitemaps(fetch(urljoin(origin, "/robots.txt")))
    except Exception as exc:  # noqa: BLE001 - robots.txt is best-effort
        sitemaps = []
        notes.append(f"robots.txt unavailable ({type(exc).__name__})")

    if sitemaps:
        notes.append(f"robots.txt advertised {len(sitemaps)} sitemap(s)")
    else:
        sitemaps = [urljoin(origin, "/sitemap.xml")]
        notes.append("no Sitemap directive found; trying /sitemap.xml")

    pages: List[str] = []
    seen: Set[str] = set()
    queue: List[str] = []
    for sitemap in sitemaps:
        if sitemap not in seen:
            queue.append(sitemap)

    scanned = 0
    while queue and scanned < max_sitemaps:
        sitemap = queue.pop(0)
        if sitemap in seen:
            continue
        seen.add(sitemap)
        try:
            xml_text = fetch(sitemap)
        except Exception as exc:  # noqa: BLE001 - one bad sitemap must not abort
            notes.append(f"sitemap unreachable: {sitemap} ({type(exc).__name__})")
            continue
        scanned += 1
        found, children = parse_sitemap(xml_text)
        pages.extend(found)
        for child in children:
            if child not in seen:
                queue.append(child)

    job_urls = filter_job_urls(pages)
    truncated = len(job_urls) > max_urls
    if truncated:
        job_urls = job_urls[:max_urls]
    notes.append(
        f"scanned {scanned} sitemap(s): {len(pages)} URLs -> {len(job_urls)} job-like"
        + (" (truncated)" if truncated else "")
    )
    return job_urls, notes


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if tag else ""


def _child_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _localname(child.tag) == name:
            return clean_loc(child.text or "")
    return ""


def _parse_sitemap_regex(text: str) -> Tuple[List[str], List[str]]:
    locs = [clean_loc(match.group(1)) for match in _LOC_RE.finditer(text)]
    locs = [loc for loc in locs if loc]
    lowered = text.lower()
    if "<sitemapindex" in lowered:
        return [], locs
    if "<urlset" in lowered:
        return locs, []
    return [], []


def _origin(url: str) -> str:
    parts = urlsplit(url or "")
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"
