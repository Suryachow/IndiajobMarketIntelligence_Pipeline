from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.reporting import write_jobs_html  # noqa: E402
from jobintel.schema import JobPosting  # noqa: E402


class TestJobsHtml(unittest.TestCase):
    def test_details_include_salary_and_escape_source_values(self):
        posting = JobPosting(
            company_name="Acme <India>",
            industry="Technology",
            job_title="Engineer",
            job_description="Build <safe> systems",
            job_location="Bengaluru",
            min_salary="400000",
            max_salary="600000",
            currency="INR",
            job_url="https://example.test/jobs/1?a=1&b=2",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "jobs.html")
            write_jobs_html([posting], path)
            with open(path, encoding="utf-8") as fh:
                document = fh.read()

        self.assertIn("400000 - 600000 INR", document)
        self.assertIn("Acme &lt;India&gt;", document)
        self.assertIn("Build &lt;safe&gt; systems", document)
        self.assertIn("href=\"https://example.test/jobs/1?a=1&amp;b=2\"", document)
        self.assertIn('href="#job-1"', document)


if __name__ == "__main__":
    unittest.main()
