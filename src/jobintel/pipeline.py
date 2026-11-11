"""Pipeline orchestration: registry -> extraction -> normalization -> dedupe ->
consolidated dataset + coverage report."""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .classify import classify_posting
from .dedupe import deduplicate
from .extractors import REGISTRY, ExtractionResult, build_extractor, build_for_url
from .history import (
    build_change_markdown,
    diff_snapshots,
    latest_snapshot_before,
    save_change_report,
    save_snapshot,
    snapshot_records,
)
from .http_client import HttpClient
from .normalize import is_india_location
from .reporting import (
    build_coverage_markdown,
    build_summary_text,
    write_coverage_csv,
    write_jobs_html,
    write_master_csv,
    write_run_log,
)
from .schema import JobPosting
from .trends import build_trends_markdown, compute_trends, write_trends_json


@dataclass
class PipelineConfig:
    registry_path: str = "config/companies.json"
    output_dir: str = "."
    cache_dir: str = ".cache/http"
    workers: int = 4
    requests_per_second: float = 6.0
    use_cache: bool = True
    max_records: Optional[int] = None
    companies: Optional[List[str]] = None
    # Explicit employer specs (e.g. resolved from bare company names or a CSV).
    # When given they are crawled *instead of* the registry.
    company_specs: Optional[List[Dict[str, Any]]] = None
    timeout: float = 30.0
    max_retries: int = 3
    extraction_date: Optional[str] = None
    render: bool = False
    # Keep only employers that actually publish pay (>= 1 posting with salary).
    payroll_only: bool = False
    # Daily-refresh history store; defaults to ``<output_dir>/history``.
    history_dir: Optional[str] = None
    # Write the snapshot/change report and the hiring-trend report.
    write_history: bool = True
    write_trends: bool = True


@dataclass
class PipelineResult:
    postings: List[JobPosting] = field(default_factory=list)
    results: List[ExtractionResult] = field(default_factory=list)
    dedupe_stats: Dict[str, int] = field(default_factory=dict)
    http_stats: Dict[str, int] = field(default_factory=dict)
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    output_paths: Dict[str, str] = field(default_factory=dict)


