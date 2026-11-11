"""End-to-end tests for the generic (universal) crawler and the URL router.

These use a fake HTTP client, so they exercise sitemap discovery -> schema.org
JSON-LD extraction -> India filtering -> validation without any network access.

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.extractors import build_for_url  # noqa: E402
from jobintel.extractors.generic import GenericExtractor  # noqa: E402

ROBOTS = "User-agent: *\nSitemap: https://careers.acme.test/sitemap.xml\n"
SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.acme.test/job/quality-officer/61129144/</loc></url>
  <url><loc>https://careers.acme.test/job/manufacturing-tech/90000111/</loc></url>
</urlset>
"""

INDIA_URL = "https://careers.acme.test/job/quality-officer/61129144/"
US_URL = "https://careers.acme.test/job/manufacturing-tech/90000111/"

INDIA_POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Officer - Quality Control",
    "description": "<p>Perform QC testing with 3-5 years of experience.</p>",
    "datePosted": "2026-10-01",
    "validThrough": "2099-12-31",
    "employmentType": "FULL_TIME",
    "identifier": {"@type": "PropertyValue", "value": "61129144"},
    "jobLocation": {
        "@type": "Place",
        "address": {
            "@type": "PostalAddress",
            "addressLocality": "Halol",
            "addressRegion": "Gujarat",
            "addressCountry": "IN",
        },
    },
    "baseSalary": {
        "@type": "MonetaryAmount",
        "currency": "INR",
        "value": {"minValue": 400000, "maxValue": 600000},
    },
    "url": INDIA_URL,
}

US_POSTING = {
    "@type": "JobPosting",
    "title": "Manufacturing Technician",
    "description": "<p>US role.</p>",
    "validThrough": "2099-12-31",
    "jobLocation": {
        "@type": "Place",
        "address": {"addressLocality": "Austin", "addressRegion": "Texas", "addressCountry": "US"},
    },
    "url": US_URL,
}


def _page(payload) -> str:
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></head><body></body></html>"
    )


class FakeClient:
    """Minimal stand-in for :class:`jobintel.http_client.HttpClient`."""

    def __init__(self, pages):
        self.pages = pages
        self.stats = {"requests": len(pages), "cache_hits": 0, "retries": 0, "errors": 0}

    def get_text(self, url, headers=None):
        if url not in self.pages:
            raise RuntimeError(f"404 {url}")
        return self.pages[url]

    def get_page(self, url, headers=None):
        return self.get_text(url, headers)

    def close(self):
        pass

    def map_concurrent(self, items, fn, workers=4):
        results = []
        for item in items:
            try:
                results.append(fn(item))
            except Exception:  # noqa: BLE001 - mirror HttpClient isolation
                results.append(None)
        return results


def _company():
    return {
        "company_name": "Acme Pharma",
        "industry": "Pharmaceuticals",
        "careers_url": "https://careers.acme.test/careers",
        "extractor": "generic",
        "slug": "acme-pharma",
        "config": {"careers_url": "https://careers.acme.test/careers"},
    }


def _pages(india_page=None, us_page=None):
    return {
        "https://careers.acme.test/robots.txt": ROBOTS,
        "https://careers.acme.test/sitemap.xml": SITEMAP,
        INDIA_URL: india_page if india_page is not None else _page(INDIA_POSTING),
        US_URL: us_page if us_page is not None else _page(US_POSTING),
    }


class TestGenericCrawler(unittest.TestCase):
    def test_extracts_india_posting_only(self):
        extractor = GenericExtractor(_company(), client=FakeClient(_pages()))
        result = extractor.extract()

        self.assertEqual(result.status, "OK")
        self.assertEqual(result.portal_total, 1)
        self.assertEqual(result.coverage_pct, "100.0%")
        self.assertEqual(len(result.postings), 1)

        posting = result.postings[0]
        self.assertEqual(posting.company_name, "Acme Pharma")
        self.assertEqual(posting.job_id, "61129144")
        self.assertEqual(posting.job_title, "Officer - Quality Control")
        self.assertEqual((posting.city, posting.state, posting.country), ("Halol", "Gujarat", "India"))
        self.assertEqual(posting.employment_type, "Full-time")
        self.assertEqual(posting.experience_required, "3-5 years")
        self.assertEqual((posting.min_salary, posting.max_salary, posting.currency),
                         ("400000", "600000", "INR"))
        self.assertEqual(posting.source_platform, "generic")
        self.assertEqual(posting.job_url, INDIA_URL)
        self.assertTrue(posting.source_record_hash)

    def test_expired_posting_excluded(self):
        expired = dict(INDIA_POSTING, validThrough="2020-01-01")
        extractor = GenericExtractor(_company(), client=FakeClient(_pages(india_page=_page(expired))))
        result = extractor.extract()
        self.assertEqual(result.postings, [])
        self.assertEqual(result.portal_total, 0)
        self.assertTrue(any("expired" in note for note in result.notes))

    def test_metadata_fallback_has_no_location(self):
        # A page with neither JSON-LD nor a country cannot be confirmed as an
        # India posting, so nothing is fabricated - the record is skipped.
        page = '<html><head><meta property="og:title" content="Mystery Role"></head></html>'
        extractor = GenericExtractor(_company(), client=FakeClient(_pages(
            india_page=page, us_page=page)))
        result = extractor.extract()
        self.assertEqual(result.postings, [])
        self.assertFalse(result.errors)

    def test_fails_without_a_sitemap(self):
        pages = {k: v for k, v in _pages().items() if not k.endswith(".xml") and "robots" not in k}
        extractor = GenericExtractor(_company(), client=FakeClient(pages))
        result = extractor.extract()
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.postings, [])
        self.assertTrue(result.errors)


class TestRouter(unittest.TestCase):
    def test_routes_known_platform_to_typed_adapter(self):
        extractor = build_for_url(
            "https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays",
            client=FakeClient({}),
        )
        self.assertEqual(type(extractor).__name__, "WorkdayExtractor")
        self.assertEqual(extractor.company["company_name"], "Barclays")

    def test_routes_unknown_platform_to_generic(self):
        extractor = build_for_url("https://careers.sunpharma.com", client=FakeClient({}))
        self.assertEqual(type(extractor).__name__, "GenericExtractor")
        self.assertEqual(extractor.platform, "generic")

    def test_derives_slug_config_for_lever(self):
        extractor = build_for_url("https://jobs.lever.co/meesho", client=FakeClient({}))
        self.assertEqual(type(extractor).__name__, "LeverExtractor")


if __name__ == "__main__":
    unittest.main()
