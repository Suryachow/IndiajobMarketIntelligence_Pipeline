"""Unit tests for recruitment-platform detection (stdlib unittest).

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.detect import (  # noqa: E402
    detect_platform,
    guess_company_name,
    is_typed,
    parse_workday,
    platform_config,
)


class TestDetectPlatform(unittest.TestCase):
    def test_workday(self):
        url = "https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays"
        self.assertEqual(detect_platform(url), "workday")
        self.assertTrue(is_typed("workday"))

    def test_lever(self):
        self.assertEqual(detect_platform("https://jobs.lever.co/meesho"), "lever")

    def test_greenhouse(self):
        self.assertEqual(detect_platform("https://boards.greenhouse.io/groww"), "greenhouse")
        self.assertEqual(detect_platform("https://job-boards.greenhouse.io/groww"), "greenhouse")

    def test_smartrecruiters(self):
        self.assertEqual(
            detect_platform("https://jobs.smartrecruiters.com/Sutherland"), "smartrecruiters"
        )

    def test_ashby(self):
        self.assertEqual(detect_platform("https://jobs.ashbyhq.com/openai"), "ashby")
        self.assertTrue(is_typed("ashby"))

    def test_workable(self):
        self.assertEqual(detect_platform("https://apply.workable.com/acme"), "workable")
        self.assertTrue(is_typed("workable"))

    def test_amazon(self):
        self.assertEqual(detect_platform("https://www.amazon.jobs/en/"), "amazon")

    def test_successfactors_behind_branded_domain(self):
        # Sun Pharma serves SAP SuccessFactors behind a vanity domain, so the
        # host alone is not enough - the page markers decide.
        self.assertEqual(detect_platform("https://careers.sunpharma.com"), "unknown")
        html = '<div class="jobDisplayShell" itemscope="itemscope">'
        self.assertEqual(detect_platform("https://careers.sunpharma.com", html), "successfactors")

    def test_unknown(self):
        self.assertEqual(detect_platform("https://careers.example.com/jobs"), "unknown")
        self.assertFalse(is_typed("unknown"))


class TestPlatformConfig(unittest.TestCase):
    def test_parse_workday(self):
        cfg = parse_workday("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite")
        self.assertEqual(cfg, {"tenant": "nvidia", "wd": "wd5", "site": "NVIDIAExternalCareerSite"})

    def test_parse_workday_negative(self):
        self.assertIsNone(parse_workday("https://jobs.lever.co/meesho"))

    def test_slug_config(self):
        self.assertEqual(platform_config("lever", "https://jobs.lever.co/meesho"), {"company": "meesho"})
        self.assertEqual(
            platform_config("greenhouse", "https://boards.greenhouse.io/groww"), {"board": "groww"}
        )
        self.assertEqual(
            platform_config("ashby", "https://jobs.ashbyhq.com/openai"), {"board": "openai"}
        )
        self.assertEqual(
            platform_config("workable", "https://apply.workable.com/acme"), {"account": "acme"}
        )
        self.assertEqual(platform_config("generic", "https://careers.example.com"), {})


class TestGuessCompanyName(unittest.TestCase):
    def test_from_path_slug(self):
        self.assertEqual(guess_company_name("https://jobs.lever.co/meesho"), "Meesho")

    def test_from_workday_tenant(self):
        self.assertEqual(
            guess_company_name("https://barclays.wd3.myworkdayjobs.com/X"), "Barclays"
        )

    def test_from_vanity_host(self):
        self.assertEqual(guess_company_name("https://careers.sunpharma.com"), "Sunpharma")


if __name__ == "__main__":
    unittest.main()
