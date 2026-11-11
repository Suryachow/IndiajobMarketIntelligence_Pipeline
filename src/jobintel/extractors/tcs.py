"""TCS iBegin adapter - headless-browser backed.

Tata Consultancy Services publishes its India openings on the *iBegin* portal
(``ibegin.tcsapps.com``), a client-rendered Angular single-page application.
There is no server-rendered HTML and no usable ``sitemap.xml`` (every path,
including ``/robots.txt``, returns the same SPA shell), and ``www.tcs.com``
sits behind a WAF that answers non-browser clients with 403.

The SPA, however, calls a *public* JSON API (no login). It is gated by an
``XSRF-TOKEN`` cookie that must be echoed back in a ``next-xsrf-token`` request
header. Reproducing that handshake needs a real browser session, so this adapter
uses the *optional* :mod:`jobintel.render` module (Playwright). When Playwright
is absent the employer is reported as ``SKIPPED`` with an actionable note rather
than silently returning nothing.

Two public endpoints are used:

* ``POST {base}/api/{locale}/search/jobs`` - paginated job index
  (``resultsPerPage`` must be 10, 25 or 50);
* ``GET  {base}/api/{locale}/job/desc/{id}`` - full description for one job.

Nothing here bypasses an access control: we drive the public page and its public
API exactly as a visitor's browser does, keep only the session cookie the site
issues, and read the ``robots.txt`` gap as "no directives published".
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..normalize import (
    clean_html,
    extract_salary,
    infer_work_arrangement,
    is_india_location,
    normalize_employment_type,
    parse_location,
)
from ..render import HeadlessSession, RenderError, available as render_available
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

DEFAULT_BASE = "https://ibegin.tcsapps.com/candidate/next"
DEFAULT_LOCALE = "en-IN"
# The iBegin search API rejects anything other than 10, 25 or 50.
PAGE_SIZE = 50

_SETUP_HINT = (
    "headless renderer unavailable; install the optional dependency with "
    "'pip install playwright' and 'python -m playwright install chromium'"
)

_NUMERIC_ID_RE = re.compile(r"^(\d+)")
_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


class TCSExtractor(BaseExtractor):
    """Extract TCS India openings through a headless browser session."""

    platform = "tcs"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.base = (self._cfg.get("base_url") or DEFAULT_BASE).rstrip("/")
        self.locale = self._cfg.get("locale") or DEFAULT_LOCALE

    # -- entry point -------------------------------------------------------
    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        if not render_available():
            result.status = "SKIPPED"
            result.errors.append(_SETUP_HINT)
            return result

        try:
            self._crawl(result)
        except RenderError as exc:
            result.errors.append(str(exc))

        if result.errors and not result.postings:
            result.status = "FAILED"
        elif result.errors:
            result.status = "PARTIAL"
        return result

    # -- crawl steps -------------------------------------------------------
    def _crawl(self, result: ExtractionResult) -> None:
        landing = self.company.get("careers_url") or f"{self.base}/{self.locale}/landing"
        timeout_ms = int(self._cfg.get("timeout_ms", 60000))
        detail_batch = int(self._cfg.get("detail_batch", 8))

        with HeadlessSession(timeout_ms=timeout_ms) as session:
            session.goto(landing)
            token = session.cookie("XSRF-TOKEN")
            if not token:
                raise RenderError(
                    "session did not receive an XSRF-TOKEN cookie; the portal "
                    "may have changed its handshake"
                )
            headers = {
                "Accept": "application/json, text/plain, */*",
                "next-xsrf-token": token,
            }

            stubs, total, pages = self._fetch_index(session, headers)
            result.portal_total = total
            result.portal_total_scope = (
                "India postings returned by the iBegin search API (regular + walk-in)"
            )
            result.notes.append(
                f"iBegin index returned {total} posting(s) across {pages} page(s)"
            )

            self._build_postings(session, headers, stubs, result, detail_batch)

    def _fetch_index(
        self, session: HeadlessSession, headers: Dict[str, str]
    ) -> tuple[List[Dict[str, Any]], int, int]:
        """Page through the search index and return ``(stubs, total, pages)``."""
        url = f"{self.base}/api/{self.locale}/search/jobs"
        stubs: List[Dict[str, Any]] = []
        total = 0
        pages = 1
        page = 1
        while page <= pages:
            payload = session.post_json(url, self._search_body(page), headers)
            body = payload.get("payload") or {}
            if page == 1:
                total = _as_int(body.get("totalElements")) or 0
                pages = max(1, _as_int(body.get("totalPages")) or 1)
            stubs.extend(body.get("content") or [])
            page += 1
        if not total:
            total = len(stubs)
        return stubs, total, pages

    def _build_postings(
        self,
        session: HeadlessSession,
        headers: Dict[str, str],
        stubs: List[Dict[str, Any]],
        result: ExtractionResult,
        detail_batch: int,
    ) -> None:
        kept: List[tuple[Dict[str, Any], str]] = []
        non_india = closed = 0
        for stub in self._cap(stubs):
            job_id = _clean(stub.get("id"))
            location = _clean(stub.get("location"))
            if not job_id:
                continue
            if not is_india_location(location):
                non_india += 1
                continue
            if _is_expired(_clean(stub.get("applyBy")), self.extraction_date):
                closed += 1
                continue
            kept.append((stub, job_id))

        if non_india:
            result.notes.append(f"skipped {non_india} non-India posting(s)")
        if closed:
            result.notes.append(
                f"skipped {closed} posting(s) already closed (apply-by date passed)"
            )
        if not kept:
            result.notes.append("no active India postings after filtering")
            return

        details = self._fetch_details(session, headers, [jid for _, jid in kept], detail_batch)
        missing_details = 0
        for (stub, job_id), detail in zip(kept, details):
            if detail is None:
                missing_details += 1
                detail = {}
            posting = self._to_posting(stub, detail, job_id)
            problems = validate(posting)
            if problems:
                result.errors.append(f"{job_id}: {'; '.join(problems)}")
                continue
            result.postings.append(posting)
        if missing_details:
            result.notes.append(
                f"{missing_details} posting(s) extracted without a description "
                "(detail endpoint returned nothing)"
            )

    def _fetch_details(
        self,
        session: HeadlessSession,
        headers: Dict[str, str],
        job_ids: List[str],
        detail_batch: int,
    ) -> List[Optional[Dict[str, Any]]]:
        """Fetch the description payload for each job, preserving order."""
        urls: List[str] = []
        for job_id in job_ids:
            match = _NUMERIC_ID_RE.match(job_id)
            urls.append(f"{self.base}/api/{self.locale}/job/desc/{match.group(1)}" if match else "")
        fetchable = [u for u in urls if u]
        if not fetchable:
            return [None] * len(job_ids)
        responses = session.get_json_many(fetchable, headers, chunk_size=detail_batch)
        payloads = [r.get("payload") if isinstance(r, dict) else None for r in responses]
        if len(payloads) < len(urls):  # defensive: realign to the requested order
            payloads.extend([None] * (len(urls) - len(payloads)))
        # Re-insert placeholders for jobs that had no numeric id.
        restored: List[Optional[Dict[str, Any]]] = []
        cursor = 0
        for candidate in urls:
            if candidate:
                restored.append(payloads[cursor])
                cursor += 1
            else:
                restored.append(None)
        return restored

    # -- mapping -----------------------------------------------------------
    def _to_posting(
        self, stub: Dict[str, Any], detail: Dict[str, Any], job_id: str
    ) -> Any:
        location = _clean(stub.get("location"))
        city, state, country = parse_location(location)
        if not country and is_india_location(location):
            country = "India"
        description = clean_html(detail.get("description") or "")
        min_sal, max_sal, currency = extract_salary(description)

        posting = self.base_posting(
            job_id=job_id,
            job_title=_clean(stub.get("title")) or _clean(detail.get("title")),
            job_description=description,
            job_category=_prettify(_clean(stub.get("domain")))
            or _clean(detail.get("functionName")),
            business_division=_clean(detail.get("role")),
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(description[:600]),
            work_arrangement=infer_work_arrangement(location, description[:1500]),
            experience_required=_experience(stub),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            job_status="Active",
            job_url=f"{self.base}/{self.locale}/jobs/{job_id}",
        )
        return posting

    def _search_body(self, page: int) -> Dict[str, Any]:
        return {
            "includeRegularJob": True,
            "includeWalkIn": True,
            "searchTerms": [],
            "jobLocationFilters": [],
            "jobDomainFilters": [],
            "experienceLevelFilters": [],
            "skillFilters": [],
            "relevancySortOrder": "desc",
            "recentSortOrder": None,
            "lastDateToApplySortOrder": None,
            "page": page,
            "resultsPerPage": PAGE_SIZE,
        }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _prettify(value: str) -> str:
    return re.sub(r"\s+", " ", value.replace("_", " ")).strip().title()


def _experience(stub: Dict[str, Any]) -> str:
    """Render the published experience band; blank when not published."""
    lo = _as_int(stub.get("minimumExperienceInYears"))
    hi = _as_int(stub.get("maximumExperienceInYears"))
    if lo is not None and hi is not None:
        return f"{lo}-{hi} years"
    if lo is not None:
        return f"{lo}+ years"
    if hi is not None:
        return f"Up to {hi} years"
    return ""


def _is_expired(apply_by: str, today_iso: str) -> bool:
    """True when an ISO-8601 apply-by date is strictly before today.

    A missing/unparseable date is treated as *not* expired - we never drop a
    posting because of absent data.
    """
    match = _ISO_DATE_RE.search(apply_by or "")
    if not match:
        return False
    return match.group(1) < today_iso
