"""Amazon adapter (``www.amazon.jobs``).

Amazon's official careers site is a proprietary platform. Its public job-search
endpoint (``/en/search.json``) returns a paged JSON payload that already embeds
the full description and qualifications, so a single pass is sufficient - no
per-job detail request is required. We scope to India with ``country=IND``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

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
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

PAGE_SIZE = 100
BASE = "https://www.amazon.jobs"


class AmazonExtractor(BaseExtractor):
    platform = "amazon"

    def extract(self) -> ExtractionResult:
        country = self._cfg.get("country", "IND")
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        listings: List[Dict[str, Any]] = []
        total: Optional[int] = None
        offset = 0
        while True:
            url = (
                f"{BASE}/en/search.json?country={country}"
                f"&result_limit={PAGE_SIZE}&offset={offset}&sort=recent"
            )
            try:
                page = self.client.get_json(url)
            except HttpError as exc:
                result.errors.append(f"listing failed at offset {offset}: {exc}")
                break
            if total is None:
                total = page.get("hits") or 0
            batch = page.get("jobs", []) or []
            if not batch:
                break
            listings.extend(batch)
            offset += PAGE_SIZE
            if offset >= total:
                break

        total = int(total or len(listings))

        result.portal_total = total
        result.portal_total_scope = f"amazon.jobs search hits for country={country}"
        result.notes.append(f"Listed {len(listings)} postings (portal reports {total})")

        for item in self._cap(listings):
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
        location = item.get("normalized_location") or item.get("location", "")
        city, state, country = parse_location(item.get("city") or location)
        if not country and is_india_location(location):
            country = "India"
        description = _compose_description(item)
        employment = normalize_employment_type(item.get("job_schedule_type"), description[:600])
        # Amazon marks remote roles with a "- Virtual" suffix on the raw location
        # (the normalized location drops it), so match on the raw value.
        arrangement = infer_work_arrangement(
            item.get("location") or location, description[:1500]
        )
        min_sal, max_sal, currency = extract_salary(description)
        job_path = item.get("job_path", "")
        job_url = f"{BASE}{job_path}" if job_path else item.get("url_next_step", "")

        posting = self.base_posting(
            job_id=str(item.get("id_icims") or item.get("id") or ""),
            job_title=item.get("title", ""),
            job_description=description,
            job_category=item.get("job_category", ""),
            business_division=item.get("job_family") or item.get("business_category", ""),
            job_location=item.get("location") or location,
            city=city,
            state=state,
            country=country,
            employment_type=employment,
            work_arrangement=arrangement,
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(item.get("posted_date")),
            job_status="Active",
            job_url=job_url,
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting


def _compose_description(item: Dict[str, Any]) -> str:
    parts: List[str] = []
    for key, heading in (
        ("description", "DESCRIPTION"),
        ("basic_qualifications", "BASIC QUALIFICATIONS"),
        ("preferred_qualifications", "PREFERRED QUALIFICATIONS"),
    ):
        value = clean_html(item.get(key, ""))
        if value:
            parts.append(f"{heading}\n{value}")
    return "\n\n".join(parts)
