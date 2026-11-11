#!/usr/bin/env python
"""Command-line entry point for the India job-intelligence pipeline.

Examples
--------
    py run.py                                  # full run, all configured employers
    py run.py --companies NVIDIA,Pfizer        # subset of employers
    py run.py --limit 25 --no-cache            # quick smoke test
    py run.py --rps 3 --workers 3              # gentler on the portals

Adding an employer needs no code - pass a name or a small CSV:
    py run.py --company Wipro --company Accenture
    py run.py --companies-file config\\target_companies.csv
    py run.py --company "Some New Co" --discover

The script is pure standard-library Python (3.9+); no third-party packages are
required, which keeps the solution reproducible on any machine.
"""

from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "src"))

from jobintel.http_client import HttpClient  # noqa: E402
from jobintel.pipeline import PipelineConfig, run_pipeline  # noqa: E402
from jobintel.resolve import load_specs, resolve_specs  # noqa: E402


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--registry", default=os.path.join(_HERE, "config", "companies.json"),
                        help="path to the company registry JSON")
    parser.add_argument("--out", default=_HERE, help="output directory for the deliverables")
    parser.add_argument("--cache", default=os.path.join(_HERE, ".cache", "http"),
                        help="directory for the on-disk HTTP response cache")
    parser.add_argument("--companies", default="", help="comma-separated subset of company names")
    parser.add_argument("--company", action="append", default=[], metavar="NAME",
                        help="employer NAME to resolve to its official careers site "
                             "(repeatable; crawls exactly the names/files you give)")
    parser.add_argument("--companies-file", default="",
                        help="JSON or CSV file of employers to crawl "
                             "(CSV columns: industry,company_name)")
    parser.add_argument("--discover", action="store_true",
                        help="auto-discover the careers URL for names not already in the directory")
    parser.add_argument("--limit", type=int, default=None, help="cap records per employer (debug)")
    parser.add_argument("--workers", type=int, default=4, help="concurrent detail fetches per employer")
    parser.add_argument("--rps", type=float, default=6.0, help="max HTTP requests per second")
    parser.add_argument("--no-cache", action="store_true", help="ignore the on-disk HTTP cache")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-request timeout (seconds)")
    parser.add_argument("--retries", type=int, default=3, help="max retries per request")
    parser.add_argument("--render", action="store_true",
                        help="render pages in a headless browser (Playwright) for JS/SPA sites")
    parser.add_argument("--payroll-only", action="store_true",
                        help="keep only employers that publish salary; companies with "
                             "no pay data are dropped from the dataset and report")
    parser.add_argument("--api", action="store_true",
                        help="serve the lightweight query API + dashboard over the "
                             "existing india_active_jobs_master.csv (no crawling)")
    parser.add_argument("--host", default="127.0.0.1", help="API bind host (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="API bind port (default 8000)")
    return parser.parse_args(argv)


def _resolve_employers(args) -> list:
    """Resolve --company names and --companies-file rows into employer specs.

    Names known to the careers directory (or the registry) resolve offline; with
    ``--discover`` an unknown name is looked up on the employer's own website.
    """
    specs = []
    if args.companies_file:
        specs.extend(load_specs(args.companies_file))
    specs.extend({"company_name": name} for name in args.company)
    if not specs:
        return []

    client = None
    if args.discover:
        client = HttpClient(cache_dir=args.cache, requests_per_second=args.rps,
                            timeout=args.timeout, max_retries=args.retries)
    try:
        resolved, unresolved = resolve_specs(specs, client=client, discover=args.discover)
    finally:
        if client is not None:
            client.close()
    for name in unresolved:
        print(f"warning: could not resolve company '{name}'; add it to "
              f"config/careers_directory.json or pass --companies-file with a careers_url",
              file=sys.stderr)
    return resolved


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.api:
        from jobintel.api import serve

        master = os.path.join(args.out, "india_active_jobs_master.csv")
        serve(master, host=args.host, port=args.port)
        return 0

    company_specs = _resolve_employers(args)
    if (args.company or args.companies_file) and not company_specs:
        print("error: none of the given companies could be resolved", file=sys.stderr)
        return 2
    config = PipelineConfig(
        registry_path=args.registry,
        output_dir=args.out,
        cache_dir=args.cache,
        workers=args.workers,
        requests_per_second=args.rps,
        use_cache=not args.no_cache,
        max_records=args.limit,
        companies=[c.strip() for c in args.companies.split(",") if c.strip()] or None,
        company_specs=company_specs or None,
        timeout=args.timeout,
        max_retries=args.retries,
        render=args.render,
        payroll_only=args.payroll_only,
    )
    result = run_pipeline(config)
    print("\nDeliverables written to:")
    for label, path in result.output_paths.items():
        print(f"  - {label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
