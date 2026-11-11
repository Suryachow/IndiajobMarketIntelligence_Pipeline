"""Output writers: master CSV, coverage report (CSV + Markdown) and run log."""

from __future__ import annotations

import csv
import html
import json
import os
from typing import Any, Dict, Iterable, List

from .schema import CSV_FIELDS, JobPosting


def write_master_csv(postings: Iterable[JobPosting], path: str) -> None:
    """Write the consolidated dataset with a stable, employer-agnostic schema.

    ``utf-8-sig`` keeps the file readable in Excel while remaining valid UTF-8.
    """
    # utf-8-sig so the ₹ / accented characters render correctly in Excel.
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for posting in postings:
            writer.writerow(posting.to_row())


def write_jobs_html(postings: Iterable[JobPosting], path: str) -> None:
    """Write a self-contained local job browser with salary detail links."""
    jobs = list(postings)

    def esc(value: Any) -> str:
        return html.escape(str(value or ""), quote=True)

    def salary(job: JobPosting) -> str:
        if not (job.min_salary or job.max_salary):
            return "Not published"
        low = job.min_salary or job.max_salary
        high = job.max_salary or job.min_salary
        amount = low if low == high else f"{low} - {high}"
        return f"{amount} {job.currency}".strip()

    rows = []
    details = []
    for index, job in enumerate(jobs, 1):
        anchor = f"job-{index}"
        rows.append(
            f'<tr><td><a href="#{anchor}">{esc(job.job_title)}</a></td>'
            f"<td>{esc(job.company_name)}</td><td>{esc(job.job_location)}</td>"
            f"<td>{esc(salary(job))}</td></tr>"
        )
        details.append(
            f'<article id="{anchor}"><h2>{esc(job.job_title)}</h2>'
            f"<p><strong>Company:</strong> {esc(job.company_name)}<br>"
            f"<strong>Location:</strong> {esc(job.job_location)}<br>"
            f"<strong>Salary:</strong> {esc(salary(job))}<br>"
            f"<strong>Employment:</strong> {esc(job.employment_type or 'Not published')}</p>"
            f'<p>{esc(job.job_description)}</p>'
            f'<p><a href="{esc(job.job_url)}" target="_blank" rel="noopener">'
            "Open original employer posting</a> | "
            f'<a href="#top">Back to jobs</a></p></article>'
        )

    document = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>India Active Jobs</title>
