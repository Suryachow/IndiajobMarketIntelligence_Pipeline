"""Unit tests for schema.org JobPosting extraction: JSON-LD + microdata (stdlib unittest).

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.jsonld import (  # noqa: E402
    extract_job_postings,
    extract_label_values,
    extract_meta,
    extract_microdata,
    parse_job_posting,
)

POSTING = {
    "@context": "https://schema.org",
    "@type": "JobPosting",
    "title": "Officer - Quality Control",
    "description": "<p>Perform <b>QC</b> testing.</p><ul><li>HPLC</li></ul>",
    "datePosted": "2026-10-01",
    "validThrough": "2099-12-31",
    "employmentType": "FULL_TIME",
    "identifier": {"@type": "PropertyValue", "name": "Sun Pharma", "value": "61129144"},
    "industry": "Pharmaceuticals",
    "occupationalCategory": "Quality",
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
        "value": {"@type": "QuantitativeValue", "minValue": 400000, "maxValue": 600000},
    },
    "url": "https://careers.sunpharma.com/job/Halol-Officer/61129144/",
}


def _page(payload) -> str:
    return (
        "<html><head><script type=\"application/ld+json\">"
        + json.dumps(payload)
        + "</script></head><body></body></html>"
    )


class TestJsonLdExtraction(unittest.TestCase):
    def test_finds_job_posting(self):
        nodes = extract_job_postings(_page(POSTING))
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["title"], "Officer - Quality Control")

    def test_finds_posting_inside_graph(self):
        node = {"@context": "https://schema.org", "@graph": [{"@type": "WebSite"}, POSTING]}
        self.assertEqual(len(extract_job_postings(_page(node))), 1)

    def test_ignores_non_jobposting_and_invalid_json(self):
        self.assertEqual(extract_job_postings(_page({"@type": "Organization"})), [])
        broken = '<script type="application/ld+json">{ not json }</script>'
        self.assertEqual(extract_job_postings(broken), [])

    def test_no_scripts(self):
        self.assertEqual(extract_job_postings("<html></html>"), [])


class TestParseJobPosting(unittest.TestCase):
    def setUp(self):
        self.fields = parse_job_posting(POSTING)

    def test_core_fields(self):
        self.assertEqual(self.fields["job_id"], "61129144")
        self.assertEqual(self.fields["date_posted"], "2026-10-01")
        self.assertEqual(self.fields["employment_type"], "FULL_TIME")
        self.assertEqual(self.fields["category"], "Quality")
        self.assertEqual(self.fields["job_url"], "https://careers.sunpharma.com/job/Halol-Officer/61129144/")

    def test_location(self):
        self.assertEqual(self.fields["city"], "Halol")
        self.assertEqual(self.fields["region"], "Gujarat")
        self.assertEqual(self.fields["country"], "India")  # "IN" normalized
        self.assertEqual(self.fields["location_text"], "Halol, Gujarat, India")

    def test_salary(self):
        self.assertEqual(self.fields["min_salary"], "400000")
        self.assertEqual(self.fields["max_salary"], "600000")
        self.assertEqual(self.fields["currency"], "INR")

    def test_single_value_salary(self):
        node = {"@type": "JobPosting", "baseSalary": {"currency": "inr", "value": {"value": 50000}}}
        fields = parse_job_posting(node)
        self.assertEqual((fields["min_salary"], fields["max_salary"], fields["currency"]),
                         ("50000", "50000", "INR"))

    def test_missing_fields_stay_blank(self):
        fields = parse_job_posting({"@type": "JobPosting", "title": "Engineer"})
        self.assertEqual(fields["title"], "Engineer")
        self.assertEqual(fields["job_id"], "")
        self.assertEqual(fields["city"], "")
        self.assertEqual(fields["min_salary"], "")
        self.assertEqual(fields["country"], "")

    def test_remote_marker(self):
        fields = parse_job_posting({"@type": "JobPosting", "jobLocationType": "TELECOMMUTE"})
        self.assertEqual(fields["work_mode"], "TELECOMMUTE")


MICRODATA = """
<html><body>
<div class="jobDisplayShell" itemscope="itemscope" itemtype="http://schema.org/JobPosting">
  <span itemprop="jobLocation" itemscope itemtype="http://schema.org/Place">
    <span itemprop="address" itemscope itemtype="http://schema.org/PostalAddress">
      <meta itemprop="streetAddress" content="Halol, IN">
    </span>
  </span>
  <meta itemprop="datePosted" content="Fri Oct 30 00:00:00 UTC 2026">
  <meta itemprop="validThrough" content="Mon Nov 30 18:30:00 UTC 2026">
  <meta itemprop="hiringOrganization" content="Sun Pharmaceutical Industries Ltd">
  <h1>
    <span class="joblayouttoken-label">Title:&nbsp;</span>
    <span itemprop="title" data-careersite-propertyid="title">Tablet - Block - A - Technician</span>
  </h1>
  <div class="joblayouttoken">
    <span itemprop="description" data-careersite-propertyid="description">
      <span class="jobdescription"><p>Operate <b>tablet</b> press.</p></span>
    </span>
  </div>
