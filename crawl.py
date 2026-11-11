#!/usr/bin/env python
"""Universal crawler: any careers URL (or a company name) in, one dataset out.

The script auto-detects which recruitment platform sits behind each URL, routes
to the matching typed adapter (Workday / Amazon / SmartRecruiters / Greenhouse /
Lever / TCS / Wipro / Accenture) or to the generic sitemap + schema.org crawler,
then writes the dataset using exactly the same schema, normalization, dedupe and
validation as the main pipeline. Core is standard-library Python (3.9+);
rendering JS/SPA sites is an optional Playwright add-on.

Examples
--------
    py crawl.py https://jobs.lever.co/meesho
    py crawl.py https://careers.sunpharma.com --name "Sun Pharma"

    # just a company NAME - resolved to its official careers site automatically
    py crawl.py --company "Wipro" --company "Accenture" --render
    py crawl.py --companies config\\target_companies.csv --render --discover

    # see every employer the resolver already knows
    py crawl.py --list-companies

    # many companies in one consolidated dataset
    py crawl.py https://jobs.lever.co/meesho https://boards.greenhouse.io/groww ...
    py crawl.py --companies my_companies.json --out .\\out

    # render JavaScript/SPA sites in a headless browser (optional Playwright)
    py crawl.py https://ibegin.tcsapps.com/candidate/next/en-IN/landing --render
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src"))

from jobintel.dedupe import deduplicate  # noqa: E402
from jobintel.detect import detect_platform, guess_company_name  # noqa: E402
from jobintel.extractors import build_for_url  # noqa: E402
from jobintel.extractors.base import ExtractionResult  # noqa: E402
from jobintel.http_client import HttpClient, HttpError  # noqa: E402
from jobintel.normalize import slugify  # noqa: E402
from jobintel.pipeline import _today_ist  # noqa: E402
from jobintel.reporting import (  # noqa: E402
    build_summary_text,
    write_coverage_csv,
    write_jobs_html,
    write_master_csv,
)
from jobintel.resolve import list_companies, load_specs, resolve_specs  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("urls", nargs="*", metavar="URL",
                        help="one or more employer careers URLs")
    parser.add_argument("--company", action="append", default=[], metavar="NAME",
                        help="company NAME to resolve to its official careers site "
                             "(repeatable; use with --discover for unknown names)")
    parser.add_argument("--companies", default="",
                        help="JSON or CSV file with employer specs "
                             "(CSV columns: industry,company_name)")
    parser.add_argument("--list-companies", action="store_true",
                        help="print every employer the resolver already knows, then exit")
    parser.add_argument("--discover", action="store_true",
                        help="auto-discover the careers URL for names the resolver does not know")
    parser.add_argument("--name", default="",
                        help="employer name (single URL only; default: derived from the URL)")
    parser.add_argument("--industry", default="",
                        help="industry label (default: 'Unspecified')")
    parser.add_argument("--sitemap", default="", help="override the sitemap.xml entry point")
    parser.add_argument("--out", default=_HERE, help="output directory for the dataset")
    parser.add_argument("--dataset-name", default="universal",
                        help="output file stem when crawling more than one company")
    parser.add_argument("--cache", default=os.path.join(_HERE, ".cache", "http"),
                        help="directory for the on-disk HTTP response cache")
    parser.add_argument("--limit", type=int, default=None, help="cap records per company (debug)")
    parser.add_argument("--workers", type=int, default=4, help="concurrent page fetches")
    parser.add_argument("--rps", type=float, default=6.0, help="max HTTP requests per second")
    parser.add_argument("--no-cache", action="store_true", help="ignore the on-disk HTTP cache")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout (seconds)")
    parser.add_argument("--retries", type=int, default=3, help="max retries per request")
    parser.add_argument("--render", action="store_true",
                        help="render pages in a headless browser (Playwright) for JS/SPA sites")
    return parser.parse_args(argv)


def build_specs(args: argparse.Namespace, client: HttpClient) -> List[Dict[str, str]]:
    """Turn URLs, --company names and --companies files into crawl specs.

    Names that are not already known are resolved through the careers directory
    (and, with ``--discover``, straight off the employer's own website).
    """
    specs: List[Dict[str, str]] = []
    if args.companies:
        specs.extend(load_specs(args.companies))
    specs.extend({"careers_url": url} for url in args.urls)
    specs.extend({"company_name": name} for name in args.company)

    resolved, unresolved = resolve_specs(specs, client=client, discover=args.discover)
    for name in unresolved:
        print(f"warning: could not resolve company '{name}'; pass its careers URL "
              f"or add it to config/careers_directory.json", file=sys.stderr)

    # --name / --industry / --sitemap describe a single employer unambiguously.
    if len(resolved) == 1:
        spec = resolved[0]
        if args.name and not spec.get("company_name"):
            spec["company_name"] = args.name
        if args.industry and not spec.get("industry"):
            spec["industry"] = args.industry
        if args.sitemap and not spec.get("sitemap_url"):
            spec["sitemap_url"] = args.sitemap
    return resolved


def build_client(args: argparse.Namespace) -> HttpClient:
    """Create the HTTP client, optionally rendering pages in a headless browser."""
    common = dict(
        cache_dir=args.cache,
        requests_per_second=args.rps,
        timeout=args.timeout,
        max_retries=args.retries,
        use_cache=not args.no_cache,
    )
    if args.render:
        from jobintel.render import RenderingClient, available

        if available():
            print("renderer: enabled (headless Chromium via Playwright)")
            return RenderingClient(**common)
        print("renderer: requested but Playwright is unavailable "
              "('pip install playwright' + 'python -m playwright install chromium'); "
              "falling back to standard HTTP", file=sys.stderr)
    return HttpClient(**common)


def resolve_platform(client: HttpClient, url: str) -> str:
    """Fingerprint the URL; fall back to the landing-page HTML when inconclusive."""
    platform = detect_platform(url)
    if platform == "unknown":
        try:
            platform = detect_platform(url, client.get_page(url))
        except HttpError:
            pass
    return platform


def crawl_one(client: HttpClient, spec: Dict[str, str], extraction_date: str,
              args: argparse.Namespace) -> ExtractionResult:
    url = spec["careers_url"]
    name = spec.get("company_name") or guess_company_name(url)
    industry = spec.get("industry") or "Unspecified"
    platform = resolve_platform(client, url)
    print(f"[{name}] {url}\n    platform: {platform}")
    try:
        extractor = build_for_url(
            url,
            company_name=name,
            industry=industry,
            sitemap_url=spec.get("sitemap_url") or None,
            client=client,
            extraction_date=extraction_date,
            workers=args.workers,
            max_records=args.limit,
        )
        result = extractor.extract()
    except Exception as exc:  # noqa: BLE001 - one bad employer must not stop the run
        result = ExtractionResult(
            company_name=name, industry=industry, platform=platform,
            careers_url=url, status="FAILED",
            errors=[f"{type(exc).__name__}: {exc}"],
        )
    print(f"    status: {result.status}  portal: {result.portal_total}  "
          f"extracted: {result.extracted}  coverage: {result.coverage_pct}")
    return result


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list_companies:
        for name in list_companies():
            print(name)
        return 0

    extraction_date = _today_ist()
    client = build_client(args)

    specs = build_specs(args, client)
    if not specs:
        print("error: no employers given (pass URLs, --company NAME, or --companies FILE)",
              file=sys.stderr)
        return 2

    try:
        results = [crawl_one(client, spec, extraction_date, args) for spec in specs]
        all_postings = [posting for result in results for posting in result.postings]
        unique, dedupe_stats = deduplicate(all_postings)
        unique.sort(key=lambda p: (p.company_name.lower(), p.job_title.lower()))
        http_stats = client.stats
    finally:
        client.close()

    stem = (slugify(results[0].company_name) or "company") if len(results) == 1 else args.dataset_name
    os.makedirs(args.out, exist_ok=True)
    master_path = os.path.join(args.out, f"{stem}_jobs.csv")
    coverage_path = os.path.join(args.out, f"{stem}_coverage.csv")
    details_path = os.path.join(args.out, f"{stem}_job_details.html")
    write_master_csv(unique, master_path)
    write_jobs_html(unique, details_path)
    write_coverage_csv(results, coverage_path)

    print("\n" + build_summary_text(results, dedupe_stats, http_stats, extraction_date))
    print("\nOutputs written to:")
    print(f"  - dataset : {master_path}")
    print(f"  - details : {details_path}")
    print(f"  - coverage: {coverage_path}")

    failed = [r for r in results if r.status == "FAILED"]
    if failed and len(failed) == len(results):
        for result in failed:
            for error in result.errors[:3]:
                print(f"  ! {result.company_name}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
