"""Resolve a company **name** to its official careers entry point.

Lets the universal crawler be driven by a name the user already knows
(``--company "Wipro"``) instead of a URL. Resolution is a four-step lookup:

1. a curated, hand-verified **directory** (:file:`config/careers_directory.json`)
   of first-party employer careers URLs - *data, not code*, so onboarding an
   employer never touches Python;
2. the project's own :file:`config/companies.json` registry;
3. a tolerant **fuzzy** match (``difflib``) over both, so "tata consultancy",
   "TCS" and "Tata Consultancy Services" all resolve;
4. optional **automatic discovery** - when a name matches nothing, the caller
   may supply an HTTP client and the resolver derives candidate domains, reads
   the company's own homepage and returns the first-party careers link it finds.

Nothing is ever *guessed into* the dataset: a URL is only returned once it has
been read off the employer's own site, keeping the "first-party sources only"
rule intact. If no match is found the resolver returns ``None`` and the caller
asks for an explicit URL.
"""

from __future__ import annotations

import json
import os
import re
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urljoin, urlsplit

from .detect import detect_platform

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_REGISTRY = os.path.join(_HERE, "..", "..", "config", "companies.json")
DEFAULT_DIRECTORY = os.path.join(_HERE, "..", "..", "config", "careers_directory.json")

_SUFFIXES = (
    "private", "pvt", "limited", "ltd", "llp", "inc", "incorporated", "corp",
    "corporation", "company", "co", "technologies", "technology", "techno",
    "solutions", "services", "systems", "software", "group", "holdings",
    "india", "global", "enterprises",
)
_PUNCT_RE = re.compile(r"[^a-z0-9]+")
_MATCH_THRESHOLD = 0.86

