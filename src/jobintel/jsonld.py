"""schema.org ``JobPosting`` extraction from HTML.

Almost every modern careers site embeds a machine-readable JSON-LD
``JobPosting`` object - it is what makes the posting appear in Google Jobs. That
makes schema.org the most portable, vendor-neutral extraction contract, which is
exactly what a *universal* crawler needs for sites whose ATS we do not know.

Some sites (notably SAP SuccessFactors / Phenom portals) publish the very same
schema.org ``JobPosting`` as **HTML microdata** (``itemprop="..."``) rather than
JSON-LD. :func:`extract_microdata` reads that encoding, so both are covered.

When a page publishes neither, we fall back to ``og:``/``<title>`` metadata,
which still yields a title, a description and a URL.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .normalize import clean_html

_SCRIPT_RE = re.compile(
    r'<script[^>]*\btype=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
_ATTR_RE = re.compile(r'([a-z:_-]+)\s*=\s*["\']([^"\']*)["\']', re.IGNORECASE)

_COUNTRY_ALIASES = {
    "in": "India", "ind": "India", "india": "India", "ind.": "India",
    "us": "United States", "usa": "United States",
    "gb": "United Kingdom", "uk": "United Kingdom",
    "ca": "Canada", "sg": "Singapore", "ae": "United Arab Emirates",
    "au": "Australia", "de": "Germany", "fr": "France", "nl": "Netherlands",
}


def iter_jsonld_objects(html: str) -> Iterable[Dict[str, Any]]:
    """Yield every JSON object found in ``application/ld+json`` script blocks."""
    for match in _SCRIPT_RE.finditer(html or ""):
        raw = match.group(1).strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for obj in _flatten(data):
            yield obj


def _flatten(data: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _flatten(item)
    elif isinstance(data, dict):
        graph = data.get("@graph")
        if isinstance(graph, list):
            for item in graph:
                yield from _flatten(item)
        yield data


def extract_job_postings(html: str) -> List[Dict[str, Any]]:
    """Return every schema.org ``JobPosting`` node embedded in the page."""
    return [obj for obj in iter_jsonld_objects(html) if "jobposting" in _type_names(obj)]


def parse_job_posting(node: Dict[str, Any]) -> Dict[str, str]:
    """Flatten a schema.org ``JobPosting`` node into canonical string fields."""
    city, region, country, location_text = _address(node.get("jobLocation"))
    low, high, currency = _salary(node.get("baseSalary"))
    return {
        "title": _text(node.get("title")),
        "description": _text(node.get("description")),
        "date_posted": _text(node.get("datePosted")),
        "valid_through": _text(node.get("validThrough")),
        "employment_type": _text(node.get("employmentType")),
        "job_id": _identifier(node.get("identifier")),
        "category": _text(node.get("occupationalCategory")),
        "industry": _text(node.get("industry")),
        "job_url": _url(node.get("url")),
        "work_mode": _text(node.get("jobLocationType")).upper(),
        "city": city,
        "region": region,
        "country": country,
        "location_text": location_text,
        "min_salary": low,
        "max_salary": high,
        "currency": currency,
    }


def extract_microdata(html: str) -> Dict[str, str]:
    """Return canonical ``JobPosting`` fields from schema.org **microdata**.

    Returns ``{}`` when the page carries no ``JobPosting`` microdata scope, so
    callers can fall through to the metadata fallback.
    """
    parser = _MicrodataJobPosting()
    try:
        parser.feed(html or "")
    except Exception:  # noqa: BLE001 - malformed markup must not abort a crawl
        return {}
    return parser.fields()


_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
# Microdata properties that make up the postal address of a ``jobLocation``.
_ADDRESS_PROPS = (
    "streetAddress", "addressLocality", "addressRegion", "addressCountry", "postalCode",
)


class _MicrodataJobPosting(HTMLParser):
    """Collect ``itemprop`` values inside an ``itemtype="…/JobPosting"`` scope."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.found = False
        self.props: Dict[str, str] = {}
        self._stack: List[str] = []
        self._capture: Optional[str] = None
        self._capture_stack = 0
        self._buffer: List[str] = []

    # -- HTMLParser hooks --------------------------------------------------
    def handle_starttag(self, tag: str, attrs: Any) -> None:
        values = {name.lower(): (value or "") for name, value in attrs}
        if not self.found:
            if values.get("itemtype", "").lower().rstrip("/").endswith("jobposting"):
                self.found = True
                self._stack.append(tag)
            return
        if tag not in _VOID_TAGS:
            self._stack.append(tag)
        self._record(values)

    def handle_endtag(self, tag: str) -> None:
        if not self.found or tag in _VOID_TAGS:
            return
        if self._stack:
            self._stack.pop()
        if self._capture is not None and len(self._stack) < self._capture_stack:
            self.props.setdefault(self._capture, "".join(self._buffer).strip())
            self._capture = None
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self.found and self._capture is not None:
            self._buffer.append(data)

    # -- internals ---------------------------------------------------------
    def _record(self, attrs: Dict[str, str]) -> None:
        prop = attrs.get("itemprop", "")
        if not prop:
            return
        content = attrs.get("content")
        if content is not None:
            self.props.setdefault(prop, content.strip())
            return
        if "itemscope" in attrs:  # nested scope (e.g. jobLocation) - descend, don't capture
            return
        if self._capture is None:
            self._capture = prop
            self._capture_stack = len(self._stack)
            self._buffer = []

    def fields(self) -> Dict[str, str]:
        if not self.found:
            return {}
        props = self.props
        location_text = ", ".join(p.strip() for p in (props.get(k, "") for k in _ADDRESS_PROPS) if p.strip())
        return {
            "title": clean_html(props.get("title", "")),
            "description": props.get("description", ""),
            "date_posted": props.get("datePosted", ""),
            "valid_through": props.get("validThrough", ""),
            "employment_type": props.get("employmentType", ""),
            "job_id": props.get("identifier", "") or props.get("jobId", ""),
            "category": props.get("occupationalCategory", ""),
            "industry": props.get("industry", ""),
            "job_url": "",
            "work_mode": props.get("jobLocationType", "").upper(),
            "city": "",
            "region": "",
            "country": "",
            "location_text": location_text,
            "min_salary": "",
            "max_salary": "",
            "currency": "",
        }