</div>
</body></html>
"""


class TestMicrodataExtraction(unittest.TestCase):
    def test_extracts_fields(self):
        fields = extract_microdata(MICRODATA)
        self.assertEqual(fields["title"], "Tablet - Block - A - Technician")
        self.assertEqual(fields["description"], "Operate tablet press.")
        self.assertEqual(fields["date_posted"], "Fri Oct 30 00:00:00 UTC 2026")
        self.assertEqual(fields["valid_through"], "Mon Nov 30 18:30:00 UTC 2026")
        self.assertEqual(fields["location_text"], "Halol, IN")

    def test_blank_fields_stay_blank(self):
        fields = extract_microdata(MICRODATA)
        self.assertEqual(fields["min_salary"], "")
        self.assertEqual(fields["currency"], "")
        self.assertEqual(fields["job_url"], "")

    def test_no_jobposting_scope(self):
        self.assertEqual(extract_microdata("<html><body>no schema here</body></html>"), {})

    def test_ignores_other_scopes(self):
        html = (
            '<div itemscope itemtype="http://schema.org/Organization">'
            '<span itemprop="name">Acme</span></div>'
        )
        self.assertEqual(extract_microdata(html), {})

    def test_does_not_leak_outside_scope(self):
        # A title AFTER the JobPosting scope closes must not be captured.
        html = MICRODATA + '<span itemprop="title">Unrelated</span>'
        self.assertEqual(extract_microdata(html)["title"], "Tablet - Block - A - Technician")


LABEL_PAGE = """
<html><body>
<h1>Systems Engineer</h1>
<div>Job ID</div><div>NT-2026-004</div>
<div>Location</div><div>Guntur, Andhra Pradesh</div>
<div>Employment Type</div><div>Full-time</div>
<div>Experience</div><div>1-3 years</div>
<div>Posted On</div><div>13 Jul 2026</div>
<div>Closing Date</div><div>19 Aug 2026</div>
</body></html>
"""

# Inline layout repeats the label inside the value element.
INLINE_PAGE = """
<html><body>
<h1>Data Analyst</h1>
<div>Location</div><div>Location: Bengaluru, Karnataka</div>
</body></html>
"""


class TestLabelValues(unittest.TestCase):
    def setUp(self):
        self.fields = extract_label_values(LABEL_PAGE)

    def test_h1_becomes_title(self):
        self.assertEqual(self.fields["title"], "Systems Engineer")

    def test_label_map(self):
        self.assertEqual(self.fields["job_id"], "NT-2026-004")
        self.assertEqual(self.fields["employment_type"], "Full-time")
        self.assertEqual(self.fields["experience"], "1-3 years")
        self.assertEqual(self.fields["date_posted"], "13 Jul 2026")
        self.assertEqual(self.fields["valid_through"], "19 Aug 2026")
        self.assertEqual(self.fields["location_text"], "Guntur, Andhra Pradesh")

    def test_inline_location_strips_label(self):
        fields = extract_label_values(INLINE_PAGE)
        self.assertEqual(fields["location_text"], "Bengaluru, Karnataka")

    def test_no_schema_returns_empty(self):
        self.assertEqual(extract_label_values("<html><body>nothing here</body></html>"), {})

    def test_empty_string(self):
        self.assertEqual(extract_label_values(""), {})


class TestMetaFallback(unittest.TestCase):
    def test_og_tags(self):
        html = (
            '<html><head><meta property="og:title" content="Quality Officer">'
            '<meta property="og:description" content="QC role in Halol">'
            '<meta property="og:url" content="https://x.test/job/1"></head></html>'
        )
        meta = extract_meta(html)
        self.assertEqual(meta["title"], "Quality Officer")
        self.assertEqual(meta["description"], "QC role in Halol")
        self.assertEqual(meta["job_url"], "https://x.test/job/1")

    def test_title_tag_fallback(self):
        meta = extract_meta("<html><head><title>Engineer &amp; Analyst</title></head></html>")
        self.assertEqual(meta["title"], "Engineer & Analyst")

    def test_empty(self):
        self.assertEqual(extract_meta(""), {"title": "", "description": "", "job_url": ""})


if __name__ == "__main__":
    unittest.main()
