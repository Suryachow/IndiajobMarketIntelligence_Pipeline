"""Ashby adapter (``api.ashbyhq.com``).

Ashby's public job-board API returns every posting for a board in one response.
Passing ``includeCompensation=true`` also returns the employer's published pay
under ``compensation``, but only when the employer opts in for that location -
Ashby sets the tier summary to ``null`` for locations it does not publish.

It is a high-volume JSON API, so the flow mirrors the Greenhouse/Lever adapters:
fetch the board, keep the postings whose location (or any secondary location)
resolves to India, and map each into the canonical ``JobPosting``.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..http_client import HttpError
from ..normalize import (
    clean_html,
    extract_experience,
    extract_salary,
    infer_work_arrangement,
    is_india_location,
    make_record_hash,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
    salary_from_structured,
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

API = "https://api.ashbyhq.com/posting-api/job-board"

# Ashby's ``workplaceType`` vocabulary -> the canonical work-arrangement values.
_WORKPLACE = {
    "remote": "Remote",
    "hybrid": "Hybrid",
    "onsite": "Onsite",
    "on-site": "Onsite",
}


class AshbyExtractor(BaseExtractor):
    platform = "ashby"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.board = self._cfg["board"]

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            payload = self.client.get_json(
                f"{API}/{self.board}?includeCompensation=true"
            )
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"board endpoint unreachable: {exc}")
            return result

        all_jobs: List[Dict[str, Any]] = payload.get("jobs", []) or []
        # Ashby returns both listed and unlisted postings; unlisted ones are
        # internal and must not be treated as publicly advertised.
        listed = [j for j in all_jobs if j.get("isListed", True)]
        india = [j for j in listed if _is_india(j)]
        result.notes.append(
            f"Board returned {len(all_jobs)} postings "
            f"({len(listed)} listed); {len(india)} located in India"
        )

        result.portal_total = len(india)
        result.portal_total_scope = "India postings returned by the full board payload"
        india = self._cap(india)

        for item in india:
            posting = self._to_posting(item)
            problems = validate(posting)
            if problems:
                result.errors.append(f"{posting.job_id}: {'; '.join(problems)}")
                continue
            result.postings.append(posting)

        if result.errors and not result.postings:
            result.status = "FAILED"
        elif result.errors:
            result.status = "PARTIAL"
        return result

    def _to_posting(self, item: Dict[str, Any]) -> Any:
        location = _location_text(item)
        city, state, country = parse_location(location)
        if not country and is_india_location(location):
            country = "India"
        description = item.get("descriptionPlain") or clean_html(item.get("descriptionHtml", ""))
        # Ashby exposes pay as typed components (``summaryComponents``); the
        # salary component carries ``minValue``/``maxValue``/``currencyCode``.
        # Fall back to a range written into the description text.
        compensation = item.get("compensation") or {}
        min_sal, max_sal, currency = salary_from_structured(
            compensation.get("summaryComponents")
        )
        if not (min_sal or max_sal):
            min_sal, max_sal, currency = extract_salary(description)

        posting = self.base_posting(
            job_id=str(item.get("id", "")),
            job_title=item.get("title", ""),
            job_description=description,
            job_category=item.get("department", "") or item.get("team", ""),
            business_division=item.get("team", "") if item.get("department") else "",
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(
                item.get("employmentType"), description[:600]
            ),
            work_arrangement=_work_arrangement(item, location, description),
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(item.get("publishedAt")),
            job_status="Active",
            job_url=item.get("jobUrl", ""),
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting


def _locations(item: Dict[str, Any]) -> List[str]:
    """All location strings for a posting: primary first, then secondaries."""
    values = [item.get("location") or ""]
    for secondary in item.get("secondaryLocations") or []:
        if isinstance(secondary, dict):
            values.append(secondary.get("location") or "")
    return [v for v in values if v]


def _is_india(item: Dict[str, Any]) -> bool:
    if item.get("isRemote") and not item.get("location"):
        return False
    return any(is_india_location(v) for v in _locations(item))


def _location_text(item: Dict[str, Any]) -> str:
    locations = _locations(item)
    primary = locations[0] if locations else ""
    if any(is_india_location(loc) for loc in locations) and not is_india_location(primary):
        # The India location may only appear as a secondary (e.g. "Remote, India").
        return next(loc for loc in locations if is_india_location(loc))
    return primary


def _work_arrangement(item: Dict[str, Any], location: str, description: str) -> str:
    mapped = _WORKPLACE.get(str(item.get("workplaceType", "")).lower())
    if mapped:
        return mapped
    return infer_work_arrangement(location, description[:1500])
