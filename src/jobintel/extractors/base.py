"""Extractor contract shared by every recruitment-platform adapter."""

from __future__ import annotations

import datetime as _dt
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..http_client import HttpClient
from ..schema import JobPosting


@dataclass
class ExtractionResult:
    """Outcome of extracting one employer."""

    company_name: str
    industry: str
    platform: str
    careers_url: str
    postings: List[JobPosting] = field(default_factory=list)
    # Number of India-scoped active postings discoverable on the portal (the
    # coverage denominator). ``None`` when it cannot be established reliably.
    portal_total: Optional[int] = None
    portal_total_scope: str = ""
    notes: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    status: str = "OK"

    @property
    def extracted(self) -> int:
        return len(self.postings)

    @property
    def coverage_pct(self) -> str:
        if not self.portal_total:
            return "unknown"
        return f"{100.0 * self.extracted / self.portal_total:.1f}%"


class BaseExtractor(ABC):
    """Base class for platform adapters.

    Subclasses implement :meth:`extract` and return an :class:`ExtractionResult`.
    """

    platform: str = "base"

    def __init__(
        self,
        company: Dict[str, Any],
        client: HttpClient,
        extraction_date: Optional[str] = None,
        workers: int = 4,
        max_records: Optional[int] = None,
    ):
        self.company = company
        self.client = client
        self.workers = max(1, workers)
        self.max_records = max_records
        self.extraction_date = extraction_date or _dt.date.today().isoformat()
        self._cfg = company.get("config", {}) or {}

    # -- helpers -----------------------------------------------------------
    def base_posting(self, **kwargs: Any) -> JobPosting:
        # When a source does not state a work arrangement we default to Onsite
        # (India postings are overwhelmingly on-site); Remote/Hybrid are only
        # ever set from an explicit source signal. A posting with no location at
        # all is left blank rather than guessed.
        if not str(kwargs.get("work_arrangement") or "").strip():
            if str(kwargs.get("job_location") or kwargs.get("city") or "").strip():
                kwargs["work_arrangement"] = "Onsite"
        posting = JobPosting(
            company_name=self.company["company_name"],
            industry=self.company.get("industry", ""),
            extraction_date=self.extraction_date,
            source_platform=self.platform,
            source_company_slug=self.company.get("slug")
            or self.company["company_name"].lower(),
            **kwargs,
        )
        return posting

    def _cap(self, items: List[Any]) -> List[Any]:
        if self.max_records is not None:
            return items[: self.max_records]
        return items

    @abstractmethod
    def extract(self) -> ExtractionResult:
        """Retrieve every accessible active India posting for this employer."""