<style>body{font:16px system-ui,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem}
table{border-collapse:collapse;width:100%}th,td{border:1px solid #ddd;padding:.6rem;text-align:left}
th{background:#f3f4f6}article{border-top:2px solid #ddd;margin-top:2rem;padding-top:1rem}
a{color:#075985}</style></head><body id="top">
<h1>India Active Jobs</h1>
<p>Click a job title to view its extracted salary and description.</p>
<table><thead><tr><th>Job</th><th>Company</th><th>Location</th><th>Salary</th></tr></thead>
<tbody>""" + "".join(rows) + "</tbody></table>" + "".join(details) + "</body></html>"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(document)


def write_coverage_csv(results: List[Any], path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "Company",
                "Industry",
                "Platform",
                "Careers Portal",
                "Portal Job Count (India scope)",
                "Jobs Extracted",
                "Coverage %",
                "Status",
                "Notes",
            ]
        )
        total_portal = 0
        total_extracted = 0
        for r in results:
            total_portal += r.portal_total or 0
            total_extracted += r.extracted
            writer.writerow(
                [
                    r.company_name,
                    r.industry,
                    r.platform,
                    r.careers_url,
                    r.portal_total if r.portal_total is not None else "unknown",
                    r.extracted,
                    r.coverage_pct,
                    r.status,
                    " | ".join(r.notes + r.errors)[:500],
                ]
            )
        overall = (
            f"{100.0 * total_extracted / total_portal:.1f}%" if total_portal else "unknown"
        )
        writer.writerow(["TOTAL", "", "", "", total_portal, total_extracted, overall, "", ""])


def build_summary_text(
    results: List[Any],
    dedupe_stats: Dict[str, int],
    http_stats: Dict[str, int],
    extraction_date: str,
) -> str:
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append(f"India Active Jobs - extraction summary ({extraction_date})")
    lines.append("=" * 78)
    lines.append(
        f"{'Company':22}{'Platform':16}{'Portal':>8}{'Extracted':>11}{'Coverage':>10}  Status"
    )
    total_portal = total_extracted = 0
    for r in results:
        total_portal += r.portal_total or 0
        total_extracted += r.extracted
        lines.append(
            f"{r.company_name[:21]:22}{r.platform[:15]:16}"
            f"{r.portal_total if r.portal_total is not None else 0:>8}"
            f"{r.extracted:>11}{r.coverage_pct:>10}  {r.status}"
        )
    overall = f"{100.0 * total_extracted / total_portal:.1f}%" if total_portal else "unknown"
    lines.append("-" * 78)
    lines.append(f"{'TOTAL':38}{total_portal:>8}{total_extracted:>11}{overall:>10}")
    lines.append("-" * 78)
    lines.append(
        f"duplicates removed={dedupe_stats.get('removed_total', 0)} "
        f"(exact={dedupe_stats.get('removed_exact_hash', 0)}, "
        f"business-key={dedupe_stats.get('removed_business_key', 0)}, "
        f"fuzzy={dedupe_stats.get('removed_fuzzy', 0)})"
    )
    lines.append(f"http requests={http_stats.get('requests', 0)} cache_hits={http_stats.get('cache_hits', 0)} errors={http_stats.get('errors', 0)}")
    return "\n".join(lines)


def build_coverage_markdown(
    results: List[Any],
    dedupe_stats: Dict[str, int],
    http_stats: Dict[str, int],
    extraction_date: str,
) -> str:
    lines: List[str] = [
        "# Company Coverage Summary",
        "",
        f"_Extraction date: {extraction_date}_",
        "",
        "`Portal Job Count` is the number of active India-scoped postings the employer's own "
        "career portal advertises (the denominator). `Jobs Extracted` is the number successfully "
        "retrieved and validated. Where the portal total cannot be established it is reported as "
        "`unknown` rather than estimated.",
        "",
        "| Company | Industry | Platform | Portal Job Count (India) | Jobs Extracted | Coverage % | Status |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    total_portal = total_extracted = 0
    for r in results:
        total_portal += r.portal_total or 0
        total_extracted += r.extracted
        lines.append(
            f"| {r.company_name} | {r.industry} | {r.platform} | "
            f"{r.portal_total if r.portal_total is not None else 'unknown'} | "
            f"{r.extracted} | {r.coverage_pct} | {r.status} |"
        )
    overall = f"{100.0 * total_extracted / total_portal:.1f}%" if total_portal else "unknown"
    lines.append(
        f"| **Total** | | | **{total_portal}** | **{total_extracted}** | **{overall}** | |"
    )
    lines += ["", "## Per-company notes", ""]
    for r in results:
        lines.append(f"### {r.company_name} ({r.platform})")
        lines.append(f"- Careers portal: {r.careers_url}")
        lines.append(f"- Portal total scope: {r.portal_total_scope or 'n/a'}")
        for note in r.notes:
            lines.append(f"- {note}")
        for err in r.errors[:10]:
            lines.append(f"- ERROR: {err}")
        if len(r.errors) > 10:
            lines.append(f"- ... and {len(r.errors) - 10} more error(s)")
        lines.append("")
    lines += [
        "## Run statistics",
        "",
        f"- Duplicate records removed: {dedupe_stats.get('removed_total', 0)} "
        f"(exact-hash={dedupe_stats.get('removed_exact_hash', 0)}, "
        f"business-key={dedupe_stats.get('removed_business_key', 0)}, "
        f"fuzzy={dedupe_stats.get('removed_fuzzy', 0)})",
        f"- HTTP requests issued: {http_stats.get('requests', 0)}",
        f"- Cache hits: {http_stats.get('cache_hits', 0)}",
        f"- Requests that exhausted retries: {http_stats.get('errors', 0)}",
        "",
    ]
    return "\n".join(lines)


def write_run_log(result: Any, path: str) -> None:
    payload: Dict[str, Any] = {
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "duration_seconds": result.duration_seconds,
        "total_records": len(result.postings),
        "dedupe_stats": result.dedupe_stats,
        "http_stats": result.http_stats,
        "companies": [
            {
                "company_name": r.company_name,
                "industry": r.industry,
                "platform": r.platform,
                "careers_url": r.careers_url,
                "portal_total": r.portal_total,
                "portal_total_scope": r.portal_total_scope,
                "extracted": r.extracted,
                "coverage_pct": r.coverage_pct,
                "status": r.status,
                "notes": r.notes,
                "errors": r.errors,
            }
            for r in result.results
        ],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
