"""SmartRecruiters adapter (``api.smartrecruiters.com``).

SmartRecruiters exposes a documented public Postings API. We page through every
posting, keep those whose ``location.country`` is India, then fetch the
per-posting detail payload for the full job advertisement.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http_client import HttpError
from ..normalize import (
    clean_html,
    extract_experience,
    extract_salary,
    infer_work_arrangement,
    make_record_hash,
    map_workplace_type,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

PAGE_SIZE = 100
API = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersExtractor(BaseExtractor):
    platform = "smartrecruiters"

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
        india: List[Dict[str, Any]] = []
        total: Optional[int] = None
        offset = 0
        while True:
            url = f"{API}/{self.slug}/postings?limit={PAGE_SIZE}&offset={offset}"
            try:
                page = self.client.get_json(url)
            except HttpError as exc:
                result.errors.append(f"listing failed at offset {offset}: {exc}")
                break
            if total is None:
                total = page.get("totalFound") or 0
            batch = page.get("content", []) or []
            if not batch:
                break
            india.extend(item for item in batch if _is_india(item))
            offset += PAGE_SIZE
            if offset >= total:
                break

        result.portal_total = len(india)
        result.portal_total_scope = (
            f"India postings found while scanning all {total if total is not None else '?'} portal postings"
        )
        result.notes.append(
            f"Scanned {total} postings; {len(india)} located in India"
        )

        india = self._cap(india)
        details = self.client.map_concurrent(
            india, lambda item: self._fetch_detail(item), workers=self.workers
        )

        for item, detail in zip(india, details):
            posting = self._to_posting(item, detail)
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

    def _fetch_detail(self, item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            return self.client.get_json(f"{API}/{self.slug}/postings/{item['id']}")
        except HttpError:
            return None

    def _to_posting(self, item: Dict[str, Any], detail: Optional[Dict[str, Any]]) -> Any:
        loc = item.get("location", {}) or {}
        raw_location = ", ".join(
            p for p in [loc.get("city"), loc.get("region"), loc.get("country")] if p
        )
        city, state, country = parse_location(loc.get("city", ""))
        if loc.get("region") and not state:
            state = loc.get("region", "")
        country = "India" if str(loc.get("country", "")).lower() in {"in", "india"} else country

        description = _compose_description(detail) or clean_html(item.get("name", ""))
        employment = normalize_employment_type(
            (item.get("typeOfEmployment") or {}).get("label"), description[:600]
        )
        arrangement = map_workplace_type(
            "remote" if loc.get("remote") else ""
        ) or infer_work_arrangement(raw_location, description[:1500])
        min_sal, max_sal, currency = extract_salary(description)
        job_id = item.get("refNumber") or item.get("id", "")
        job_url = item.get("applyUrl") or f"https://jobs.smartrecruiters.com/{self.slug}/{item.get('id','')}"

        posting = self.base_posting(
            job_id=str(job_id),
            job_title=item.get("name", ""),
            job_description=description,
            job_category=(item.get("function") or {}).get("label", ""),
            business_division=(item.get("department") or {}).get("label", ""),
            job_location=raw_location,
            city=city,
            state=state,
            country=country,
            employment_type=employment,
            work_arrangement=arrangement,
            experience_required=extract_experience(description)
            or (item.get("experienceLevel") or {}).get("label", ""),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(item.get("releasedDate")),
            job_status="Active",
            job_url=job_url,
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting


def _is_india(item: Dict[str, Any]) -> bool:
    country = str((item.get("location") or {}).get("country", "")).lower()
    return country in {"in", "ind", "india"}


def _compose_description(detail: Optional[Dict[str, Any]]) -> str:
    if not detail:
        return ""
    job_ad = detail.get("jobAd", {}) or {}
    sections = job_ad.get("sections", {}) or {}
    parts: List[str] = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if isinstance(section, dict):
            text = clean_html(section.get("text", ""))
            if text:
                parts.append(text)
    return "\n\n".join(parts)
