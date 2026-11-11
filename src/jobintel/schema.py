"""Canonical job-posting data model and CSV schema.

The whole pipeline converges on :class:`JobPosting`. Every extractor, regardless
of the source recruitment platform, must emit ``JobPosting`` objects so that the
consolidated dataset is schema-consistent across employers.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List

# Ordered list of columns written to ``india_active_jobs_master.csv``.
# The first block mirrors the fields requested in the assignment brief; the
# second block adds derived analytics and source platform information used by
# the public dataset. An empty cell ("") is emitted for a genuinely missing
# value - we never fabricate data. The one explicit label we do write is
# :data:`SALARY_NOT_PUBLISHED` for the salary columns, so that "no amount
# disclosed" is visible rather than looking like a parsing gap.
CSV_FIELDS: List[str] = [
    "company_name",
    "industry",
    "job_id",
    "job_title",
    "job_description",
    "job_category",
    "business_division",
    "job_location",
    "city",
    "state",
    "country",
    "employment_type",
    "work_arrangement",
    "experience_required",
    "min_salary",
    "max_salary",
    "currency",
    "posting_date",
    "job_status",
    "job_url",
    "extraction_date",
    # --- derived analytics columns -----------------------------------------
    "job_family",
    "seniority_level",
    # --- lineage / operational columns -------------------------------------
    "source_platform",
]

# Columns that must always be populated for a record to be considered valid.
REQUIRED_FIELDS: List[str] = [
    "company_name",
    "industry",
    "job_title",
    "job_status",
    "job_url",
    "extraction_date",
]

# Fields the assignment lists as mandatory *where published* (i.e. they may be
# blank, but a blank value must be a genuine absence at source, not a bug).
CONDITIONALLY_REQUIRED: List[str] = [
    "job_location",
    "job_description",
]

# Placeholder written to ``min_salary`` / ``max_salary`` when the source
# publishes no amount. It is an explicit label, not a fabricated figure.
SALARY_NOT_PUBLISHED = "Not published"


@dataclass
class JobPosting:
    """A single, standardized, active job posting."""

    company_name: str
    industry: str
    job_id: str = ""
    job_title: str = ""
    job_description: str = ""
    job_category: str = ""
    business_division: str = ""
    job_location: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    employment_type: str = ""
    work_arrangement: str = ""
    experience_required: str = ""
    min_salary: str = ""
    max_salary: str = ""
    currency: str = ""
    posting_date: str = ""
    job_status: str = "Active"
    job_url: str = ""
    extraction_date: str = ""
    job_family: str = ""
    seniority_level: str = ""
    source_platform: str = ""
    source_company_slug: str = ""
    source_record_hash: str = ""

    def to_row(self) -> Dict[str, Any]:
        """Return a CSV-ready dict containing exactly ``CSV_FIELDS`` keys."""
        raw = asdict(self)
        row = {k: _cell(raw.get(k, "")) for k in CSV_FIELDS}
        if not row["min_salary"] and not row["max_salary"]:
            row["min_salary"] = SALARY_NOT_PUBLISHED
            row["max_salary"] = SALARY_NOT_PUBLISHED
        return row


def _cell(value: Any) -> str:
    """Coerce a value to a clean CSV cell. ``None`` and empty become ``''``."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value)


def has_salary(min_salary: Any, max_salary: Any) -> bool:
    """True when the row carries a real published amount.

    ``SALARY_NOT_PUBLISHED`` (and any blank) count as "no amount disclosed".
    """
    for value in (min_salary, max_salary):
        text = _cell(value)
        if text and text.lower() != SALARY_NOT_PUBLISHED.lower():
            return True
    return False


def validate(posting: JobPosting) -> List[str]:
    """Return a list of validation problems (empty list == valid)."""
    problems: List[str] = []
    for f in REQUIRED_FIELDS:
        if not getattr(posting, f, ""):
            problems.append(f"missing required field '{f}'")
    if posting.min_salary or posting.max_salary:
        if not posting.currency:
            problems.append("salary present without currency")
        try:
            lo = float(posting.min_salary) if posting.min_salary else None
            hi = float(posting.max_salary) if posting.max_salary else None
            if lo is not None and hi is not None and lo > hi:
                problems.append("min_salary greater than max_salary")
        except ValueError:
            problems.append("non-numeric salary value")
    if posting.job_url and not posting.job_url.startswith("http"):
        problems.append("job_url is not an absolute URL")
    return problems