_LABEL_FIELDS = {
    "job id": "job_id",
    "job code": "job_id",
    "job reference": "job_id",
    "reference": "job_id",
    "requisition id": "job_id",
    "location": "location_text",
    "job location": "location_text",
    "work location": "location_text",
    "based in": "location_text",
    "employment type": "employment_type",
    "job type": "employment_type",
    "contract type": "employment_type",
    "experience": "experience",
    "experience required": "experience",
    "posted on": "date_posted",
    "date posted": "date_posted",
    "open date": "date_posted",
    "closing date": "valid_through",
    "closed on": "valid_through",
    "apply before": "valid_through",
    "category": "category",
    "department": "category",
    "team": "category",
    "industry": "industry",
}
# Upper bound on a single element's text, so whole-page containers (``<body>``)
# do not become candidate "values".
_MAX_LABEL_TEXT = 400


class _ElementTextParser(HTMLParser):
    """Emit the trimmed text of every element, in document order."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: List[List[Any]] = []
        self._skip = 0
        self.texts: List[str] = []
        self.headings: List[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if self._skip:
            return
        if tag in ("script", "style"):
            self._skip += 1
            return
        if tag in _VOID_TAGS:
            return
        self._stack.append([tag, []])

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip or not self._stack or self._stack[-1][0] != tag:
            return
        _, parts = self._stack.pop()
        text = re.sub(r"\s+", " ", "".join(parts)).strip()
        if not text or len(text) > _MAX_LABEL_TEXT:
            return
        self.texts.append(text)
        if tag == "h1":
            self.headings.append(text)

    def handle_data(self, data: str) -> None:
        if self._skip or not self._stack:
            return
        self._stack[-1][1].append(data)


def extract_label_values(html: str) -> Dict[str, str]:
    """Map common ``Label → value`` layouts onto canonical job fields.

    Hand-built and template career sites often publish no schema.org markup but
    render each job attribute as a label element followed by a value element,
    e.g. ``<h5>Location</h5><span>Guntur, Andhra Pradesh</span>``. The first
    ``<h1>`` is used as the title when present. Returns ``{}`` when nothing is
    recognised, so callers fall through to the metadata fallback.
    """
    parser = _ElementTextParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must not abort a crawl
        return {}

    texts = parser.texts
    out: Dict[str, str] = {}
    if parser.headings:
        out["title"] = parser.headings[0]
    for index, text in enumerate(texts):
        field = _LABEL_FIELDS.get(_label_key(text))
        if not field or field in out or index + 1 >= len(texts):
            continue
        value = texts[index + 1].strip()
        # Inline layouts (``<p><strong>Location:</strong> Guntur</p>``) repeat the
        # label inside the value; strip that leading label back off.
        if value.lower().startswith(text.lower()):
            value = value[len(text):].lstrip(" :\u2013\u2014-").strip()
        if value and _label_key(value) not in _LABEL_FIELDS:
            out[field] = value
    return out


def _label_key(text: str) -> str:
    return re.sub(r"[\s:*.]+$", "", (text or "").strip().lower())


def extract_meta(html: str) -> Dict[str, str]:
    """Minimal fallback metadata for pages that publish no JSON-LD."""
    metas = _meta_map(html)
    title = metas.get("og:title", "")
    if not title:
        match = _TITLE_RE.search(html or "")
        if match:
            title = match.group(1)
    return {
        "title": clean_html(title),
        "description": clean_html(metas.get("og:description") or metas.get("description", "")),
        "job_url": metas.get("og:url", ""),
    }


def _meta_map(html: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for tag in _META_TAG_RE.findall(html or ""):
        attrs = {name.lower(): value for name, value in _ATTR_RE.findall(tag)}
        key = attrs.get("property") or attrs.get("name")
        if key and "content" in attrs:
            out.setdefault(key.lower(), attrs["content"].strip())
    return out


def _type_names(node: Dict[str, Any]) -> set:
    value = node.get("@type")
    if isinstance(value, str):
        return {value.strip().lower()}
    if isinstance(value, list):
        return {str(item).strip().lower() for item in value}
    return set()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value).strip()
    if isinstance(value, list):
        for item in value:
            text = _text(item)
            if text:
                return text
        return ""
    if isinstance(value, dict):
        for key in ("@value", "value", "name", "text"):
            if key in value:
                text = _text(value[key])
                if text:
                    return text
    return ""


def _url(value: Any) -> str:
    if isinstance(value, dict):
        return _text(value.get("url")) or _text(value.get("@id"))
    return _text(value)


def _identifier(value: Any) -> str:
    if isinstance(value, list):
        for item in value:
            identifier = _identifier(item)
            if identifier:
                return identifier
        return ""
    if isinstance(value, dict):
        return _text(value.get("value")) or _text(value.get("name"))
    return _text(value)


def _address(place: Any) -> Tuple[str, str, str, str]:
    """Return ``(city, region, country, location_text)`` from a ``jobLocation``."""
    if isinstance(place, list):
        place = place[0] if place else None
    if isinstance(place, str):
        return "", "", "", place.strip()
    if not isinstance(place, dict):
        return "", "", "", ""

    address = place.get("address")
    if isinstance(address, list):
        address = address[0] if address else None
    if not isinstance(address, dict):
        return "", "", "", _text(place.get("name")) or _text(address)

    city = _text(address.get("addressLocality"))
    region = _text(address.get("addressRegion"))
    country = _country_name(_text(address.get("addressCountry")))
    location_text = ", ".join(part for part in (city, region, country) if part)
    return city, region, country, location_text


def _country_name(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return _COUNTRY_ALIASES.get(text.lower(), text)


def _salary(value: Any) -> Tuple[str, str, str]:
    """Return ``(min_salary, max_salary, currency)`` from a ``baseSalary`` node."""
    if not isinstance(value, dict):
        return "", "", ""
    currency = _text(value.get("currency")).upper()
    inner = value.get("value")
    if isinstance(inner, list):
        inner = inner[0] if inner else None
    if isinstance(inner, dict):
        low = _number(_text(inner.get("minValue")))
        high = _number(_text(inner.get("maxValue")))
        single = _number(_text(inner.get("value")))
    else:
        low = high = single = _number(_text(inner))
    low = low or single
    high = high or single
    return low, high, currency


def _number(value: str) -> str:
    text = (value or "").strip().replace(",", "")
    if not text:
        return ""
    try:
        number = float(text)
    except ValueError:
        return ""
    return str(int(number)) if number.is_integer() else f"{number:g}"
