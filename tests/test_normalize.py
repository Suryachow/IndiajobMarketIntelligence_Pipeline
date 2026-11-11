"""Unit tests for the normalization layer (stdlib unittest; no dependencies).

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.normalize import (  # noqa: E402
    clean_html,
    extract_experience,
    extract_salary,
    infer_work_arrangement,
    is_india_location,
    normalize_employment_type,
    parse_location,
    parse_posting_date,
    salary_from_structured,
)


class TestIndiaDetection(unittest.TestCase):
    def test_country_name(self):
        self.assertTrue(is_india_location("India, Karnataka, Bengaluru"))
        self.assertTrue(is_india_location("IN, MH, Maharashtra"))

    def test_city_and_state_only(self):
        # Lever/Greenhouse frequently omit the country name entirely.
        self.assertTrue(is_india_location("Bangalore, Karnataka"))
        self.assertTrue(is_india_location("bengaluru"))
        self.assertTrue(is_india_location("Delhi NCR"))
        self.assertTrue(is_india_location("Tamil Nadu"))

    def test_negative_cases(self):
        self.assertFalse(is_india_location("Indiana, US"))
        self.assertFalse(is_india_location("Amsterdam, Netherlands"))
        self.assertFalse(is_india_location(""))
        self.assertFalse(is_india_location(None))  # type: ignore[arg-type]
        self.assertFalse(is_india_location("Austin, Texas, US"))


class TestLocationParsing(unittest.TestCase):
    def test_workday_country_first(self):
        self.assertEqual(parse_location("India, Karnataka, Bengaluru"), ("Bengaluru", "Karnataka", "India"))

    def test_workday_country_last(self):
        self.assertEqual(parse_location("Bangalore, Karnataka, India"), ("Bangalore", "Karnataka", "India"))

    def test_city_only_infers_country(self):
        city, state, country = parse_location("Bengaluru")
        self.assertEqual(city, "Bengaluru")
        self.assertEqual(country, "India")

    def test_remote_stripped(self):
        city, state, country = parse_location("India, Remote")
        self.assertEqual(country, "India")


class TestDates(unittest.TestCase):
    def _today(self):
        return _dt.date(2026, 11, 5)

    def test_relative(self):
        self.assertEqual(parse_posting_date("Posted Today", self._today()), "2026-11-05")
        self.assertEqual(parse_posting_date("Posted Yesterday", self._today()), "2026-11-04")
        self.assertEqual(parse_posting_date("Posted 30+ Days Ago", self._today()), "2026-10-06")

    def test_absolute(self):
        self.assertEqual(parse_posting_date("November  5, 2026", self._today()), "2026-11-05")
        self.assertEqual(parse_posting_date("2026-11-05", self._today()), "2026-11-05")

    def test_successfactors_ctime(self):
        # SAP SuccessFactors / Phenom publish ctime-style dates.
        self.assertEqual(parse_posting_date("Fri Oct 30 00:00:00 UTC 2026", self._today()), "2026-10-30")
        self.assertEqual(parse_posting_date("Mon Nov 30 18:30:00 UTC 2026", self._today()), "2026-11-30")

    def test_unknown(self):
        self.assertEqual(parse_posting_date("", self._today()), "")
        self.assertEqual(parse_posting_date("sometime soon", self._today()), "")


class TestControlledVocabularies(unittest.TestCase):
    def test_employment_type(self):
        self.assertEqual(normalize_employment_type("Full time"), "Full-time")
        self.assertEqual(normalize_employment_type("", "This is a part-time role"), "Part-time")
        self.assertEqual(normalize_employment_type("Contractor"), "Contract")
        self.assertEqual(normalize_employment_type("nonsense"), "")

    def test_work_arrangement(self):
        self.assertEqual(infer_work_arrangement("Bengaluru (Hybrid)"), "Hybrid")
        self.assertEqual(infer_work_arrangement("Remote - India"), "Remote")
        self.assertEqual(infer_work_arrangement("Pune"), "")

    def test_experience(self):
        self.assertEqual(extract_experience("You have 5-8 years of experience"), "5-8 years")
        self.assertEqual(extract_experience("Minimum 3 years experience required"), "Minimum 3 years")
        self.assertEqual(extract_experience("8+ years in backend"), "8+ years")
        self.assertEqual(extract_experience("no experience info here"), "")


class TestSalary(unittest.TestCase):
    def test_no_fabrication(self):
        # No published range -> everything blank.
        self.assertEqual(extract_salary("Competitive salary and benefits"), ("", "", ""))
        self.assertEqual(extract_salary(""), ("", "", ""))
        self.assertEqual(extract_salary(None), ("", "", ""))  # type: ignore[arg-type]

    def test_explicit_range(self):
        mn, mx, cur = extract_salary("Compensation: INR 12,00,000 to INR 18,00,000 per annum")
        self.assertEqual(cur, "INR")
        self.assertEqual((mn, mx), ("1200000", "1800000"))

    def test_unit_led_range_without_symbol(self):
        # "12-15 LPA" must be captured, and LPA implies INR.
        self.assertEqual(extract_salary("Salary band: 12-15 LPA"), ("1200000", "1500000", "INR"))

    def test_monthly_range_is_annualised(self):
        txt = "Salary: \u20b918k - \u20b940k in-hand per month"
        self.assertEqual(extract_salary(txt), ("216000", "480000", "INR"))

    def test_hourly_range(self):
        mn, mx, cur = extract_salary("Pay range $20 - $22 per hour")
        self.assertEqual((mn, mx, cur), ("20", "22", "USD"))

    def test_single_value_next_to_keyword(self):
        self.assertEqual(extract_salary("CTC: 8,00,000"), ("800000", "800000", "INR"))

    def test_labelled_min_max_pair(self):
        # Workday postings publish pay as two labelled lines; both bounds and the
        # currency must be captured (regression: the max used to be lost).
        txt = (
            "This role is located in Whippany, NJ office.\n\n"
            "Minimum Salary: $ 170,000\n\n"
            "Maximum Salary: $ 230,000\n\n"
            "The minimum and maximum salary/rate information above include only "
            "base salary or base hourly rate."
        )
        self.assertEqual(extract_salary(txt), ("170000", "230000", "USD"))

    def test_labelled_min_max_pair_rupees(self):
        txt = "Minimum Salary: \u20b9 12,00,000\nMaximum Salary: \u20b9 18,00,000"
        self.assertEqual(extract_salary(txt), ("1200000", "1800000", "INR"))

    def test_lone_min_label_is_not_a_pair(self):
        # A single label is not a published range; it must not fabricate a max.
        self.assertEqual(
            extract_salary("Minimum Salary: $ 170,000 is offered"), ("170000", "170000", "USD")
        )

    def test_ambiguous_ctc_acronym_is_not_salary(self):
        # "clear-to-close (CTC)" is an acronym, not pay; the nearby year 2022
        # must never be captured as a salary.
        txt = (
            "Provide guidance on loan decisions, required conditions, and final "
            "clear-to-close (CTC) status. Escalate complex issues to senior "
            "management when necessary. Established in 2022."
        )
        self.assertEqual(extract_salary(txt), ("", "", ""))

    def test_revenue_and_headcount_are_not_salary(self):
        # Monetary prose about business metrics must never become pay.
        self.assertEqual(extract_salary("Revenue grew over 64 per cent to Rs 1,370 crore"), ("", "", ""))
        self.assertEqual(extract_salary("We have 5k to 10k users"), ("", "", ""))
        self.assertEqual(
            extract_salary("products ranging from \u20b950,000 to \u20b95 Lakh, we enable personal loans"),
            ("", "", ""),
        )
        # Deal size, not pay.
        self.assertEqual(
            extract_salary("identify and qualify opportunities worth $500K\u2013$5M+"),
            ("", "", ""),
        )

    def test_structured_lever(self):
        lever = {"min": 1200000, "max": 1800000, "currency": "INR", "interval": "per-year-salary"}
        self.assertEqual(salary_from_structured(lever), ("1200000", "1800000", "INR"))

    def test_structured_greenhouse_cents(self):
        gh = [{"min_cents": 120000000, "max_cents": 180000000, "currency_type": "INR"}]
        self.assertEqual(salary_from_structured(gh), ("1200000", "1800000", "INR"))

    def test_structured_absent(self):
        self.assertEqual(salary_from_structured(None, {}, []), ("", "", ""))

    def test_structured_ashby_summary_components(self):
        # Ashby types each component; only the salary one counts, and equity is ignored.
        comps = [
            {"compensationType": "EquityPercentage", "minValue": 0, "maxValue": 0.05},
            {
                "compensationType": "Salary",
                "minValue": 2000000,
                "maxValue": 3000000,
                "currencyCode": "INR",
            },
        ]
        self.assertEqual(salary_from_structured(comps), ("2000000", "3000000", "INR"))

    def test_structured_non_salary_type_is_not_pay(self):
        # A board that only publishes equity must stay blank (no fabrication).
        comps = [{"compensationType": "EquityPercentage", "minValue": 0, "maxValue": 0.1}]
        self.assertEqual(salary_from_structured(comps), ("", "", ""))

    def test_structured_zeroed_range_is_blank(self):
        # Lever boards may enable salaryRange but publish 0/0 ("not disclosed").
        self.assertEqual(
            salary_from_structured({"min": 0, "max": 0, "currency": "INR"}), ("", "", "")
        )


class TestHtmlCleaning(unittest.TestCase):
    def test_strips_markup(self):
        html = "<p>Hello&nbsp;<b>World</b></p><ul><li>One</li><li>Two</li></ul>"
        text = clean_html(html)
        self.assertIn("Hello World", text)
        self.assertIn("One", text)
        self.assertNotIn("<", text)

    def test_removes_scripts(self):
        self.assertNotIn("alert", clean_html("<script>alert(1)</script>content"))


if __name__ == "__main__":
    unittest.main()
