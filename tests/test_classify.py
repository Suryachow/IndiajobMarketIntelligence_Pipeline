"""Unit tests for the deterministic job-family / seniority classifier."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.classify import (  # noqa: E402
    classify_posting,
    job_family,
    seniority_level,
)
from jobintel.schema import JobPosting  # noqa: E402


class TestJobFamily(unittest.TestCase):
    def test_title_keywords(self):
        self.assertEqual(job_family("Senior Software Engineer"), "Engineering")
        self.assertEqual(job_family("Data Scientist"), "Data & Analytics")
        self.assertEqual(job_family("HR Business Partner"), "Human Resources")
        self.assertEqual(job_family("Clinical Research Associate"), "Clinical & Medical")
        self.assertEqual(job_family("Regional Sales Manager"), "Sales")

    def test_category_is_fallback(self):
        self.assertEqual(job_family("Member of Technical Staff", "Software Engineering"), "Engineering")

    def test_unknown_falls_back_to_other(self):
        self.assertEqual(job_family("Ninja"), "Other")


class TestSeniority(unittest.TestCase):
    def test_title_keywords_win_in_priority_order(self):
        self.assertEqual(seniority_level("Chief Technology Officer"), "Executive")
        self.assertEqual(seniority_level("Director of Engineering"), "Director")
        self.assertEqual(seniority_level("Senior Manager, Operations"), "Manager")
        self.assertEqual(seniority_level("Principal Engineer"), "Lead")
        self.assertEqual(seniority_level("Senior Analyst"), "Senior")
        self.assertEqual(seniority_level("Software Engineer Intern"), "Internship")

    def test_experience_fallback(self):
        self.assertEqual(seniority_level("Engineer", experience="10+ years"), "Senior")
        self.assertEqual(seniority_level("Engineer", experience="4 years"), "Mid")
        self.assertEqual(seniority_level("Engineer", experience="1 year"), "Entry")

    def test_no_signal_is_blank(self):
        self.assertEqual(seniority_level("Engineer"), "")


class TestClassifyPosting(unittest.TestCase):
    def test_fills_both_columns_in_place(self):
        posting = JobPosting(
            company_name="Acme",
            industry="Technology",
            job_title="Senior Data Engineer",
            job_description="Build pipelines",
        )
        classify_posting(posting)
        self.assertEqual(posting.job_family, "Data & Analytics")
        self.assertEqual(posting.seniority_level, "Senior")


if __name__ == "__main__":
    unittest.main()
