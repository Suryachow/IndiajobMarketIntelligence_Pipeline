"""Normalization helpers: turn platform-specific raw values into the canonical
schema values used by :class:`jobintel.schema.JobPosting`.

Design rules
------------
* Never invent data. If a value cannot be confidently derived it is returned as
  an empty string.
* Normalization is intentionally *conservative*: heuristics only fire on
  explicit textual signals (e.g. the word "hybrid"), so the output stays
  auditable against the source posting.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import html as _html
import re
from typing import Any, Optional, Tuple

# ---------------------------------------------------------------------------
# HTML / text cleaning
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_BR_RE = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_BLOCK_END_RE = re.compile(r"</\s*(p|div|li|tr|h[1-6])\s*>", re.IGNORECASE)
_MULTISPACE_RE = re.compile(r"[ \t\u00a0]+")
_MULTINEWLINE_RE = re.compile(r"\n{3,}")


def clean_html(value: Optional[str]) -> str:
    """Convert an HTML fragment into readable plain text."""
    if not value:
        return ""
    text = _SCRIPT_RE.sub(" ", str(value))
    text = _BR_RE.sub("\n", text)
    text = _BLOCK_END_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = _html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTISPACE_RE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTINEWLINE_RE.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d-%m-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %b %Y",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
)

_REL_DAYS_RE = re.compile(r"(\d+)\s*\+?\s*days?\s*ago", re.IGNORECASE)
# SAP SuccessFactors / Phenom publish ctime-style dates, e.g.
# "Fri Oct 30 00:00:00 UTC 2026".
_CTIME_RE = re.compile(
    r"^(?:[A-Za-z]{3,9},?\s+)?([A-Za-z]{3,9})\s+(\d{1,2})\s+(\d{1,2}:\d{2}(?::\d{2})?)\s+"
    r"(?:[A-Z]{2,5}\s+)?(\d{4})$"
)


def parse_posting_date(value: Optional[str], today: Optional[_dt.date] = None) -> str:
    """Normalize a posting date into ISO ``YYYY-MM-DD`` (or ``''`` if unknown).

    Handles Workday relative strings ("Posted Today", "Posted 3 Days Ago"),
    Amazon ("November  5, 2026") and ISO/epoch values.
    """
    if value is None:
        return ""
    raw = str(value).strip()
    if not raw:
        return ""
    today = today or _dt.date.today()
    low = raw.lower()

    if "today" in low:
        return today.isoformat()
    if "yesterday" in low:
        return (today - _dt.timedelta(days=1)).isoformat()
    m = _REL_DAYS_RE.search(low)
    if m:
        return (today - _dt.timedelta(days=int(m.group(1)))).isoformat()

    # Epoch seconds / millis
    if raw.isdigit():
        try:
            n = int(raw)
            if n > 10_000_000_000:
                n //= 1000
            return _dt.datetime.utcfromtimestamp(n).date().isoformat()
        except (ValueError, OverflowError, OSError):
            return ""

    # SuccessFactors / Phenom ctime-style: "Fri Oct 30 00:00:00 UTC 2026"
    m = _CTIME_RE.match(raw)
    if m:
        cleaned = f"{m.group(1)} {m.group(2)} {m.group(3)} {m.group(4)}"
        for fmt in ("%b %d %H:%M:%S %Y", "%B %d %H:%M:%S %Y", "%b %d %H:%M %Y", "%B %d %H:%M %Y"):
            try:
                return _dt.datetime.strptime(cleaned, fmt).date().isoformat()
            except ValueError:
                continue

    candidate = raw.replace("  ", " ")
    for fmt in _DATE_FORMATS:
        for probe in (candidate, candidate.rstrip("Z")):
            try:
                return _dt.datetime.strptime(probe, fmt).date().isoformat()
            except ValueError:
                continue
    iso = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if iso:
        return iso.group(1)
    return ""


# ---------------------------------------------------------------------------
# Location: India detection + city/state/country parsing
# ---------------------------------------------------------------------------

INDIA_STATES = {
    "andhra pradesh", "arunachal pradesh", "assam", "bihar", "chhattisgarh", "goa",
    "gujarat", "haryana", "himachal pradesh", "jharkhand", "karnataka", "kerala",
    "madhya pradesh", "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland",
    "odisha", "orissa", "punjab", "rajasthan", "sikkim", "tamil nadu", "telangana",
    "tripura", "uttar pradesh", "uttarakhand", "west bengal", "delhi", "new delhi",
    "jammu and kashmir", "jammu & kashmir", "ladakh", "puducherry", "pondicherry",
    "chandigarh", "andaman and nicobar islands", "dadra and nagar haveli",
    "daman and diu", "lakshadweep", "telengana", "up_east", "up_west",
}

INDIA_CITIES = {
    "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune", "mumbai", "bombay",
    "navi mumbai", "thane", "chennai", "madras", "kolkata", "calcutta", "delhi",
    "new delhi", "noida", "greater noida", "gurugram", "gurgaon", "faridabad",
    "ghaziabad", "ahmedabad", "jaipur", "surat", "kochi", "cochin", "ernakulam",
    "thiruvananthapuram", "trivandrum", "coimbatore", "indore", "bhopal", "nagpur",
    "nashik", "vadodara", "baroda", "chandigarh", "mohali", "lucknow", "kanpur",
    "varanasi", "patna", "ranchi", "bhubaneswar", "visakhapatnam", "vizag",
    "vijayawada", "mysuru", "mysore", "mangaluru", "mangalore", "madurai",
    "tiruchirappalli", "trichy", "salem", "dehradun", "guwahati", "raipur",
    "jodhpur", "udaipur", "agra", "meerut", "amritsar", "ludhiana", "jalandhar",
    "srinagar", "shimla", "panaji", "howrah", "warangal", "nellore", "tirupati",
    "guntur", "hubli", "dharwad", "belagavi", "belgaum", "kolhapur", "aurangabad",
    "solapur", "tiruppur", "erode", "vellore", "puducherry", "pondicherry",
    "bhilai", "jamshedpur", "dhanbad", "bareilly", "gorakhpur", "aligarh",
    "moradabad", "saharanpur", "kota", "ajmer", "bikaner", "bhavnagar", "rajkot",
    "jamnagar", "gandhinagar", "anand", "vapi", "silvassa", "daman", "ambala",
    "karnal", "rohtak", "hisar", "panipat", "sonipat", "deoria", "ncr",
    "delhi ncr", "delhi-ncr", "mumbai suburban", "thane west", "airoli", "powai",
    "whitefield", "electronic city", "gurgaon sector", "okhla", "andheri",
    "bandra", "worli", "lower parel", "kharadi", "hinjewadi", "magarpatta",
    "viman nagar", "baner", "wakad", "gachibowli", "madhapur", "hitech city",
    "kondapur", "manesar", "bidadi", "hosur", "sriperumbudur", "ooty",
}

_INDIA_WORD_RE = re.compile(r"\bindia\b", re.IGNORECASE)
_ISO_IN_RE = re.compile(r"(?:^|,)\s*(in|ind)\s*(?:$|,)")


def is_india_location(text: Optional[str]) -> bool:
    """Best-effort determination that a location string refers to India.

    Matches the country name, the ISO country code as a *standalone* token, any
    Indian state, or any major Indian city. Returns ``False`` for e.g.
    "Indiana, US" because of the word-boundary on "India".
    """
    if not text:
        return False
    raw = str(text).lower()
    if _INDIA_WORD_RE.search(raw):
        return True
    if _ISO_IN_RE.search(str(text)):
        return True
    tokens = [t.strip() for t in re.split(r"[,/|()\-]", raw) if t.strip()]
    for token in tokens:
        if token in INDIA_STATES or token in INDIA_CITIES:
            return True
    # Multi-word tokens (e.g. "delhi ncr" already covered, but check substrings)
    for state in INDIA_STATES:
        if " " in state and state in raw:
            return True
    for city in INDIA_CITIES:
        if " " in city and city in raw:
            return True
    return False


def parse_location(text: Optional[str]) -> Tuple[str, str, str]:
    """Split a raw location string into ``(city, state, country)``.

    Handles both Workday orderings ("India, Karnataka, Bengaluru" and
    "Acton, Massachusetts, US") plus Amazon's "IN, MH, Maharashtra".
    """
    if not text:
        return "", "", ""
    parts = [p.strip() for p in re.split(r",", str(text)) if p.strip()]
    if not parts:
        return "", "", ""

    country = ""
    if parts and parts[-1].lower() in {"india", "in", "ind"}:
        country, parts = "India", parts[:-1]
    elif parts and parts[0].lower() in {"india", "in", "ind"}:
        country, parts = "India", parts[1:]

    parts = [p for p in parts if p.lower() not in {"remote", "hybrid", "onsite", "on-site"}]

    city, state = "", ""
    if len(parts) == 1:
        token = parts[0]
        if token.lower() in INDIA_STATES:
            state = token.title()
        else:
            city = token.title()
    elif len(parts) >= 2:
        first, last = parts[0], parts[-1]
        if last.lower() in INDIA_STATES:
            state, city = last.title(), first.title()
        elif first.lower() in INDIA_STATES:
            state, city = first.title(), last.title()
        else:
            city, state = first.title(), last.title()
    if country == "" and (state or city):
        if (state.lower() in INDIA_STATES) or (city.lower() in INDIA_CITIES) or is_india_location(text):
            country = "India"
    return city, state, country


# ---------------------------------------------------------------------------
# Employment type / work arrangement / experience / salary
# ---------------------------------------------------------------------------

_EMPLOYMENT_PATTERNS = (
    (re.compile(r"\bintern(ship)?\b", re.I), "Internship"),
    (re.compile(r"\bapprentice(ship)?\b", re.I), "Apprenticeship"),
    (re.compile(r"\bpart[\s-]?time\b", re.I), "Part-time"),
    (re.compile(r"\bfull[\s-]?time\b", re.I), "Full-time"),
    (re.compile(r"\bcontract(or)?\b", re.I), "Contract"),
    (re.compile(r"\btemporary\b", re.I), "Temporary"),
    (re.compile(r"\bcasual\b", re.I), "Casual"),
    (re.compile(r"\bfixed[\s-]?term\b", re.I), "Fixed-term"),
)


def normalize_employment_type(*values: Optional[str]) -> str:
    """Map a free-text employment type onto a controlled vocabulary."""
    for value in values:
        if not value:
            continue
        for pattern, label in _EMPLOYMENT_PATTERNS:
            if pattern.search(str(value)):
                return label
    return ""


# Controlled vocabulary for structured ATS workplace/remote fields.
_WORKPLACE_KEYWORDS = {
    "remote": "Remote",
    "fully remote": "Remote",
    "virtual": "Remote",
    "hybrid": "Hybrid",
    "flexible": "Hybrid",
    "on-site": "Onsite",
    "onsite": "Onsite",
    "on site": "Onsite",
    "office": "Onsite",
    "in-office": "Onsite",
}


def map_workplace_type(value: Optional[str]) -> str:
    """Map a structured ATS workplace/remote field to the controlled vocabulary.

    Used for authoritative fields (Workday ``remoteType``, Lever
    ``workplaceType``, SmartRecruiters ``location.remote``). Returns "" for an
    unrecognised or "unspecified" value.
    """
    if not value:
        return ""
    return _WORKPLACE_KEYWORDS.get(str(value).strip().lower(), "")


# Location-scoped signals: here a bare "remote"/"virtual" is authoritative
# (e.g. Amazon publishes "IN, TS, Hyderabad - Virtual" for remote roles).
_LOCATION_ARRANGEMENT_PATTERNS = (
    (re.compile(r"\bhybrid\b", re.I), "Hybrid"),
    (re.compile(r"\bremote\b|\bvirtual\b", re.I), "Remote"),
)

# Free-text signals. Only fairly explicit phrases fire, because a bare word in a
# long advert is often boilerplate (e.g. "collaborate with remote teams"). The
# onsite default is applied by the caller when nothing is stated.
_TEXT_ARRANGEMENT_PATTERNS = (
    (re.compile(r"\bhybrid\b", re.I), "Hybrid"),
    (re.compile(
        r"\bfully[\s-]remote\b|\b100\s*%\s*remote\b|\bwork from home\b|\bwfh\b|"
        r"\bwork from anywhere\b|\bremote[\s-]first\b|\btelecommut\w*|"
        r"\bwork remotely\b|\bremote\s(?:position|role|job|work|working|opportunity)\b",
        re.I,
    ), "Remote"),
    (re.compile(
        r"\bon[\s-]?site\b|\bin[\s-]?office\b|\boffice[\s-]?based\b|"
        r"\bwork from office\b|\bwfo\b",
        re.I,
    ), "Onsite"),
)


def infer_work_arrangement(location: Optional[str], *extra_text: Optional[str]) -> str:
    """Infer the work arrangement from explicit signals only.

    The ``location`` string is matched first (where a bare "virtual" implies
    remote); the remaining haystacks - a structured ATS value and/or description
    text - are then matched against face-value phrases. Returns ``""`` when
    nothing is stated; the caller applies the onsite default.
    """
    if location:
        for pattern, label in _LOCATION_ARRANGEMENT_PATTERNS:
            if pattern.search(str(location)):
                return label
    for hay in extra_text:
        if not hay:
            continue
        for pattern, label in _TEXT_ARRANGEMENT_PATTERNS:
            if pattern.search(str(hay)):
                return label
    return ""


_EXPERIENCE_RE = re.compile(
    r"(\d{1,2})\s*(?:-|to|–)\s*(\d{1,2})\s*\+?\s*(?:years|yrs)",
    re.IGNORECASE,
)
_EXPERIENCE_MIN_RE = re.compile(
    r"(?:minimum|min\.?|at least|over|more than)\s*(?:of\s*)?(\d{1,2})\s*\+?\s*(?:years|yrs)",
    re.IGNORECASE,
)
_EXPERIENCE_PLUS_RE = re.compile(r"(\d{1,2})\s*\+\s*(?:years|yrs)", re.IGNORECASE)


def extract_experience(*values: Optional[str]) -> str:
    """Extract an explicit years-of-experience statement, if one is published."""
    for value in values:
        if not value:
            continue
        text = str(value)
        m = _EXPERIENCE_RE.search(text)
        if m:
            return f"{m.group(1)}-{m.group(2)} years"
        m = _EXPERIENCE_MIN_RE.search(text)
        if m:
            return f"Minimum {m.group(1)} years"
        m = _EXPERIENCE_PLUS_RE.search(text)
        if m:
            return f"{m.group(1)}+ years"
    return ""


_CURRENCY_HINTS = (
    ("INR", re.compile(r"\bINR\b|\u20b9|rs\.?\s*\d|\brupees?\b", re.I)),
    ("USD", re.compile(r"\bUSD\b|\$\s*\d", re.I)),
    ("EUR", re.compile(r"\bEUR\b|\u20ac\s*\d", re.I)),
    ("GBP", re.compile(r"\bGBP\b|\u00a3\s*\d", re.I)),
)

# A currency symbol/code that may introduce a pay figure.
_CURRENCY = r"(?:INR|Rs\.?|\u20b9|\$|USD|\u20ac|EUR|\u00a3|GBP)"
# Recognised salary units/periods. "crore", "million" and "billion" are
# deliberately excluded: in these sources they describe revenue, funding or
# head-count far more often than pay.
_SALARY_UNIT = r"(?:lpa|lakhs?|lacs?|k|per\s*annum|per\s*year|per\s*month|p\.?a\.?)"
_SALARY_KEYWORD_RE = re.compile(
    r"\b(?:salar(?:y|ies)|ctc|compensation|stipend|remuneration)\b", re.I
)
# Words that mark a monetary figure as *not* pay (revenue, loans, head-count...).
_MONEY_NOISE_RE = re.compile(
    r"\b(?:crore|million|billion|revenue|turnover|valuation|funding|raised|"
    r"loans?|deposits?|interest|assets?|tcv|aum|budget|premium|penalt|fines?|fees?|"
    r"grant|customers?|users?|employees?|clients?|stores?|people|companies|"
    r"downloads?|merchants?|deals?|worth|opportunit\w*|pipeline|gmv|tam|"
    r"capex|opex|savings?|backlog|contract|spend|order\s*book)\b",
    re.I,
)
# "5M" / "3B" magnitudes mark a business metric (deal size, funding), not a
# salary; real pay is expressed in rupees/lakhs/LPA, never millions.
_MAGNITUDE_NOISE_RE = re.compile(r"\b\d[\d,]*(?:\.\d+)?\s*[MB]\b", re.I)
_PERIOD_RE = re.compile(r"per[\s\-]*month|/\s*month|monthly|\bp\.?m\.?\b", re.I)

# Currency-led range, e.g. "Rs 50,000 - 80,000", "₹18k - ₹40k", "$90k to $120k".
# Groups: 1=low number, 2=low unit, 3=high number, 4=high unit.
_SALARY_RANGE_RE = re.compile(
    rf"{_CURRENCY}\s*([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?"
    rf"\s*(?:-|to|\u2013|\u2014)\s*"
    rf"{_CURRENCY}?\s*([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?",
    re.IGNORECASE,
)
# Unit-led range without a leading currency symbol, e.g. "12-15 LPA", "8 to 12 lakhs".
# The trailing unit is mandatory (the leading one may be implicit).
_SALARY_UNIT_RANGE_RE = re.compile(
    rf"\b([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?\s*(?:-|to|\u2013|\u2014)\s*"
    rf"([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})\b",
    re.IGNORECASE,
)
# A single value, optionally carrying a salary unit, e.g. "₹12 LPA", "CTC: 8,00,000".
_SALARY_SINGLE_RE = re.compile(
    rf"{_CURRENCY}?\s*([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?",
    re.IGNORECASE,
)
# Labelled min/max pair, used by Workday-style postings that publish pay as two
# separate lines, e.g. "Minimum Salary: $170,000 ... Maximum Salary: $230,000".
# The connector between a label and its figure may not cross a newline, so the
# "minimum" label can never swallow the figure that belongs to "maximum".
# Groups: 1 = currency, 2 = number, 3 = unit.
_SALARY_MIN_LABEL_RE = re.compile(
    rf"\b(?:minimum|min|base|starting)\s+(?:salary|pay|compensation|rate|ctc)\b"
    rf"[^\d\n]{{0,15}}?({_CURRENCY})?\s*([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?",
    re.IGNORECASE,
)
_SALARY_MAX_LABEL_RE = re.compile(
    rf"\b(?:maximum|max|top|upper)\s+(?:salary|pay|compensation|rate|ctc)\b"
    rf"[^\d\n]{{0,15}}?({_CURRENCY})?\s*([\d,]+(?:\.\d+)?)\s*({_SALARY_UNIT})?",
    re.IGNORECASE,
)


def extract_salary(text: Optional[str]) -> Tuple[str, str, str]:
    """Return ``(min_salary, max_salary, currency)`` from a published pay figure.

    The assignment explicitly forbids fabricating salary data, so this only fires
    when the source text contains an explicit, machine-detectable pay range (or a
    single value sitting next to the word "salary"/"CTC"/...). Monetary prose such
    as "revenue grew to Rs 1,370 crore" or "loans from ₹50,000 to ₹5 lakh" is
    rejected via :data:`_MONEY_NOISE_RE`. Monthly figures are annualised (x12) so
    the column always represents an annual amount.
    """
    if not text:
        return "", "", ""
    raw = str(text)
    currency = _detect_currency(raw)
    if not currency and re.search(r"\blpa\b|\blakhs?\b|\blacs?\b|\brupees?\b", raw, re.I):
        currency = "INR"  # LPA/lakh/rupee implies rupee-denominated pay

    # 1) Labelled minimum/maximum pair, e.g. Workday postings that publish pay
    #    on two lines ("Minimum Salary: $170,000" / "Maximum Salary: $230,000").
    #    Both labels must be present; a lone label is left to the branches below.
    min_label = _SALARY_MIN_LABEL_RE.search(raw)
    max_label = _SALARY_MAX_LABEL_RE.search(raw)
    if min_label and max_label:
        window = raw[max(0, min_label.start() - 40): max_label.end() + 40]
        if not (_MONEY_NOISE_RE.search(window) or _MAGNITUDE_NOISE_RE.search(window)):
            lo = _to_number(min_label.group(2), min_label.group(3))
            hi = _to_number(max_label.group(2), max_label.group(3))
            if lo is not None and hi is not None:
                if lo > hi:
                    lo, hi = hi, lo
                scale = 12 if _PERIOD_RE.search(window) else 1
                currency = (
                    _detect_currency(min_label.group(0))
                    or _detect_currency(max_label.group(0))
                    or currency
                    or "INR"
                )
                return _fmt_number(lo * scale), _fmt_number(hi * scale), currency

    # 2) Explicit range - currency-led first, then unit-led ("12-15 LPA").
    for pattern in (_SALARY_RANGE_RE, _SALARY_UNIT_RANGE_RE):
        for m in pattern.finditer(raw):
            window = raw[max(0, m.start() - 60): m.end() + 60]
            if _MONEY_NOISE_RE.search(window) or _MAGNITUDE_NOISE_RE.search(window):
                continue  # revenue / loan / deal-size figure, not pay
            if not _has_salary_signal(window, m):
                continue
            # "12-15 LPA": the unit trails the range but applies to both ends.
            unit_lo, unit_hi = m.group(2), m.group(4)
            unit_lo = unit_lo or unit_hi
            unit_hi = unit_hi or unit_lo
            lo = _to_number(m.group(1), unit_lo)
            hi = _to_number(m.group(3), unit_hi)
            if lo is None or hi is None:
                continue
            if lo > hi:
                lo, hi = hi, lo
            scale = 12 if _PERIOD_RE.search(window) else 1
            return _fmt_number(lo * scale), _fmt_number(hi * scale), currency or "INR"

    # 3) A single value is trusted only when it sits immediately beside the word
    #    salary/CTC/compensation, e.g. "CTC: 8,00,000". Figures buried in later
    #    prose are not pay and must never be picked up.
    keyword = _SALARY_KEYWORD_RE.search(raw)
    if keyword:
        tail = raw[keyword.end(): keyword.end() + 60]
        if not _MONEY_NOISE_RE.search(tail):
            m = _SALARY_SINGLE_RE.search(tail)
            if m:
                # Only a short, punctuation-free connector may separate the
                # keyword from the figure ("', "-", ":", "of", "approx", "upto").
                connector = tail[: m.start()]
                adjacent = (
                    len(connector) <= 24
                    and not re.search(r"[.!?;]|\n", connector)
                    and len(connector.split()) <= 3
                )
                value = _to_number(m.group(1), m.group(2))
                has_unit = bool(m.group(2))
                has_currency = bool(re.search(_CURRENCY, m.group(0)))
                # A bare 4-digit number with neither a unit nor a currency symbol
                # is a year ("...clear-to-close (CTC) status ... 2022"), not pay.
                looks_like_year = (
                    value is not None
                    and not has_unit
                    and not has_currency
                    and 1900 <= value <= 2099
                    and float(value).is_integer()
                )
                # Otherwise a bare figure must be substantial to count as pay.
                if (
                    adjacent
                    and value is not None
                    and (has_unit or has_currency or value >= 1000)
                    and not looks_like_year
                ):
                    if _PERIOD_RE.search(tail):
                        value *= 12
                    return _fmt_number(value), _fmt_number(value), currency or "INR"
    return "", "", ""


def salary_from_structured(*candidates: Any) -> Tuple[str, str, str]:
    """Map an ATS-supplied pay object/list to ``(min, max, currency)``.

    Handles Lever's ``salaryRange`` (``min``/``max``/``currency``/``interval``),
    Greenhouse's ``pay_input_ranges`` (``min_cents``/``max_cents``/
    ``currency_type``) and Ashby's typed components (``minValue``/``maxValue``/
    ``currencyCode`` with a ``compensationType``). Returns empty strings when
    nothing usable is present.
    """
    for candidate in candidates:
        result = _structured_pay(candidate)
        if result and (result[0] or result[1]):
            return result
    return "", "", ""


def _structured_pay(candidate: Any) -> Optional[Tuple[str, str, str]]:
    if not candidate:
        return None
    items = candidate if isinstance(candidate, list) else [candidate]
    for item in items:
        if not isinstance(item, dict) or item.get("blur"):
            continue
        # Ashby tags each component with its type; equity/bonus rows must never
        # be mistaken for base pay even when they carry values.
        comp_type = str(item.get("compensationType") or "")
        if comp_type and "salary" not in comp_type.lower():
            continue
        lo = item.get("min", item.get("minValue"))
        hi = item.get("max", item.get("maxValue"))
        if lo is None and item.get("min_cents") is not None:
            lo = _from_cents(item.get("min_cents"))
        if hi is None and item.get("max_cents") is not None:
            hi = _from_cents(item.get("max_cents"))
        if lo is None and hi is None:
            continue
        scale = 12 if re.search(r"month", str(item.get("interval", "")), re.I) else 1
        lo = None if lo is None else float(lo) * scale
        hi = None if hi is None else float(hi) * scale
        # A zeroed range (e.g. Lever boards that enable salaryRange but publish
        # 0/0) is an "amount not disclosed" placeholder, not real pay.
        if (lo or 0) <= 0 and (hi or 0) <= 0:
            continue
        currency = str(
            item.get("currency") or item.get("currency_type") or item.get("currencyCode") or ""
        ).upper()
        return (
            _fmt_number(lo) if lo is not None else "",
            _fmt_number(hi) if hi is not None else "",
            currency,
        )
    return None


def _has_salary_signal(window: str, match: "re.Match") -> bool:
    """A range counts as pay only when anchored by a salary unit, a currency
    symbol/code, or an explicit salary keyword."""
    matched = match.group(0)
    return bool(
        re.search(_SALARY_UNIT, matched, re.I)
        or re.search(_CURRENCY, matched)
        or _SALARY_KEYWORD_RE.search(window)
    )


def _detect_currency(text: str) -> str:
    for code, pattern in _CURRENCY_HINTS:
        if pattern.search(text):
            return code
    return ""


def _fmt_number(value: float) -> str:
    """Render a number without scientific notation (1,200,000 -> '1200000')."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:g}"


def _to_number(value: str, unit: Optional[str]) -> Optional[float]:
    try:
        num = float(value.replace(",", ""))
    except (ValueError, AttributeError):
        return None
    if unit:
        u = unit.lower()
        if u.startswith("lpa") or u.startswith("lakh") or u.startswith("lac"):
            num *= 100_000
        elif u == "k":
            num *= 1_000
    return num


def _from_cents(value: Any) -> Optional[float]:
    try:
        return float(value) / 100.0
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Hashing / identifiers
# ---------------------------------------------------------------------------

def make_record_hash(company_slug: str, job_id: str, title: str, location: str) -> str:
    """Stable content hash used for change detection and duplicate removal."""
    seed = "|".join([
        (company_slug or "").strip().lower(),
        (job_id or "").strip().lower(),
        (title or "").strip().lower(),
        (location or "").strip().lower(),
    ])
    return hashlib.sha1(seed.encode("utf-8")).hexdigest()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (value or "").strip().lower()).strip("-")


def normalize_title(title: str) -> str:
    """Collapse a title for equality comparison during deduplication."""
    text = (title or "").lower()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(female|male|urgent|hiring|immediate joiner)\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()
