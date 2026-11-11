"""Unit tests for deduplication and schema validation."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.dedupe import deduplicate  # noqa: E402
from jobintel.schema import JobPosting, validate  # noqa: E402


def _posting(**over):
    base = dict(
        company_name="Acme",
        industry="Technology",
        job_id="REQ-1",
        job_title="Software Engineer",
        job_description="Build things",
        job_location="India, Karnataka, Bengaluru",
        city="Bengaluru",
        country="India",
        job_status="Active",
        job_url="https://example.com/jobs/1",
        extraction_date="2026-11-05",
        source_platform="workday",
    )
    base.update(over)
    return JobPosting(**base)


class TestDedupe(unittest.TestCase):
    def test_exact_hash_collapsed(self):
        unique, stats = deduplicate([_posting(), _posting()])
        self.assertEqual(len(unique), 1)
        self.assertEqual(stats["removed_exact_hash"], 1)

    def test_business_key_collapsed(self):
        a = _posting(job_location="India, Bengaluru")
        b = _posting(job_location="India, Pune")  # same requisition, different site blurb
        unique, stats = deduplicate([a, b])
        self.assertEqual(len(unique), 1)
        self.assertEqual(stats["removed_business_key"], 1)

    def test_fuzzy_key_when_no_id(self):
        # Same role, same city, no requisition id and differently punctuated
        # titles -> collapses to one record via the fuzzy key.
        a = _posting(job_id="", job_title="Senior Data Analyst (Remote)")
        b = _posting(job_id="", job_title="Senior Data Analyst")
        unique, stats = deduplicate([a, b])
        self.assertEqual(len(unique), 1)
        self.assertEqual(stats["removed_fuzzy"], 1)

    def test_fuzzy_requires_same_city(self):
        a = _posting(job_id="", job_title="Data Analyst", city="Bengaluru")
        b = _posting(
            job_id="", job_title="Data Analyst", city="Pune", job_location="Pune, India"
        )
        unique, _ = deduplicate([a, b])
        self.assertEqual(len(unique), 2)

    def test_distinct_kept(self):
        unique, stats = deduplicate([_posting(job_id="A"), _posting(job_id="B", job_title="Designer")])
        self.assertEqual(len(unique), 2)
        self.assertEqual(stats["removed_total"], 0)


class TestValidation(unittest.TestCase):
    def test_valid_record(self):
        self.assertEqual(validate(_posting()), [])

    def test_missing_required(self):
        problems = validate(_posting(job_title="", job_url=""))
        self.assertTrue(any("job_title" in p for p in problems))
        self.assertTrue(any("job_url" in p for p in problems))

    def test_relative_url_rejected(self):
        self.assertTrue(any("absolute URL" in p for p in validate(_posting(job_url="/jobs/1"))))

    def test_salary_without_currency(self):
        self.assertTrue(
            any("currency" in p for p in validate(_posting(min_salary="100", max_salary="200")))
        )

    def test_salary_inverted(self):
        self.assertTrue(
            any("greater than" in p for p in validate(_posting(min_salary="200", max_salary="100", currency="INR")))
        )


if __name__ == "__main__":
    unittest.main()