# -- automatic discovery tuning -------------------------------------------
# TLD/domain shapes worth probing when we have to find a careers site ourselves.
_DOMAIN_SUFFIXES = ("com", "in", "co.in")
_CAREER_PATHS = ("/careers", "/careers/", "/jobs", "/company/careers", "/en/careers")
_CAREER_WORD_RE = re.compile(
    r"careers?|jobs?|vacanc|opening|position|hiring|recruit", re.IGNORECASE
)
_ANCHOR_RE = re.compile(r"<a\b[^>]*?href\s*=\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Never probe or accept a third-party job board.
_AGGREGATOR_RE = re.compile(
    r"linkedin|naukri|indeed|glassdoor|monster|timesjobs|shine\.com|foundit|"
    r"simplyhired|ziprecruiter|dice\.com|wellfound|angel\.co|ambitionbox|"
    r"instahyre|cutshort|hirect|upwork|freelancer",
    re.IGNORECASE,
)


def load_directory(path: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """Read the curated careers directory keyed by lower-cased company name."""
    directory_path = path or DEFAULT_DIRECTORY
    try:
        with open(directory_path, encoding="utf-8") as handle:
            data: Any = json.load(handle)
    except (OSError, ValueError):
        return {}
    entries = data.get("companies", data) if isinstance(data, dict) else data
    out: Dict[str, Dict[str, Any]] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("company_name") or entry.get("name") or ""
        url = entry.get("careers_url") or entry.get("url") or ""
        if not name or not url:
            continue
        out[name.strip().lower()] = {
            "company_name": name.strip(),
            "industry": entry.get("industry", ""),
            "careers_url": url,
            "aliases": entry.get("aliases", []) or [],
            "extractor": entry.get("extractor", ""),
        }
    return out


DIRECTORY: Dict[str, Dict[str, Any]] = load_directory()


def load_registry(path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read employer specs from the company registry (best-effort)."""
    registry_path = path or DEFAULT_REGISTRY
    try:
        with open(registry_path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = data.get("companies", data) if isinstance(data, dict) else data
    specs: List[Dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        url = entry.get("careers_url") or entry.get("url") or ""
        if not url:
            continue
        specs.append({
            "company_name": entry.get("company_name", ""),
            "industry": entry.get("industry", ""),
            "careers_url": url,
            "extractor": entry.get("extractor", ""),
        })
    return specs


def _catalogue(
    registry_path: Optional[str] = None,
    directory_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Combine the curated directory with the registry into matchable entries."""
    directory = load_directory(directory_path) if directory_path else DIRECTORY
    entries: List[Dict[str, Any]] = []
    for meta in directory.values():
        entries.append({
            "company_name": meta["company_name"],
            "industry": meta.get("industry", ""),
            "careers_url": meta["careers_url"],
            "aliases": meta.get("aliases", []),
            "extractor": meta.get("extractor", ""),
        })
    for spec in load_registry(registry_path):
        entries.append({
            "company_name": spec.get("company_name", ""),
            "industry": spec.get("industry", ""),
            "careers_url": spec["careers_url"],
            "aliases": [spec.get("company_name", "")],
            "extractor": spec.get("extractor", ""),
        })
    return entries


def _normalize(name: str) -> str:
    text = (name or "").strip().lower()
    text = _PUNCT_RE.sub(" ", text)
    tokens = [t for t in text.split() if t and t not in _SUFFIXES]
    if not tokens:
        tokens = [t for t in text.split() if t]
    return " ".join(tokens)


def _candidates(entry: Dict[str, Any]) -> List[str]:
    seen: List[str] = []
    for value in [entry.get("company_name", "")] + list(entry.get("aliases", [])):
        norm = _normalize(value)
        if norm and norm not in seen:
            seen.append(norm)
    return seen


def resolve(
    name: str,
    registry_path: Optional[str] = None,
    client: Any = None,
    discover: bool = False,
) -> Optional[Dict[str, Any]]:
    """Resolve ``name`` to a careers spec, or ``None`` if nothing matches.

    Pass ``discover=True`` **and** an HTTP ``client`` (anything exposing
    ``get_page(url) -> html``) to fall back to automatic first-party discovery
    when the name is not already known.
    """
    query = _normalize(name)
    if not query:
        return None
    entries = _catalogue(registry_path)

    # 1. exact match on a canonical name or alias
    for entry in entries:
        if query in _candidates(entry):
            return _spec(entry)

    # 2. fuzzy match (guards against typos and word-order differences)
    best: Optional[Dict[str, Any]] = None
    best_score = 0.0
    for entry in entries:
        for candidate in _candidates(entry):
            score = _similarity(query, candidate)
            if score > best_score:
                best_score, best = score, entry
    if best is not None and best_score >= _MATCH_THRESHOLD:
        spec = _spec(best)
        spec["match_score"] = round(best_score, 2)
        return spec

    # 3. automatic discovery straight from the employer's own website
    if discover and client is not None:
        return _resolve_by_discovery(name, client)
    return None


def resolve_many(
    names: List[str],
    registry_path: Optional[str] = None,
    client: Any = None,
    discover: bool = False,
) -> List[Dict[str, Any]]:
    """Resolve several names, preserving order and skipping unmatched ones."""
    specs: List[Dict[str, Any]] = []
    seen: set = set()
    for name in names:
        spec = resolve(name, registry_path, client=client, discover=discover)
        if not spec or spec["careers_url"] in seen:
            continue
        seen.add(spec["careers_url"])
        specs.append(spec)
    return specs


def list_companies(registry_path: Optional[str] = None) -> List[str]:
    """Return the sorted names the resolver currently knows about."""
    names = {entry["company_name"] for entry in _catalogue(registry_path) if entry["company_name"]}
    return sorted(names, key=str.lower)


# -- spec loading / resolution (shared by run.py and crawl.py) -------------
def load_specs(path: str) -> List[Dict[str, Any]]:
    """Read employer specs from a JSON or CSV file.

    JSON accepts the registry/directory shape (``{"companies": [...]}``) or a
    bare list. CSV accepts an ``industry,company_name`` table with a header row
    (a ``careers_url`` column is honoured when present). An entry may carry a
    name only - :func:`resolve_specs` fills in everything else.
    """
    if not path:
        return []
    if path.lower().endswith(".csv"):
        return _load_specs_csv(path)
    try:
        with open(path, encoding="utf-8") as handle:
            data: Any = json.load(handle)
    except (OSError, ValueError):
        return []
    entries = data.get("companies", data) if isinstance(data, dict) else data
    specs: List[Dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, str):
            specs.append({"careers_url": entry})
            continue
        if not isinstance(entry, dict):
            continue
        config = entry.get("config") or {}
        specs.append({
            "company_name": entry.get("company_name") or entry.get("name") or "",
            "industry": entry.get("industry") or "",
            "careers_url": entry.get("careers_url") or entry.get("url") or "",
            "extractor": entry.get("extractor") or "",
            "sitemap_url": entry.get("sitemap_url") or config.get("sitemap_url") or "",
        })
    return specs


def _load_specs_csv(path: str) -> List[Dict[str, Any]]:
    """Read ``industry,company_name`` rows (header row required) into specs."""
    import csv

    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows:
        return []
    header = [cell.strip().lower() for cell in rows[0]]
    index = {name: i for i, name in enumerate(header)}
    specs: List[Dict[str, Any]] = []
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        specs.append({
            "company_name": _cell(row, index, "company_name", "company", "name"),
            "industry": _cell(row, index, "industry", "sector", "vertical"),
            "careers_url": _cell(row, index, "careers_url", "url"),
        })
    return specs


def _cell(row: List[str], index: Dict[str, int], *names: str) -> str:
    for name in names:
        if name in index and index[name] < len(row):
            return row[index[name]].strip()
    return ""


def resolve_specs(
    specs: List[Dict[str, Any]],
    client: Any = None,
    discover: bool = False,
    registry_path: Optional[str] = None,
):
    """Resolve name-only specs to full specs.

    Returns ``(resolved, unresolved_names)``. Specs that already carry a
    ``careers_url`` pass through untouched; specs with only a ``company_name``
    are resolved through the directory/registry - and, with ``discover=True``
    and a ``client``, straight off the employer's own website. Duplicate URLs are
    collapsed. Names that resolve to nothing are returned separately so the
    caller can warn instead of silently crawling nothing.
    """
    resolved: List[Dict[str, Any]] = []
    unresolved: List[str] = []
    seen: set = set()
    for spec in specs or []:
        if not spec.get("careers_url"):
            name = spec.get("company_name", "")
            match = resolve(name, registry_path, client=client, discover=discover)
            if not match:
                unresolved.append(name)
                continue
            spec = {**match, "industry": spec.get("industry") or match.get("industry", "")}
        url = (spec.get("careers_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        spec["careers_url"] = url
        resolved.append(spec)
    return resolved, unresolved


# -- automatic discovery ---------------------------------------------------
def discover_careers_url(
    name: str,
    fetch_page: Callable[[str], str],
    max_candidates: int = 8,
) -> Optional[Dict[str, Any]]:
    """Find a company's first-party careers URL from its name alone.

    Derives plausible ``<name>.com``/``<name>.in`` hosts, reads the company's own
    homepage and returns the strongest *careers/jobs* link found there (falling
    back to common ``/careers`` paths). Every candidate is **verified**: the page
    must load and look like a careers/jobs page, and the URL must be on the
    employer's own domain - never a job-board aggregator. Unverified candidates
    are discarded, so nothing is ever stored without evidence.
    """
    tokens = _tokens(name)
    if not tokens:
        return None
    for host in _valid_hosts(_candidate_hosts(tokens, max_candidates)):
        root = f"https://{host}/"
        html = _safe_fetch(fetch_page, root)
        link = _best_careers_link(html, root, host)
        if link and _looks_like_careers(_safe_fetch(fetch_page, link)):
            return {"careers_url": link, "host": host, "source": "homepage-link"}
        for path in _CAREER_PATHS:
            url = urljoin(root, path)
            if _looks_like_careers(_safe_fetch(fetch_page, url)):
                return {"careers_url": url, "host": host, "source": "career-path"}
    return None


def _valid_hosts(candidates: List[str]) -> List[str]:
    """Keep only plausible, non-aggregator hostnames."""
    hosts: List[str] = []
    for host in candidates:
        clean = str(host).strip().lower().replace("https://", "").replace("http://", "").split("/")[0]
        if not clean or "." not in clean or clean in hosts:
            continue
        if _AGGREGATOR_RE.search(clean):  # never probe a job board
            continue
        hosts.append(clean)
    return hosts


def _safe_fetch(fetch_page: Callable[[str], str], url: str) -> str:
    try:
        return fetch_page(url) or ""
    except Exception:  # noqa: BLE001 - a bad candidate must never abort discovery
        return ""


def _looks_like_careers(html: str) -> bool:
    return bool(html) and len(html) > 500 and bool(_CAREER_WORD_RE.search(html))


def _resolve_by_discovery(name: str, client: Any) -> Optional[Dict[str, Any]]:
    found = discover_careers_url(name, client.get_page)
    if not found:
        return None
    url = found["careers_url"]
    platform = detect_platform(url)
    if platform == "unknown":
        try:
            platform = detect_platform(url, client.get_page(url))
        except Exception:  # noqa: BLE001 - detection is best-effort
            platform = "unknown"
    return {
        "company_name": name.strip().title(),
        "industry": "",
        "careers_url": url,
        "platform": platform,
        "discovered": True,
        "discovery_source": found["source"],
    }


def _tokens(name: str) -> List[str]:
    text = _PUNCT_RE.sub(" ", (name or "").lower())
    tokens = [t for t in text.split() if t and t not in _SUFFIXES]
    return tokens or [t for t in text.split() if t]


def _candidate_hosts(tokens: List[str], limit: int) -> List[str]:
    """Plausible domains for a brand, most specific first."""
    stems: List[str] = []
    compact = "".join(tokens)
    if compact:
        stems.append(compact)
    if tokens:
        stems.append(tokens[0])
        if len(tokens) > 1:
            stems.append(tokens[-1])
        stems.append("".join(t[0] for t in tokens if t))
    hosts: List[str] = []
    for stem in stems:
        for suffix in _DOMAIN_SUFFIXES:
            host = f"{stem}.{suffix}"
            if host not in hosts:
                hosts.append(host)
            if len(hosts) >= limit:
                return hosts
    return hosts


def _best_careers_link(html: str, base: str, host: str) -> Optional[str]:
    """Return the strongest same-site careers link in ``html`` (or ``None``)."""
    if not html:
        return None
    best: Optional[str] = None
    best_score = 0
    for match in _ANCHOR_RE.finditer(html):
        href = match.group(1).strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        text = _TAG_RE.sub(" ", match.group(2))
        target = urljoin(base, href)
        parts = urlsplit(target)
        if parts.scheme not in ("http", "https") or not _same_site(parts.netloc, host):
            continue
        haystack = f"{parts.path} {text}"
        if not _CAREER_WORD_RE.search(haystack):
            continue
        score = 0
        if _CAREER_WORD_RE.search(parts.path):
            score += 2
        if "career" in haystack.lower():
            score += 1
        if parts.path.rstrip("/").lower() in ("/careers", "/jobs"):
            score += 1
        if score > best_score:
            best_score, best = score, target
    return best


def _same_site(a: str, b: str) -> bool:
    """True when two hosts share the same registrable domain."""
    a_labels = [p for p in (a or "").lower().split(".") if p]
    b_labels = [p for p in (b or "").lower().split(".") if p]
    if not a_labels or not b_labels:
        return False
    return a_labels[-2:] == b_labels[-2:]


def _spec(entry: Dict[str, Any]) -> Dict[str, Any]:
    spec = {
        "company_name": entry.get("company_name", ""),
        "industry": entry.get("industry", ""),
        "careers_url": entry["careers_url"],
    }
    if entry.get("extractor"):
        spec["extractor"] = entry["extractor"]
    return spec


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    # Token containment ("wipro" in "wipro technologies") scores highly.
    if a in b or b in a:
        return 0.95
    return SequenceMatcher(None, a, b).ratio()
