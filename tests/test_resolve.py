"""Unit tests for name resolution, auto-discovery and the CSV company loader.

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _ROOT)

import crawl  # noqa: E402
from jobintel.resolve import (  # noqa: E402
    _candidate_hosts,
    _same_site,
    _tokens,
    discover_careers_url,
    load_directory,
    load_specs,
    resolve,
)

TARGET_CSV = os.path.join(_ROOT, "config", "target_companies.csv")
CAREERS_HTML = "<html><h1>Careers</h1>" + ("open jobs and careers " * 40) + "</html>"


class FakeClient:
    """A tiny in-memory client: returns canned HTML, raises for unknown URLs."""

    def __init__(self, pages):
        self.pages = pages

    def get_page(self, url, headers=None):
        if url in self.pages:
            return self.pages[url]
        raise RuntimeError("not found")


class TestDirectory(unittest.TestCase):
    def test_shipped_directory_loads(self):
        directory = load_directory()
        self.assertIn("wipro", directory)
        self.assertIn("hdfc bank", directory)

    def test_exact_and_alias(self):
        self.assertEqual(resolve("Wipro")["careers_url"], "https://careers.wipro.com/")
        self.assertEqual(
            resolve("Wipro Ltd")["careers_url"], "https://careers.wipro.com/"
        )

    def test_fuzzy(self):
        # "tata consultancy" must fold onto the full TCS entry.
        self.assertEqual(
            resolve("tata consultancy")["careers_url"],
            "https://ibegin.tcsapps.com/candidate/next/en-IN/landing",
        )

    def test_typed_extractor_is_preserved(self):
        self.assertEqual(resolve("Accenture").get("extractor"), "accenture")

    def test_unknown_without_discovery(self):
        self.assertIsNone(resolve("Totally Made Up Company 12345"))

    def test_target_names_all_resolve(self):
        for name in [
            "HDFC Bank", "Tata Consultancy Services (TCS)", "Microsoft India",
            "Reliance Industries", "Bharti Airtel", "Tata Motors",
            "Sun Pharmaceutical", "Hindustan Unilever",
        ]:
            self.assertIsNotNone(resolve(name), f"{name} did not resolve")


class TestDiscovery(unittest.TestCase):
    def test_homepage_link(self):
        client = FakeClient({
            "https://example.com/": '<a href="/careers">Careers</a>',
            "https://example.com/careers": CAREERS_HTML,
        })
        found = discover_careers_url("Example", client.get_page)
        self.assertEqual(found["careers_url"], "https://example.com/careers")
        self.assertEqual(found["source"], "homepage-link")

    def test_career_path_fallback(self):
        client = FakeClient({"https://acme.com/": "<html>home</html>",
                             "https://acme.com/careers": CAREERS_HTML})
        found = discover_careers_url("Acme", client.get_page)
        self.assertEqual(found["careers_url"], "https://acme.com/careers")
        self.assertEqual(found["source"], "career-path")

    def test_unverified_link_is_rejected(self):
        # Homepage advertises a careers link, but the target does not load.
        client = FakeClient({"https://example.com/": '<a href="/careers">Careers</a>'})
        self.assertIsNone(discover_careers_url("Example", client.get_page))

    def test_resolve_with_discovery(self):
        client = FakeClient({
            "https://totallyunknown.com/": '<a href="/jobs">Jobs</a>',
            "https://totallyunknown.com/jobs": CAREERS_HTML,
        })
        spec = resolve("Totally Unknown Co", client=client, discover=True)
        self.assertIsNotNone(spec)
        self.assertTrue(spec["discovered"])
        self.assertEqual(spec["careers_url"], "https://totallyunknown.com/jobs")

    def test_no_fabrication_without_client(self):
        self.assertIsNone(resolve("Totally Unknown Co", discover=True))


class TestHelpers(unittest.TestCase):
    def test_tokens_drop_suffixes(self):
        self.assertEqual(_tokens("Hindustan Unilever Limited"), ["hindustan", "unilever"])

    def test_candidate_hosts_prefers_compact(self):
        hosts = _candidate_hosts(["hdfc", "bank"], 4)
        self.assertEqual(hosts[0], "hdfcbank.com")

    def test_same_site(self):
        self.assertTrue(_same_site("www.hdfcbank.com", "hdfcbank.com"))
        self.assertFalse(_same_site("naukri.com", "hdfcbank.com"))


class TestCsvLoader(unittest.TestCase):
    def test_load_target_csv(self):
        specs = load_specs(TARGET_CSV)
        self.assertEqual(len(specs), 8)
        self.assertEqual(specs[0]["company_name"], "HDFC Bank")
        self.assertEqual(specs[0]["industry"], "Banking & Financial Services")

    def test_build_specs_resolves_names(self):
        args = crawl.parse_args(["--companies", TARGET_CSV])
        specs = crawl.build_specs(args, FakeClient({}))
        self.assertEqual(len(specs), 8)
        self.assertTrue(all(s["careers_url"] for s in specs))
        self.assertTrue(all(s.get("industry") for s in specs))

    def test_company_flag(self):
        args = crawl.parse_args(["--company", "Wipro", "--company", "Accenture"])
        specs = crawl.build_specs(args, FakeClient({}))
        urls = {s["careers_url"] for s in specs}
        self.assertEqual(len(specs), 2)
        self.assertIn("https://careers.wipro.com/", urls)


if __name__ == "__main__":
    unittest.main()
