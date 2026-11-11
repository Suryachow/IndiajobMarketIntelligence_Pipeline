"""Lever adapter (``api.lever.co``).

Lever's public postings API returns every live posting (with description) in a
single JSON array. We retain postings whose ``categories.location`` resolves to
India; Lever frequently publishes a bare city name, so the India matcher uses
state/city vocabularies as well as the country name.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

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
    salary_from_structured,
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

API = "https://api.lever.co/v0/postings"


class LeverExtractor(BaseExtractor):
    platform = "lever"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.slug = self._cfg["company"]

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            payload = self.client.get_json(f"{API}/{self.slug}?mode=json")
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"postings endpoint unreachable: {exc}")
            return result

        all_jobs: List[Dict[str, Any]] = payload if isinstance(payload, list) else []
        india = [
            j for j in all_jobs
            if is_india_location((j.get("categories") or {}).get("location", ""))
        ]
        result.notes.append(f"API returned {len(all_jobs)} postings; {len(india)} located in India")

        india = self._cap(india)
        result.portal_total = len(india)
        result.portal_total_scope = "India postings returned by the full postings payload"

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
        categories = item.get("categories") or {}
        location = categories.get("location", "")
        city, state, country = parse_location(location)
        if not country and is_india_location(location):
            country = "India"
        description = item.get("descriptionPlain") or clean_html(item.get("description", ""))
        extra = _lists_text(item)
        if extra:
            description = f"{description}\n\n{extra}".strip()
        # Lever publishes a structured pay range when the employer opts in;
        # fall back to an explicit range in the description text.
        min_sal, max_sal, currency = salary_from_structured(item.get("salaryRange"))
        if not (min_sal or max_sal):
            min_sal, max_sal, currency = extract_salary(description)

        posting = self.base_posting(
            job_id=str(item.get("id", "")),
            job_title=item.get("text", ""),
            job_description=description,
            job_category=categories.get("department") or categories.get("team", ""),
            business_division=categories.get("team", ""),
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(
                categories.get("commitment"), description[:600]
            ),
            work_arrangement=map_workplace_type(item.get("workplaceType"))
            or infer_work_arrangement(location, description[:1500]),
            experience_required=extract_experience(description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=_from_millis(item.get("createdAt")) or parse_posting_date(item.get("createdAt")),
            job_status="Active",
            job_url=item.get("hostedUrl", ""),
        )
        return posting


def _from_millis(value: Any) -> str:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return _dt.datetime.utcfromtimestamp(millis / 1000.0).date().isoformat()


def _lists_text(item: Dict[str, Any]) -> str:
    chunks: List[str] = []
    for block in item.get("lists", []) or []:
        heading = clean_html(block.get("text", ""))
        content = clean_html(block.get("content", ""))
        if heading or content:
            chunks.append(f"{heading}\n{content}".strip())
    additional = clean_html(item.get("additional", ""))
    if additional:
        chunks.append(additional)
    return "\n\n".join(chunks)
