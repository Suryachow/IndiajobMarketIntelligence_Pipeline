"""Unit tests for robots.txt / sitemap.xml discovery (stdlib unittest).

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.sitemap import (  # noqa: E402
    discover_job_urls,
    filter_job_urls,
    is_job_like_url,
    parse_robots_sitemaps,
    parse_sitemap,
)

URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://careers.acme.test/job/Halol-Officer/61129144/</loc></url>
  <url><loc>https://careers.acme.test/jobs</loc></url>
  <url><loc>https://careers.acme.test/search/?q=engineer</loc></url>
</urlset>
"""

SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://careers.acme.test/sitemap-jobs.xml</loc></sitemap>
</sitemapindex>
"""

ROBOTS = """
User-agent: *
Disallow: /search
Sitemap: https://careers.acme.test/sitemap.xml
Sitemap: https://careers.acme.test/sitemap-jobs.xml
"""


def _fetcher(pages):
    def fetch(url):
        if url not in pages:
            raise RuntimeError("404")
        return pages[url]

    return fetch


class TestRobots(unittest.TestCase):
    def test_parse_sitemaps(self):
        self.assertEqual(
            parse_robots_sitemaps(ROBOTS),
            ["https://careers.acme.test/sitemap.xml", "https://careers.acme.test/sitemap-jobs.xml"],
        )

    def test_empty(self):
        self.assertEqual(parse_robots_sitemaps(""), [])
        self.assertEqual(parse_robots_sitemaps("User-agent: *\nDisallow: /"), [])


class TestSitemapParsing(unittest.TestCase):
    def test_urlset(self):
        pages, children = parse_sitemap(URLSET)
        self.assertEqual(len(pages), 3)
        self.assertEqual(children, [])
        self.assertIn("https://careers.acme.test/job/Halol-Officer/61129144/", pages)

    def test_sitemap_index(self):
        pages, children = parse_sitemap(SITEMAP_INDEX)
        self.assertEqual(pages, [])
        self.assertEqual(children, ["https://careers.acme.test/sitemap-jobs.xml"])

    def test_malformed_xml_falls_back_to_regex(self):
        pages, children = parse_sitemap("<urlset><url><loc>https://x.test/job/a</loc></url>")
        self.assertEqual(pages, ["https://x.test/job/a"])
        self.assertEqual(children, [])

    def test_empty(self):
        self.assertEqual(parse_sitemap(""), ([], []))


class TestJobUrlFilter(unittest.TestCase):
    def test_job_like(self):
        self.assertTrue(is_job_like_url("https://careers.acme.test/job/Halol-Officer/61129144/"))
        self.assertTrue(is_job_like_url("https://x.test/jobs/software-engineer-123"))
        self.assertTrue(is_job_like_url("https://x.test/positions/42"))
        self.assertTrue(is_job_like_url("https://jobs.lever.co/meesho/abc-123"))
        # Collection directory + role slug, no keyword and no id.
        self.assertTrue(is_job_like_url("https://www.neuraltrixai.com/career/systems-engineer"))

    def test_listing_pages_rejected(self):
        self.assertFalse(is_job_like_url("https://careers.acme.test/jobs"))
        self.assertFalse(is_job_like_url("https://careers.acme.test/search/?q=engineer"))
        self.assertFalse(is_job_like_url("https://careers.acme.test/careers/"))
        self.assertFalse(is_job_like_url("https://careers.acme.test/login"))
        self.assertFalse(is_job_like_url(""))
        self.assertFalse(is_job_like_url("https://careers.acme.test/about-us"))
        self.assertFalse(is_job_like_url("https://careers.acme.test/about/careers"))

    def test_filter_dedupes_and_keeps_order(self):
        urls = [
            "https://x.test/job/a",
            "https://x.test/job/a",
            "https://x.test/jobs",
            "https://x.test/job/b",
        ]
        self.assertEqual(filter_job_urls(urls), ["https://x.test/job/a", "https://x.test/job/b"])


class TestDiscoverJobUrls(unittest.TestCase):
    def test_robots_to_index_to_urlset(self):
        fetch = _fetcher(
            {
                "https://careers.acme.test/robots.txt": ROBOTS,
                "https://careers.acme.test/sitemap.xml": SITEMAP_INDEX,
                "https://careers.acme.test/sitemap-jobs.xml": URLSET,
            }
        )
        job_urls, notes = discover_job_urls(fetch, "https://careers.acme.test/careers")
        self.assertEqual(job_urls, ["https://careers.acme.test/job/Halol-Officer/61129144/"])
        self.assertTrue(any("robots.txt advertised" in note for note in notes))
        self.assertTrue(any("job-like" in note for note in notes))

    def test_falls_back_to_default_sitemap_when_robots_missing(self):
        fetch = _fetcher({"https://careers.acme.test/sitemap.xml": URLSET})
        job_urls, notes = discover_job_urls(fetch, "https://careers.acme.test/careers")
        self.assertEqual(job_urls, ["https://careers.acme.test/job/Halol-Officer/61129144/"])
        self.assertTrue(any("trying /sitemap.xml" in note for note in notes))

    def test_no_origin(self):
        job_urls, notes = discover_job_urls(_fetcher({}), "not-a-url")
        self.assertEqual(job_urls, [])
        self.assertTrue(notes)


if __name__ == "__main__":
    unittest.main()
