"""Workday adapter (``*.myworkdayjobs.com``).

Workday powers the career sites of a large share of multinational employers.
Each tenant exposes an unauthenticated "CXS" JSON API that the public career
page itself calls, so no scraping of HTML or access-control bypass is required.

Strategy
--------
1. ``POST /wday/cxs/{tenant}/{site}/jobs`` returns the facet tree; we discover
   the facet that represents the country "India" (tenants differ: some expose a
   flat ``Location_Country`` facet, others a grouped ``locationMainGroup``).
2. We then page through the India-filtered result set (20 per request).
3. For every posting we fetch ``/{externalPath}`` to obtain the full job
   description and metadata.

If no usable India facet can be discovered the adapter falls back to scanning
the unfiltered result set and filtering on ``locationsText``; that fallback is
recorded in ``notes`` for transparency.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..http_client import HttpError
from ..normalize import (
    clean_html,
    extract_experience,
    extract_salary,
    infer_work_arrangement,
    is_india_location,
    map_workplace_type,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
)
from ..schema import JobPosting, validate
from .base import BaseExtractor, ExtractionResult

PAGE_SIZE = 20
MAX_PAGES = 120  # safety valve: 2,400 postings per employer
_INDIA_RE = re.compile(r"\bindia\b", re.IGNORECASE)


class WorkdayExtractor(BaseExtractor):
    platform = "workday"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.tenant = self._cfg["tenant"]
        self.wd = self._cfg.get("wd", "wd1")
        self.site = self._cfg["site"]
        self.host = f"{self.tenant}.{self.wd}.myworkdayjobs.com"
        self.api = f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}"

    # -- public ------------------------------------------------------------
    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            first = self._search(offset=0, limit=1)
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"listing endpoint unreachable: {exc}")
            return result

        facets = first.get("facets", []) or []
        facet_key, facet_values, strategy = _discover_india_filter(facets)

        if facet_key:
            result.notes.append(
                f"India facet strategy: '{strategy}' (facet='{facet_key}', "
                f"{len(facet_values)} value(s))"
            )
            listings, total = self._paginate(facet_key, facet_values, result)
            result.portal_total = total
            result.portal_total_scope = "Workday India facet total"
        else:
            result.notes.append(
                "No India facet discovered; falling back to full scan filtered on locationsText"
            )
            listings, total = self._paginate_full_scan(result)
            result.portal_total = len(listings)
            result.portal_total_scope = "postings matching India in locationsText"

        listings = self._cap(listings)
        result.notes.append(f"Listed {len(listings)} India postings; fetching details")

        details = self.client.map_concurrent(
            listings,
            lambda item: self._fetch_detail(item, result),
            workers=self.workers,
        )
        skipped_non_india = 0
        for item, detail in zip(listings, details):
            if detail is None:
                result.errors.append(f"detail fetch failed: {item.get('externalPath')}")
                continue
            posting = self._to_posting(item, detail)
            if not _is_india_posting(posting):
                skipped_non_india += 1
                continue
            problems = validate(posting)
            if problems:
                result.errors.append(f"{posting.job_id or posting.job_title}: {'; '.join(problems)}")
                continue
            result.postings.append(posting)

        if skipped_non_india:
            result.notes.append(
                f"skipped {skipped_non_india} posting(s) with no India location"
            )
        if result.errors:
            result.status = "PARTIAL" if result.postings else "FAILED"
        return result

    # -- listing -----------------------------------------------------------
    def _search(
        self,
        offset: int,
        limit: int,
        facet_key: Optional[str] = None,
        facet_values: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "appliedFacets": {facet_key: facet_values} if facet_key else {},
            "limit": limit,
            "offset": offset,
            "searchText": "",
        }
        return self.client.post_json(self.api + "/jobs", payload, headers={"Referer": f"https://{self.host}/{self.site}"})

    def _paginate(
        self, facet_key: str, facet_values: List[str], result: ExtractionResult
    ) -> Tuple[List[Dict[str, Any]], int]:
        listings: List[Dict[str, Any]] = []
        total: Optional[int] = None
        offset = 0
        for _ in range(MAX_PAGES):
            page = self._search(offset, PAGE_SIZE, facet_key, facet_values)
            # Workday reports the authoritative ``total`` only on the first page;
            # subsequent pages return 0, so we latch the first non-null value.
            if total is None:
                total = page.get("total") or 0
            batch = page.get("jobPostings", []) or []
            if not batch:
                break
            listings.extend(batch)
            offset += PAGE_SIZE
            if offset >= total:
                break
        return listings, int(total or len(listings))

    def _paginate_full_scan(
        self, result: ExtractionResult
    ) -> Tuple[List[Dict[str, Any]], int]:
        listings: List[Dict[str, Any]] = []
        offset = 0
        total: Optional[int] = None
        for _ in range(MAX_PAGES):
            page = self._search(offset, PAGE_SIZE)
            if total is None:
                total = page.get("total") or 0
            batch = page.get("jobPostings", []) or []
            if not batch:
                break
            for item in batch:
                if is_india_location(item.get("locationsText", "")):
                    listings.append(item)
            offset += PAGE_SIZE
            if total and offset >= total:
                break
        return listings, len(listings)

    # -- detail ------------------------------------------------------------
    def _fetch_detail(self, item: Dict[str, Any], result: ExtractionResult) -> Optional[Dict[str, Any]]:
        path = item.get("externalPath")
        if not path:
            return None
        try:
            payload = self.client.get_json(self.api + path, headers={"Referer": f"https://{self.host}/{self.site}"})
            return payload.get("jobPostingInfo", payload)
        except HttpError:
            return None

    # -- mapping -----------------------------------------------------------
    def _to_posting(self, item: Dict[str, Any], detail: Dict[str, Any]) -> JobPosting:
        external_path = item.get("externalPath", "")
        job_url = detail.get("externalUrl") or f"https://{self.host}/{self.site}{external_path}"
        # A posting may list several countries. Prefer the India location so an
        # India-available role is represented as such, and so any posting with
        # no India location at all is caught by the caller's gate.
        primary = detail.get("location") or item.get("locationsText", "")
        additional = [str(a) for a in (detail.get("additionalLocations") or []) if a]
        location = next(
            (loc for loc in [primary, *additional] if is_india_location(loc)), primary
        )
        city, state, country = parse_location(location)
        description = clean_html(detail.get("jobDescription", ""))
        requisition = (
            detail.get("jobReqId")
            or (item.get("bulletFields") or [""])[0]
            or str(detail.get("jobPostingId", ""))
        )
        employment = normalize_employment_type(detail.get("timeType"), description[:600])
        arrangement = map_workplace_type(detail.get("remoteType")) or infer_work_arrangement(
            location, description[:1500]
        )
        min_sal, max_sal, currency = extract_salary(description)
        can_apply = detail.get("canApply")
        status = "Active" if can_apply is not False else "Closed"

        posting = self.base_posting(
            job_id=str(requisition or ""),
            job_title=detail.get("title") or item.get("title", ""),
            job_description=description,
            job_category=detail.get("jobFamilyGroup") or "",
            business_division=detail.get("jobFamily") or detail.get("businessUnit") or "",
            job_location=location,
            city=city,
            state=state,
            country=country or ("India" if is_india_location(location) else ""),
            employment_type=employment,
            work_arrangement=arrangement,
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(detail.get("startDate") or detail.get("postedOn")),
            job_status=status,
            job_url=job_url,
        )
        return posting


def _is_india_posting(posting: JobPosting) -> bool:
    """True when the posting's location evidence places it in India."""
    if is_india_location(posting.job_location):
        return True
    return str(posting.country or "").strip().lower() in {"india", "in"}


