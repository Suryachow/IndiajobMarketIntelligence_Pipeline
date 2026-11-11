"""jobintel - a reusable, source-agnostic pipeline for employer job data."""

from .schema import CSV_FIELDS, JobPosting, validate  # noqa: F401

__all__ = ["JobPosting", "CSV_FIELDS", "validate"]
__version__ = "1.0.0"
