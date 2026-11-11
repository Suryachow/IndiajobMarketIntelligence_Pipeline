"""Accenture adapter (public ``findjobs`` search API).

Accenture's India career site (``accenture.com/in-en/careers/jobsearch``) is a
JavaScript application, but it is driven by a **public** Elastic-backed search
endpoint, ``POST /api/accenture/elastic/findjobs``, which accepts a
``multipart/form-data`` body and returns the full India corpus as JSON --
title, location, description, family, career level and posting date included.

Using the API (rather than rendering the page and scraping DOM) gives complete
coverage, real ``totalHits`` for the coverage denominator, and one HTTP request
per 50 postings. Only India-scoped, active postings are kept.
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
    map_workplace_type,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
)
from ..schema import validate
from .base import BaseExtractor, ExtractionResult

API = "https://www.accenture.com/api/accenture/elastic/findjobs"
JOB_URL_TEMPLATE = "https://www.accenture.com/{locale}/careers/jobdetails?id={job_id}"
PAGE_SIZE = 50
# The endpoint reports an Elasticsearch-style cap: ``totalHits.total`` never
# exceeds 10000 regardless of the real number of postings.
HITS_CAP = 10000
HEADERS = {
    "Origin": "https://www.accenture.com",
    "Referer": "https://www.accenture.com/in-en/careers/jobsearch",
}


class AccentureExtractor(BaseExtractor):
    platform = "accenture"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.locale = self._cfg.get("locale") or "in-en"
        self.country = self._cfg.get("country") or "India"

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            payload = self._search(0)
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"findjobs endpoint unreachable: {exc}")
            return result

        total = _total_hits(payload)
        result.portal_total = min(total, HITS_CAP) if total else None
        result.portal_total_scope = (
            f"{self.country} postings reported by the Accenture findjobs API"
            + (f" (capped at {HITS_CAP:,})" if total > HITS_CAP else "")
        )

        records: List[Dict[str, Any]] = list(payload.get("data") or [])
        target = self.max_records if self.max_records is not None else min(total or HITS_CAP, HITS_CAP)
        start = PAGE_SIZE
        while len(records) < target and records:
            try:
                page = self._search(start)
            except HttpError as exc:
                result.notes.append(f"pagination stopped at startIndex={start}: {exc}")
                break
            batch = page.get("data") or []
            if not batch:
                break
            records.extend(batch)
            start += PAGE_SIZE

        records = self._cap(records)
        india = [rec for rec in records if _is_india(rec)]
        if len(india) != len(records):
            result.notes.append(
                f"dropped {len(records) - len(india)} posting(s) not located in {self.country}"
            )
        result.notes.append(f"findjobs API returned {len(records)} posting(s) for {self.country}")

        for rec in india:
            posting = self._to_posting(rec)
            problems = validate(posting)
            if problems:
                result.errors.append(f"{posting.job_id or '?'}: {'; '.join(problems)}")
                continue
            result.postings.append(posting)

        if result.errors and not result.postings:
            result.status = "FAILED"
        elif result.errors:
            result.status = "PARTIAL"
        return result

    # -- internals ---------------------------------------------------------
    def _search(self, start_index: int) -> Dict[str, Any]:
        fields = {
            "startIndex": str(start_index),
            "maxResultSize": str(PAGE_SIZE),
            "jobKeyword": "",
            "jobCountry": self.country,
            "jobLanguage": "en",
            "countrySite": self.locale,
            "sortBy": "2",
            "searchType": "vectorSearch",
            "enableQueryBoost": "true",
            "minScore": "0.6",
            "getFeedbackJudgmentEnabled": "true",
        }
        payload = self.client.post_multipart_json(API, fields, headers=HEADERS)
        return payload if isinstance(payload, dict) else {}

    def _to_posting(self, rec: Dict[str, Any]) -> Any:
        job_id = str(rec.get("requisitionId") or rec.get("guid") or "")
        location = _first(rec.get("location")) or rec.get("feedCity", "")
        city, state, country = parse_location(location)
        city = city or _first(rec.get("feedCity"))
        country = country or ("India" if str(rec.get("country", "")).lower() == "india" else "")
        description = rec.get("jobDescriptionClean") or clean_html(rec.get("jobDescription", ""))
        min_sal, max_sal, currency = extract_salary(description)
        remote = _first(rec.get("remoteType"))

        posting = self.base_posting(
            job_id=job_id,
            job_title=rec.get("title", ""),
            job_description=description,
            job_category=_first(rec.get("jobFamilyGroup"), rec.get("areaOfInterest"), rec.get("function")),
            business_division=rec.get("businessArea", ""),
            job_location=location,
            city=city,
            state=state,
            country=country,
            employment_type=normalize_employment_type(
                rec.get("employeeType"), rec.get("jobTypeDescription"), rec.get("jobScheduleDescription")
            ),
            work_arrangement=map_workplace_type(remote)
            or infer_work_arrangement(location, description[:1500]),
            experience_required=extract_experience(rec.get("yearsOfExperience"), description),
            min_salary=min_sal,
            max_salary=max_sal,
            currency=currency,
            posting_date=parse_posting_date(rec.get("updateDate"))
            or parse_posting_date(rec.get("postedDateText")),
            job_status="Active",
            job_url=self._job_url(rec, job_id),
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting

    def _job_url(self, rec: Dict[str, Any], job_id: str) -> str:
        template = rec.get("jobDetailUrl") or ""
        if template:
            url = template.replace("{0}", self.locale)
            if "{" not in url:
                return url
        return JOB_URL_TEMPLATE.format(locale=self.locale, job_id=job_id)


def _total_hits(payload: Dict[str, Any]) -> int:
    hits = payload.get("totalHits")
    if isinstance(hits, dict):
        try:
            return int(hits.get("total") or 0)
        except (TypeError, ValueError):
            return 0
    try:
        return int(hits or 0)
    except (TypeError, ValueError):
        return 0


def _is_india(rec: Dict[str, Any]) -> bool:
    """Keep a record only when it is (or is most likely) located in India.

    The search is already scoped with ``jobCountry=India``; this is a belt-and-
    braces check against the record's own country/location fields.
    """
    rec_country = str(rec.get("country", "")).strip().lower()
    if rec_country in {"india", "in", "ind"}:
        return True
    if rec_country:
        return False
    return is_india_location(_first(rec.get("location")))


def _first(*values: Optional[Any]) -> str:
    """First non-empty value; list values are joined."""
    for value in values:
        if not value:
            continue
        if isinstance(value, (list, tuple)):
            parts = [str(v).strip() for v in value if str(v).strip()]
            if parts:
                return ", ".join(parts)
            continue
        return str(value).strip()
    return ""