def _discover_india_filter(facets: List[Dict[str, Any]]) -> Tuple[Optional[str], List[str], str]:
    """Find the facet that scopes results to India.

    Returns ``(facet_parameter_or_locations_key, values, strategy_description)``.
    """
    # Strategy 1: a flat country-level facet whose values include "India".
    for facet in facets:
        for value in facet.get("values") or []:
            if str(value.get("descriptor", "")).strip().lower() == "india" and value.get("id"):
                return facet.get("facetParameter", "locationCountry"), [value["id"]], "country-facet"

    # Strategy 2: the grouped location facet (``locationMainGroup``) exposes a
    # "Sites" (or "Locations") sub-group whose leaf ids are accepted by the
    # ``locations`` filter key.
    for facet in facets:
        if facet.get("facetParameter") != "locationMainGroup":
            continue
        groups = facet.get("values") or []
        for preferred in ("Sites", "Locations"):
            for group in groups:
                if str(group.get("descriptor", "")) != preferred:
                    continue
                ids = [leaf["id"] for leaf in _leaves(group.get("values") or []) if _INDIA_RE.search(str(leaf.get("descriptor", ""))) and leaf.get("id")]
                if ids:
                    return "locations", ids, f"locationMainGroup/{preferred}"
    return None, [], "none"


def _leaves(values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for node in values:
        children = node.get("values") or []
        if children:
            out.extend(_leaves(children))
        else:
            out.append(node)
    return out
