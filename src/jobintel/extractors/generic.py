"""Generic, platform-agnostic crawler - the "universal" fallback adapter.

Used for any employer whose careers site is **not** served by one of the typed
adapters (SAP SuccessFactors, Phenom, or any unknown ATS). Strategy, in order:

1. **Discover** candidate job URLs from the site's own ``robots.txt`` →
   ``sitemap.xml`` index (see :mod:`jobintel.sitemap`). The publisher's sitemap
   gives a bounded, auditable completeness set for one cheap request, instead of
   scraping a listing page.
2. **Extract** each posting from its embedded schema.org ``JobPosting`` - first as
   JSON-LD, then as **microdata** (``itemprop=``, used by SuccessFactors/Phenom
   portals) - the vendor-neutral standard that career sites publish for Google
   Jobs, falling back to ``og:``/``<title>`` metadata (see :mod:`jobintel.jsonld`).
3. **Filter** to postings that resolve to India and **validate** them into the
   shared :class:`~jobintel.schema.JobPosting` model.

Normalization, deduplication, coverage accounting and reporting are inherited
from the base pipeline, so the output is schema-identical to the typed adapters.

**JavaScript / SPA sites:** a site that renders all content client-side *and*
publishes neither a sitemap nor JSON-LD cannot be crawled over raw HTTP. For
those, run the crawler with the headless renderer (``--render``), which swaps in
:class:`~jobintel.render.RenderingClient` so job pages come back as the
browser-rendered DOM. When the sitemap turns up nothing, discovery also falls
back to **harvesting job links from the rendered careers page**, so a JS-only
listing page still yields a crawlable set of job URLs.
"""

from __future__ import annotations

import datetime as _dt
import re
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from ..http_client import HttpError
from ..jsonld import (
    extract_job_postings,
    extract_label_values,
    extract_meta,
    extract_microdata,
    parse_job_posting,
)
from ..normalize import (
    clean_html,
    extract_experience,
    extract_salary,
    infer_work_arrangement,
    is_india_location,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
)
from ..schema import validate
from ..sitemap import discover_job_urls
from .base import BaseExtractor, ExtractionResult

# Path/query fragments that mark a link as a *job posting* rather than chrome.
_JOB_LINK_RE = re.compile(
    r"/job[s]?/|/careers?/job|jobdetail|job-detail|/position[s]?/|/opening[s]?/|"
    r"/vacanc(?:y|ies)/|/requisition/|/apply/|jobid=|job_id=|requisitionid=",
    re.IGNORECASE,
)
# Links that match the pattern above but are clearly not postings.
_JOB_LINK_BLOCKLIST = re.compile(
    r"login|signin|sign-in|register|apply-now$|/jobs/?$|/careers/?$|privacy|cookie",
    re.IGNORECASE,
)

_EMPTY_FIELDS = {
    "title": "", "description": "", "date_posted": "", "valid_through": "",
    "employment_type": "", "job_id": "", "category": "", "industry": "",
    "job_url": "", "work_mode": "", "city": "", "region": "", "country": "",
    "location_text": "", "min_salary": "", "max_salary": "", "currency": "",
    "experience": "",
}


class GenericExtractor(BaseExtractor):
    platform = "generic"

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        start_url = self._cfg.get("sitemap_url") or self.company.get("careers_url", "")
        if not start_url:
            result.status = "FAILED"
            result.errors.append("no careers URL supplied for the generic crawler")
            return result

        job_urls, notes = discover_job_urls(
            self.client.get_text,
            start_url,
            max_sitemaps=int(self._cfg.get("max_sitemaps", 30)),
            max_urls=int(self._cfg.get("max_urls", 20000)),
        )
        result.notes.extend(notes)
        if not job_urls:
            result.status = "FAILED"
            result.errors.append("no job URLs discovered via robots.txt/sitemap.xml")
            result.notes.append("the site may be a client-rendered SPA with no sitemap")
            return result

        fetched = self.client.map_concurrent(job_urls, self._fetch, workers=self.workers)

        india: List[Any] = []
        expired = 0
        unusable = 0
        for item in fetched:
            if not item:
                unusable += 1
                continue
            url, html = item
            posting = self._build_posting(url, html)
            if posting is None:
                unusable += 1
                continue
            if posting.job_status != "Active":
                expired += 1
                continue
            if self._is_india(posting):
                india.append(posting)

        result.notes.append(
            f"fetched {len(job_urls) - unusable}/{len(job_urls)} job pages; "
            f"{len(india)} located in India"
            + (f"; {expired} expired" if expired else "")
            + (f"; {unusable} unreadable" if unusable else "")
        )
        result.portal_total = len(india)
        result.portal_total_scope = "India job pages discovered via robots.txt/sitemap.xml"

        for posting in self._cap(india):
            problems = validate(posting)
            if problems:
                result.errors.append(
                    f"{posting.job_id or posting.job_url}: {'; '.join(problems)}"
                )
                continue
            result.postings.append(posting)

        if result.errors and not result.postings:
            result.status = "FAILED"
        elif result.errors:
            result.status = "PARTIAL"
        return result

    # -- helpers -----------------------------------------------------------
    def _fetch(self, url: str) -> Tuple[str, str]:
        """Fetch one job page. Failures surface as ``None`` via map_concurrent.

        Uses :meth:`get_page` so a rendering client returns the browser-rendered
        DOM for SPAs, while ``robots.txt``/``sitemap.xml`` in discovery keep
        using raw HTTP.
        """
        return url, self.client.get_page(url)

    def _build_posting(self, url: str, html: str) -> Optional[Any]:
        nodes = extract_job_postings(html)
        if nodes:
            fields = parse_job_posting(nodes[0])
        else:
            fields = extract_microdata(html)
            if not fields:
                fields = self._fallback_fields(html)
                if fields is None:
                    return None
        return self._posting_from_fields(url, fields)

    @staticmethod
    def _fallback_fields(html: str) -> Optional[Dict[str, str]]:
        """No schema.org: combine ``Label → value`` blocks with metadata."""
        labels = extract_label_values(html)
        meta = extract_meta(html)
        if not labels and not meta.get("title") and not meta.get("description"):
            return None
        fields = dict(_EMPTY_FIELDS)
        fields["title"] = meta.get("title", "")
        fields["description"] = meta.get("description", "")
        fields["job_url"] = meta.get("job_url", "")
        fields.update({key: value for key, value in labels.items() if key in fields})
        return fields

    def _posting_from_fields(self, url: str, fields: Dict[str, str]) -> Any:
        location_text = fields.get("location_text", "")
        city = fields.get("city", "")
        state = fields.get("region", "")
        country = fields.get("country", "")
        if not (city or state or country) and location_text:
            city, state, country = parse_location(location_text)
        if not country and is_india_location(location_text or f"{city} {state}"):
            country = "India"

        description = clean_html(fields.get("description", ""))
        min_salary = fields.get("min_salary", "")
        max_salary = fields.get("max_salary", "")
        currency = fields.get("currency", "")
        # A schema.org baseSalary (already parsed into the fields above) wins;
        # only when the page carries none do we fall back to a pay range written
        # in the posting text - the same order every typed adapter uses.
        if not (min_salary or max_salary):
            min_salary, max_salary, currency = extract_salary(description)

        job_url = fields.get("job_url") or url
        if job_url and not job_url.startswith("http"):
            job_url = url
        job_url = _collapse_slashes(job_url)

        posting = self.base_posting(
            job_id=fields.get("job_id", "") or _id_from_url(url),
            job_title=clean_html(fields.get("title", "")),
            job_description=description,
            job_category=fields.get("category", ""),
            business_division="",
            job_location=location_text,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(
                fields.get("employment_type", "").replace("_", " "), description[:600]
            ),
            work_arrangement=self._work_arrangement(fields, location_text, description),
            experience_required=fields.get("experience", "") or extract_experience(description),
            min_salary=min_salary,
            max_salary=max_salary,
            currency=currency,
            posting_date=parse_posting_date(fields.get("date_posted", "")),
            job_status="Closed" if _is_expired(fields.get("valid_through", "")) else "Active",
            job_url=job_url,
        )
        return posting

    @staticmethod
    def _work_arrangement(fields: Dict[str, str], location_text: str, description: str) -> str:
        if fields.get("work_mode", "") in {"TELECOMMUTE", "REMOTE"}:
            return "Remote"
        return infer_work_arrangement(location_text, description[:1500])

    @staticmethod
    def _is_india(posting: Any) -> bool:
        if (posting.country or "").lower() == "india":
            return True
        haystack = " ".join(
            part for part in (posting.job_location, posting.city, posting.state) if part
        )
        return is_india_location(haystack)


def _id_from_url(url: str) -> str:
    path = urlsplit(url or "").path.rstrip("/")
    if not path:
        return ""
    last = path.rsplit("/", 1)[-1]
    return last[:-5] if last.lower().endswith(".html") else last


def _collapse_slashes(url: str) -> str:
    """Collapse repeated slashes in a URL's path (e.g. ``og:url`` typos)."""
    scheme, sep, rest = (url or "").partition("://")
    if not sep:
        return url
    return f"{scheme}://{re.sub(r'/{2,}', '/', rest)}"


def _is_expired(value: str) -> bool:
    parsed = parse_posting_date(value)
    if not parsed:
        return False
    try:
        return _dt.date.fromisoformat(parsed) < _dt.date.today()
    except ValueError:
        return False
