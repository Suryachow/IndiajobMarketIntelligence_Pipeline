"""Workable adapter (``apply.workable.com``).

Workable's public careers widget API exposes every published posting for an
account in one response. Unlike Ashby, the widget payload carries no
compensation block, so pay is only picked up when an account publishes an
explicit salary range in the posting text.

Location data arrives as a flat ``city``/``state``/``country`` triple plus a
``locations`` array; both are consulted so a bare city (``Bengaluru``) still
resolves to India.
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
    normalize_employment_type,
    parse_location,
    parse_posting_date,
    salary_from_structured,
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

API = "https://apply.workable.com/api/v1/widget/accounts"


class WorkableExtractor(BaseExtractor):
    platform = "workable"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.account = self._cfg["account"]

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            payload = self.client.get_json(f"{API}/{self.account}?details=true")
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"widget endpoint unreachable: {exc}")
            return result

        all_jobs: List[Dict[str, Any]] = payload.get("jobs", []) or []
        india = [j for j in all_jobs if _is_india(j)]
        result.notes.append(f"Account returned {len(all_jobs)} postings; {len(india)} located in India")

        result.portal_total = len(india)
        result.portal_total_scope = "India postings returned by the full widget payload"
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
        description = clean_html(item.get("description", ""))
        # Workable's widget payload exposes no compensation block today; read a
        # structured range if an account ever supplies one, else fall back to
        # an explicit range published in the posting text.
        min_sal, max_sal, currency = salary_from_structured(item.get("salary"))
        if not (min_sal or max_sal):
            min_sal, max_sal, currency = extract_salary(description)

        posting = self.base_posting(
            job_id=str(item.get("code") or item.get("shortcode") or item.get("id", "")),
            job_title=item.get("title", ""),
            job_description=description,
            job_category=item.get("department", "") or item.get("function", ""),
            business_division=item.get("function", "") if item.get("department") else "",
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(
                item.get("employment_type"), description[:600]
            ),
            work_arrangement=_work_arrangement(item, location, description),
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(
                item.get("published_on") or item.get("created_at")
            ),
            job_status="Active",
            job_url=item.get("url") or item.get("shortlink") or item.get("application_url", ""),
        )
        return posting


def _locations(item: Dict[str, Any]) -> List[str]:
    """Location strings, preferring the structured ``locations`` array."""
    values: List[str] = []
    for entry in item.get("locations") or []:
        if not isinstance(entry, dict) or entry.get("hidden"):
            continue
        parts = [entry.get("city"), entry.get("region"), entry.get("country")]
        values.append(", ".join(str(p) for p in parts if p))
    flat = [item.get("city"), item.get("state"), item.get("country")]
    values.append(", ".join(str(p) for p in flat if p))
    return [v for v in values if v]


def _is_india(item: Dict[str, Any]) -> bool:
    return any(is_india_location(loc) for loc in _locations(item))


def _location_text(item: Dict[str, Any]) -> str:
    locations = _locations(item)
    if not locations:
        return ""
    for loc in locations:
        if is_india_location(loc):
            return loc
    return locations[0]


def _work_arrangement(item: Dict[str, Any], location: str, description: str) -> str:
    if item.get("telecommuting"):
        return "Remote"
    return infer_work_arrangement(location, description[:1500])
