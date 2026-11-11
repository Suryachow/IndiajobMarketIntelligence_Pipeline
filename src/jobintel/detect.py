"""Recruitment-platform detection.

The universal crawler has to answer one question first: *which applicant
tracking system (ATS) is behind this careers URL?* When a typed adapter exists we
use it (it is faster and richer); otherwise we fall back to the generic
sitemap + schema.org crawler.

Detection is pure and offline: it fingerprints the URL, and optionally the
landing-page HTML, against known ATS hosts and page markers.
"""

from __future__ import annotations

import re
from typing import Dict, Optional
from urllib.parse import urlsplit

# Platforms that ship with a dedicated, typed adapter in ``jobintel.extractors``.
TYPED_PLATFORMS = (
    "workday", "amazon", "smartrecruiters", "greenhouse", "lever", "tcs",
    "wipro", "accenture", "ashby", "workable",
)

# URL fingerprints, checked first - the host is the strongest signal.
_URL_PATTERNS = (
    ("workday", re.compile(r"//[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/", re.I)),
    ("lever", re.compile(r"//(?:jobs|api)\.lever\.co/", re.I)),
    ("greenhouse", re.compile(r"//(?:boards|job-boards)\.greenhouse\.io/|boards-api\.greenhouse\.io/", re.I)),
    ("smartrecruiters", re.compile(r"//(?:jobs|careers)\.smartrecruiters\.com/", re.I)),
    ("amazon", re.compile(r"//(?:www\.)?amazon\.jobs/", re.I)),
    ("tcs", re.compile(r"//ibegin\.tcsapps\.com/", re.I)),
    ("wipro", re.compile(r"//careers\.wipro\.com/", re.I)),
    ("accenture", re.compile(r"//(?:www\.)?accenture\.com/(?:[a-z-]+/)?careers", re.I)),
    ("ashby", re.compile(r"//jobs\.ashbyhq\.com/", re.I)),
    ("teamtailor", re.compile(r"//[a-z0-9-]+\.teamtailor\.com/", re.I)),
    ("recruitee", re.compile(r"//[a-z0-9-]+\.recruitee\.com/", re.I)),
    ("workable", re.compile(r"//[a-z0-9.-]*workable\.com/", re.I)),
    ("icims", re.compile(r"//[a-z0-9.-]*icims\.com/", re.I)),
    ("successfactors", re.compile(r"successfactors\.(?:com|eu)|sapsf\.(?:com|eu)|/career\?career_ns=", re.I)),
    ("zoho", re.compile(r"//recruit\.zoho\.|/jobs/Careers/", re.I)),
    ("phenom", re.compile(r"phenompeople\.com|\.phenom\.cloud", re.I)),
)

# HTML markers, used when the host is a branded vanity domain (e.g.
# ``careers.<brand>.com`` that is actually served by SuccessFactors or Phenom).
_HTML_PATTERNS = (
    ("successfactors", re.compile(r"jobDisplayShell|careerSiteId|successfactors", re.I)),
    ("workday", re.compile(r"myworkdayjobs|wday/cxs", re.I)),
    ("ashby", re.compile(r"ashbyhq", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io", re.I)),
    ("smartrecruiters", re.compile(r"smartrecruiters", re.I)),
    ("icims", re.compile(r"icims\.com", re.I)),
    ("workable", re.compile(r"workable\.com", re.I)),
    ("teamtailor", re.compile(r"teamtailor\.com", re.I)),
    ("recruitee", re.compile(r"recruitee\.com", re.I)),
    ("lever", re.compile(r"lever\.co", re.I)),
    ("tcs", re.compile(r"ibegin|tcsapps", re.I)),
    ("wipro", re.compile(r"careers\.wipro\.com|jobs2web|rmk-", re.I)),
    ("accenture", re.compile(r"findjobs|accenture.*jobdetails", re.I)),
)

_WORKDAY_RE = re.compile(r"//([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#]+)", re.I)

# Slug-bearing platforms and the config key their adapter expects.
_SLUG_RES = {
    "lever": re.compile(r"lever\.co/([^/?#]+)", re.I),
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.I),
    "smartrecruiters": re.compile(r"smartrecruiters\.com/([^/?#]+)", re.I),
    "ashby": re.compile(r"ashbyhq\.com/([^/?#]+)", re.I),
    "workable": re.compile(r"(?:apply\.)?workable\.com/([^/?#]+)", re.I),
}
_SLUG_KEYS = {
    "lever": "company",
    "greenhouse": "board",
    "smartrecruiters": "company",
    "ashby": "board",
    "workable": "account",
}

_HOST_NOISE = {"www", "jobs", "careers", "job", "com", "co", "io", "net", "org", "myworkdayjobs"}


def detect_platform(careers_url: str, html: str = "") -> str:
    """Return the platform slug for a careers URL, or ``"unknown"``."""
    url = careers_url or ""
    for platform, pattern in _URL_PATTERNS:
        if pattern.search(url):
            return platform
    text = html or ""
    if text:
        for platform, pattern in _HTML_PATTERNS:
            if pattern.search(text):
                return platform
    return "unknown"


def is_typed(platform: str) -> bool:
    """True when ``platform`` has a dedicated adapter (no generic crawl needed)."""
    return platform in TYPED_PLATFORMS


def parse_workday(url: str) -> Optional[Dict[str, str]]:
    """Extract ``{tenant, wd, site}`` from a ``*.myworkdayjobs.com`` URL."""
    match = _WORKDAY_RE.search(url or "")
    if not match:
        return None
    return {"tenant": match.group(1), "wd": match.group(2), "site": match.group(3)}


def platform_config(platform: str, url: str) -> Dict[str, str]:
    """Build the adapter ``config`` block for a typed platform from its URL."""
    if platform == "workday":
        return parse_workday(url) or {}
    if platform in _SLUG_RES:
        match = _SLUG_RES[platform].search(url or "")
        if match:
            return {_SLUG_KEYS[platform]: match.group(1)}
    if platform == "amazon":
        return {"country": "IND"}
    if platform == "tcs":
        # TCS iBegin: a headless-browser adapter (see jobintel.render).
        return {"base_url": "https://ibegin.tcsapps.com/candidate/next", "locale": "en-IN"}
    if platform == "wipro":
        # SAP SuccessFactors jobs2web/RMK behind a vanity domain.
        return {"locale": "en_US", "location": "india"}
    if platform == "accenture":
        return {"locale": "in-en", "country": "India"}
    return {}


def guess_company_name(url: str) -> str:
    """Best-effort employer name from a careers URL (used by the ad-hoc CLI)."""
    config = platform_config(detect_platform(url), url)
    for key in ("board", "company", "account", "tenant"):
        if config.get(key):
            return _pretty(config[key])
    host = urlsplit(url or "").netloc.lower().split(":")[0]
    parts = [p for p in host.split(".") if p and p not in _HOST_NOISE]
    if parts:
        return _pretty(parts[0])
    return "Unknown"


def _pretty(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value or "").strip().title()
