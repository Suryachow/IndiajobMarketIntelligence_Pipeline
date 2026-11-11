"""Wipro adapter (SAP SuccessFactors **jobs2web / RMK**).

``careers.wipro.com`` is an Angular single-page app whose job pages publish
schema.org **microdata** (``itemprop=``) but no JSON-LD. Its ``sitemap.xml`` is
*unreliable*: the listed URLs mostly redirect to an error page, so sitemap
discovery alone finds almost nothing.

Instead we use the site's own search backend --
``POST /services/recruiting/v1/jobs`` -- which returns the complete India index
(``id``, title, cities, states, country, start/end dates, currency), ten postings
at a time, with ``totalJobs`` as the coverage denominator. Each posting's
description is then read from the schema.org microdata on its canonical job page
(``/job/{urlTitle}/{id}-{locale}/``).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..http_client import HttpError
from ..jsonld import extract_meta, extract_microdata
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

API = "https://careers.wipro.com/services/recruiting/v1/jobs"
JOB_URL = "https://careers.wipro.com/job/{title}/{job_id}-{locale}/"
PAGE_SIZE = 10
DEFAULT_LOCALE = "en_US"


class WiproExtractor(BaseExtractor):
    platform = "wipro"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.locale = self._cfg.get("locale") or DEFAULT_LOCALE
        self.location = self._cfg.get("location") or "india"
        self.enrich = bool(self._cfg.get("enrich_details", True))

    def extract(self) -> ExtractionResult:
        result = ExtractionResult(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            platform=self.platform,
            careers_url=self.company.get("careers_url", ""),
        )
        try:
            first = self._search(0)
        except HttpError as exc:
            result.status = "FAILED"
            result.errors.append(f"search endpoint unreachable: {exc}")
            return result

        total = _as_int(first.get("totalJobs"))
        result.portal_total = total or None
        result.portal_total_scope = (
            "India postings reported by the Wipro careers search API"
        )

        records = _records(first)
        target = self.max_records if self.max_records is not None else (total or len(records))
        page = 1
        while records and len(records) < target:
            try:
                payload = self._search(page)
            except HttpError as exc:
                result.notes.append(f"pagination stopped at page {page}: {exc}")
                break
            batch = _records(payload)
            if not batch:
                break
            records.extend(batch)
            page += 1

        records = self._cap(records)
        result.notes.append(f"search API returned {len(records)} India posting(s)")

        postings: List[Any] = []
        for rec in records:
            posting = self._to_posting(rec)
            if posting is None:
                continue
            postings.append(posting)

        if self.enrich:
            self._enrich(postings)
            result.notes.append("descriptions read from each posting's schema.org microdata")

        for posting in postings:
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
    def _search(self, page_number: int) -> Dict[str, Any]:
        payload = {
            "locale": self.locale,
            "pageNumber": page_number,
            "sortBy": "",
            "keywords": "",
            "location": self.location,
            "facetFilters": {},
            "brand": "",
            "skills": [],
            "categoryId": 0,
            "alertId": "",
            "rcmCandidateId": "",
        }
        data = self.client.post_json(API, payload, headers={"Accept": "application/json"})
        return data if isinstance(data, dict) else {}

    def _to_posting(self, rec: Dict[str, Any]) -> Optional[Any]:
        job_id = str(rec.get("id") or "").strip()
        title = _text(rec.get("unifiedStandardTitle")) or _text(rec.get("urlTitle"))
        url_title = _text(rec.get("urlTitle")) or _text(rec.get("unifiedUrlTitle"))
        if not job_id or not title:
            return None

        cities = _clean_list(rec.get("sfstd_jobLocation_obj"))
        states = _clean_list(rec.get("jobLocationState"))
        countries = _clean_list(rec.get("jobLocationCountry"))
        short_locations = _clean_list(rec.get("jobLocationShort"))
        country = countries[0] if countries else ""
        city = cities[0] if cities else ""
        state = states[0] if states else ""
        location_text = ", ".join(short_locations) or ", ".join(filter(None, (city, state, country)))
        if not country and is_india_location(location_text):
            country = "India"

        job_url = JOB_URL.format(title=url_title or job_id, job_id=job_id, locale=self.locale)

        posting = self.base_posting(
            job_id=job_id,
            job_title=title,
            job_description="",
            job_category="",
            business_division="",
            job_location=location_text,
            city=city,
            state=state,
            country=country or ("India" if is_india_location(location_text) else ""),
            employment_type=normalize_employment_type(_text(rec.get("unifiedStandardTitle"))),
            work_arrangement=infer_work_arrangement(location_text),
            experience_required="",
            min_salary="",
            max_salary="",
            currency=(_clean_list(rec.get("currency")) or [""])[0],
            posting_date=parse_posting_date(_text(rec.get("unifiedStandardStart"))),
            job_status="Active",
            job_url=job_url,
        )
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting

    def _enrich(self, postings: List[Any]) -> None:
        """Fill descriptions from each posting's schema.org microdata page."""
        self.client.map_concurrent(postings, self._enrich_one, workers=self.workers)

    def _enrich_one(self, posting: Any) -> Any:
        html = self.client.get_page(posting.job_url)
        fields = extract_microdata(html)
        if not fields:
            fields = extract_meta(html)
        description = clean_html(fields.get("description", ""))
        if description:
            posting.job_description = description
        posting.experience_required = (
            posting.experience_required or fields.get("experience", "") or extract_experience(description)
        )
        if fields.get("employment_type"):
            posting.employment_type = normalize_employment_type(
                fields["employment_type"].replace("_", " "), description[:600]
            ) or posting.employment_type
        if fields.get("location_text"):
            city, state, country = parse_location(fields["location_text"])
            posting.city = posting.city or city
            posting.state = posting.state or state
            posting.country = posting.country or country
        min_sal, max_sal, currency = extract_salary(description)
        if min_sal:
            posting.min_salary, posting.max_salary, posting.currency = min_sal, max_sal, currency
        posting.source_record_hash = make_record_hash(
            posting.source_company_slug, posting.job_id, posting.job_title, posting.job_location
        )
        return posting


def _records(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for item in payload.get("jobSearchResult") or []:
        if isinstance(item, dict):
            record = item.get("response") or item
            if isinstance(record, dict):
                out.append(record)
    return out


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v).strip() for v in value if str(v).strip())
    return str(value).strip() if value is not None else ""


def _clean_list(value: Any) -> List[str]:
    """Split a list (or ``<br/>``-delimited string) into clean, non-empty parts."""
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    out: List[str] = []
    for item in items:
        cleaned = clean_html(str(item)).strip(" ,")
        if cleaned:
            out.append(cleaned)
    return out
