"""Greenhouse adapter (``boards-api.greenhouse.io``).

Greenhouse's public job-board API returns the whole board in a single response
(optionally with full HTML content), so no pagination is required. We keep the
postings whose location resolves to India (Greenhouse often publishes only a
city/state, so :func:`is_india_location` does the heavy lifting).
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

API = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseExtractor(BaseExtractor):
    platform = "greenhouse"

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
            payload = self.client.get_json(f"{API}/{self.board}/jobs?content=true")
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"board endpoint unreachable: {exc}")
            return result

        all_jobs: List[Dict[str, Any]] = payload.get("jobs", []) or []
        india = [j for j in all_jobs if is_india_location((j.get("location") or {}).get("name", ""))]
        result.notes.append(f"Board returned {len(all_jobs)} postings; {len(india)} located in India")

        india = self._cap(india)
        result.portal_total = len(india)
        result.portal_total_scope = "India postings returned by the full board payload"

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
        location = (item.get("location") or {}).get("name", "")
        city, state, country = parse_location(location)
        if not country and is_india_location(location):
            country = "India"
        departments = [d.get("name", "") for d in item.get("departments", []) or []]
        description = clean_html(item.get("content", ""))
        # Greenhouse exposes pay via ``pay_input_ranges`` (job detail) when the
        # employer enables pay transparency; fall back to a range in the text.
        min_sal, max_sal, currency = salary_from_structured(item.get("pay_input_ranges"))
        if not (min_sal or max_sal):
            min_sal, max_sal, currency = extract_salary(description)

        posting = self.base_posting(
            job_id=str(item.get("id", "")),
            job_title=item.get("title", ""),
            job_description=description,
            job_category=departments[0] if departments else "",
            business_division=" / ".join(departments[1:]) if len(departments) > 1 else "",
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(description[:600]),
            work_arrangement=infer_work_arrangement(location, description[:1500]),
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(item.get("updated_at") or item.get("first_published")),
            job_status="Active",
            job_url=item.get("absolute_url", ""),
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting
