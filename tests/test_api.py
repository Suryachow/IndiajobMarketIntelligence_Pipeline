"""Unit tests for the dataset query layer behind the lightweight API."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.api import Dataset  # noqa: E402
from jobintel.reporting import write_master_csv  # noqa: E402
from jobintel.schema import JobPosting  # noqa: E402


def _posting(job_id, company, title, location, family="Engineering", seniority="Senior", salary=""):
    return JobPosting(
        company_name=company,
        industry="Technology",
        job_id=job_id,
        job_title=title,
        job_description=f"{title} role",
        job_location=location,
        state="Karnataka",
        city="Bengaluru",
        job_family=family,
        seniority_level=seniority,
        work_arrangement="Onsite",
        min_salary=salary,
        currency="INR" if salary else "",
        job_status="Active",
        job_url=f"https://example.test/{job_id}",
        extraction_date="2026-11-11",
        source_platform="workday",
        source_company_slug=company.lower(),
        source_record_hash=f"hash-{job_id}",
    )


class TestDatasetQuery(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            _posting("R1", "Acme", "Senior Backend Engineer", "Bengaluru, India", salary="1200000"),
            _posting("R2", "Acme", "Product Designer", "Bengaluru, India", family="Design", salary=""),
            _posting("R3", "Beta", "Data Scientist", "Pune, India", family="Data & Analytics", seniority="Mid", salary="900000"),
        ]
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.csv = os.path.join(self.tmp.name, "master.csv")
        write_master_csv(self.jobs, self.csv)
        self.dataset = Dataset.load(self.csv)

    def test_free_text_search(self):
        rows, total = self.dataset.query({"q": ["engineer"]})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["job_id"], "R1")

    def test_company_filter_and_paging(self):
        rows, total = self.dataset.query({"company": ["Acme"], "limit": ["1"]})
        self.assertEqual(total, 2)
        self.assertEqual(len(rows), 1)
        rows2, _ = self.dataset.query({"company": ["Acme"], "limit": ["1"], "offset": ["1"]})
        self.assertNotEqual(rows[0]["job_id"], rows2[0]["job_id"])

    def test_has_salary_filter(self):
        _, total = self.dataset.query({"has_salary": ["1"]})
        self.assertEqual(total, 2)

    def test_family_filter_is_case_insensitive(self):
        rows, total = self.dataset.query({"family": ["design"]})
        self.assertEqual(total, 1)
        self.assertEqual(rows[0]["job_id"], "R2")

    def test_sorting(self):
        rows, _ = self.dataset.query({"sort": ["-job_title"], "limit": ["3"]})
        titles = [r["job_title"] for r in rows]
        self.assertEqual(titles, sorted(titles, reverse=True))

    def test_find_by_id_and_qualified_key(self):
        self.assertEqual(self.dataset.find("R3")["company_name"], "Beta")
        self.assertEqual(self.dataset.find("acme:R1")["job_title"], "Senior Backend Engineer")
        self.assertIsNone(self.dataset.find("nope"))

    def test_stats_reconcile_with_dataset(self):
        stats = self.dataset.stats()
        self.assertEqual(stats["total_postings"], 3)
        self.assertEqual(stats["with_salary"], 2)

    def test_options_are_unique_and_sorted(self):
        options = self.dataset.options()
        self.assertEqual(options["company"], ["Acme", "Beta"])
        self.assertEqual(options["location"], ["Bengaluru, India", "Pune, India"])
        self.assertEqual(options["state"], ["Karnataka"])
        self.assertEqual(options["industry"], ["Technology"])


if __name__ == "__main__":
    unittest.main()
