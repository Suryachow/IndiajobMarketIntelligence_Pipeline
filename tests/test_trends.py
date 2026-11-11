"""Unit tests for hiring-trend aggregation."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.schema import JobPosting  # noqa: E402
from jobintel.trends import build_trends_markdown, compute_trends  # noqa: E402


def _posting(company, title, family, state, salary=""):
    return JobPosting(
        company_name=company,
        industry="Technology",
        job_title=title,
        job_family=family,
        state=state,
        min_salary=salary,
        currency="INR" if salary else "",
        source_platform="workday",
    )


class TestComputeTrends(unittest.TestCase):
    def setUp(self):
        self.jobs = [
            _posting("Acme", "Engineer", "Engineering", "Karnataka", "500000"),
            _posting("Acme", "Engineer II", "Engineering", "Karnataka", "800000"),
            _posting("Beta", "Designer", "Design", "Maharashtra"),
        ]
        self.trends = compute_trends(self.jobs)

    def test_totals_and_salary_coverage(self):
        self.assertEqual(self.trends["total_postings"], 3)
        self.assertEqual(self.trends["with_salary"], 2)

    def test_company_breakdown_is_sorted_by_count(self):
        rows = self.trends["by_company"]["rows"]
        self.assertEqual(rows[0]["value"], "Acme")
        self.assertEqual(rows[0]["count"], 2)
        self.assertAlmostEqual(rows[0]["share"], 2 / 3, places=3)

    def test_location_breakdown(self):
        values = {r["value"]: r["count"] for r in self.trends["by_location"]["rows"]}
        self.assertEqual(values["Karnataka"], 2)
        self.assertEqual(values["Maharashtra"], 1)

    def test_salary_stats(self):
        salary = self.trends["salary"]
        self.assertEqual(salary["count"], 2)
        self.assertEqual(salary["min"], 500000)
        self.assertEqual(salary["max"], 800000)
        self.assertEqual(salary["median"], 650000)
        self.assertEqual(salary["currency"], "INR")

    def test_markdown_renders_tables(self):
        markdown = build_trends_markdown(self.trends, "2026-11-11")
        self.assertIn("# Hiring Trends — 2026-11-11", markdown)
        self.assertIn("## Postings by Company", markdown)
        self.assertIn("| Acme | 2 | 66.7% |", markdown)


if __name__ == "__main__":
    unittest.main()