def load_registry(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _india_only(postings: List[JobPosting]) -> tuple:
    """Split postings into ``(india_postings, dropped_count)``."""
    kept: List[JobPosting] = []
    dropped = 0
    for posting in postings:
        country = str(posting.country or "").strip().lower()
        if is_india_location(posting.job_location) or country in {"india", "in"}:
            kept.append(posting)
        else:
            dropped += 1
    return kept, dropped


def _write_history(
    postings: List[JobPosting], history_dir: str, extraction_date: str, log=print
) -> Dict[str, str]:
    """Snapshot the dataset and diff it against the previous snapshot.

    Returns the written paths so the caller can surface them as deliverables.
    """
    previous = latest_snapshot_before(history_dir, extraction_date)
    snapshot_path = save_snapshot(postings, history_dir, extraction_date)

    changes = diff_snapshots(
        previous[1] if previous else None,
        {"date": extraction_date, "postings": snapshot_records(postings)},
        compared_to=_snapshot_date(previous[0]) if previous else "",
    )
    change_json = save_change_report(changes, history_dir, extraction_date)
    change_md = os.path.join(os.path.dirname(change_json), f"changes_{extraction_date}.md")
    with open(change_md, "w", encoding="utf-8") as fh:
        fh.write(build_change_markdown(changes, extraction_date))

    counts = changes["counts"]
    if changes.get("baseline"):
        log(f"history: baseline snapshot written ({counts['unchanged']} postings)")
    else:
        log(
            f"history: vs {changes['compared_to']} -> new={counts['new']} "
            f"modified={counts['modified']} closed={counts['closed']}"
        )
    return {
        "history_snapshot": snapshot_path,
        "change_report_json": change_json,
        "change_report_markdown": change_md,
    }


def _snapshot_date(path: str) -> str:
    base = os.path.basename(path)
    return base[len("snapshot_"):-len(".json")]


def _make_client(config: PipelineConfig, log=print):
    """Build the HTTP client, optionally rendering pages in a headless browser."""
    common = dict(
        cache_dir=config.cache_dir,
        requests_per_second=config.requests_per_second,
        timeout=config.timeout,
        max_retries=config.max_retries,
        use_cache=config.use_cache,
    )
    if config.render:
        from .render import RenderingClient, available

        if available():
            log("renderer: enabled (headless Chromium via Playwright)")
            return RenderingClient(**common)
        log("renderer: requested but Playwright is unavailable; using standard HTTP")
    return HttpClient(**common)


def run_pipeline(config: PipelineConfig, log=print) -> PipelineResult:
    started = time.time()
    result = PipelineResult(started_at=_iso())
    extraction_date = config.extraction_date or _today_ist()

    registry = load_registry(config.registry_path)
    if config.company_specs is not None:
        companies = list(config.company_specs)
    else:
        companies = list(registry.get("companies", []))
        if config.companies:
            wanted = {c.lower() for c in config.companies}
            companies = [c for c in companies if c["company_name"].lower() in wanted]

    client = _make_client(config, log)

    all_postings: List[JobPosting] = []
    try:
        for company in companies:
            name = company["company_name"]
            log(f"[{name}] extracting via '{company.get('extractor')}' ...")
            try:
                extractor = _build_company_extractor(
                    company,
                    client=client,
                    extraction_date=extraction_date,
                    workers=config.workers,
                    max_records=config.max_records,
                )
                company_result = extractor.extract()
            except Exception as exc:  # noqa: BLE001 - one employer must not kill the run
                company_result = ExtractionResult(
                    company_name=name,
                    industry=company.get("industry", ""),
                    platform=company.get("extractor", ""),
                    careers_url=company.get("careers_url", ""),
                    status="FAILED",
                    errors=[f"unhandled extractor error: {type(exc).__name__}: {exc}"],
                )
            result.results.append(company_result)
            all_postings.extend(company_result.postings)
            log(
                f"[{name}] status={company_result.status} extracted={company_result.extracted} "
                f"portal_total={company_result.portal_total} coverage={company_result.coverage_pct}"
            )
    finally:
        client.close()

    # Final safety net: the dataset must contain India postings only, whatever
    # the source returned (some multi-country Workday postings list a non-India
    # primary location).
    all_postings, dropped_non_india = _india_only(all_postings)
    if dropped_non_india:
        log(f"India-scope filter: dropped {dropped_non_india} non-India posting(s)")

    unique, dedupe_stats = deduplicate(all_postings)

    if config.payroll_only:
        # Drop whole employers that publish no pay at all, so the consolidated
        # dataset is made up only of salary-bearing companies.
        payroll = {p.company_name for p in unique if p.min_salary or p.max_salary}
        unique = [p for p in unique if p.company_name in payroll]
        result.results = [r for r in result.results if r.company_name in payroll]

    unique.sort(key=lambda p: (p.company_name.lower(), p.job_title.lower()))

    # Derived analytics: standardized job family + seniority for every posting
    # (deterministic, from the posting's own title/category/description).
    for posting in unique:
        classify_posting(posting)

    result.postings = unique
    result.dedupe_stats = dedupe_stats
    result.http_stats = client.stats

    os.makedirs(config.output_dir, exist_ok=True)
    master_path = os.path.join(config.output_dir, "india_active_jobs_master.csv")
    coverage_csv = os.path.join(config.output_dir, "coverage_report.csv")
    coverage_md = os.path.join(config.output_dir, "coverage_report.md")
    run_log_path = os.path.join(config.output_dir, "run_log.json")
    jobs_html_path = os.path.join(config.output_dir, "job_details.html")

    write_master_csv(unique, master_path)
    write_jobs_html(unique, jobs_html_path)
    write_coverage_csv(result.results, coverage_csv)
    with open(coverage_md, "w", encoding="utf-8") as fh:
        fh.write(build_coverage_markdown(result.results, dedupe_stats, result.http_stats, extraction_date))

    result.finished_at = _iso()
    result.duration_seconds = round(time.time() - started, 2)
    result.output_paths = {
        "master_csv": master_path,
        "job_details_html": jobs_html_path,
        "coverage_csv": coverage_csv,
        "coverage_markdown": coverage_md,
        "run_log": run_log_path,
    }

    if config.write_history:
        history_dir = config.history_dir or os.path.join(config.output_dir, "history")
        result.output_paths.update(
            _write_history(unique, history_dir, extraction_date, log)
        )

    if config.write_trends:
        trends = compute_trends(unique)
        trends_json = os.path.join(config.output_dir, "hiring_trends.json")
        trends_md = os.path.join(config.output_dir, "hiring_trends.md")
        write_trends_json(trends, trends_json)
        with open(trends_md, "w", encoding="utf-8") as fh:
            fh.write(build_trends_markdown(trends, extraction_date))
        result.output_paths["trends_json"] = trends_json
        result.output_paths["trends_markdown"] = trends_md

    write_run_log(result, run_log_path)

    summary = build_summary_text(result.results, dedupe_stats, result.http_stats, extraction_date)
    log("\n" + summary)
    return result


def _build_company_extractor(
    company: Dict[str, Any],
    client: HttpClient,
    extraction_date: str,
    workers: int,
    max_records: Optional[int],
):
    """Build the right adapter for a registry entry *or* a resolved name spec.

    A spec that names a known typed adapter (``extractor``) uses it; anything
    else is routed by platform detection (a typed adapter when recognised, else
    the generic crawler) so a bare company name just works.
    """
    company = _defaulted(company)
    common = dict(
        client=client,
        extraction_date=extraction_date,
        workers=workers,
        max_records=max_records,
    )
    if company.get("extractor") in REGISTRY:
        return build_extractor(company, **common)
    return build_for_url(
        company["careers_url"],
        company_name=company.get("company_name"),
        industry=company.get("industry", ""),
        sitemap_url=company.get("sitemap_url"),
        **common,
    )


def _defaulted(company: Dict[str, Any]) -> Dict[str, Any]:
    """Fill the fields every adapter relies on, without mutating the source."""
    company = dict(company)
    if not company.get("industry"):
        company["industry"] = "Unspecified"
    return company


def _today_ist() -> str:
    try:
        from zoneinfo import ZoneInfo  # type: ignore

        return _dt.datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    except Exception:  # noqa: BLE001 - fall back to local date
        return _dt.date.today().isoformat()


def _iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()
