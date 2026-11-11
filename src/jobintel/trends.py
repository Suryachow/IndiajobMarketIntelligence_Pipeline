"""Hiring-trend analytics over the consolidated dataset.

Deterministic aggregations (no database, no external service) that answer the
optional "hiring trends by company, industry or location" capability: the
consolidated postings are grouped by company, industry, state, city, job family,
seniority, work arrangement, employment type and source platform, and the
published salary range is summarised.

Everything is derived from the master dataset alone, so the numbers always
reconcile with ``india_active_jobs_master.csv``.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from collections import Counter
from typing import Any, Dict, Iterable, List

from .schema import has_salary

# (json key, JobPosting attribute, human label)
BREAKDOWNS = (
    ("by_company", "company_name", "Company"),
    ("by_industry", "industry", "Industry"),
    ("by_location", "state", "State / Region"),
    ("by_city", "city", "City"),
    ("by_job_family", "job_family", "Job Family"),
    ("by_seniority", "seniority_level", "Seniority"),
    ("by_work_arrangement", "work_arrangement", "Work Arrangement"),
    ("by_employment_type", "employment_type", "Employment Type"),
    ("by_platform", "source_platform", "Source Platform"),
)

_NUMBER = re.compile(r"[^0-9.]")


def compute_trends(postings: Iterable[Any]) -> Dict[str, Any]:
    """Return the full trend summary for the given postings."""
    jobs = list(postings)
    total = len(jobs)
    trends: Dict[str, Any] = {
        "generated_at": _dt.datetime.now().replace(microsecond=0).isoformat(),
        "total_postings": total,
        "with_salary": sum(1 for p in jobs if has_salary(p.min_salary, p.max_salary)),
    }
    for key, attr, label in BREAKDOWNS:
        counts = Counter(
            (getattr(p, attr, "") or "").strip() or "Unknown" for p in jobs
        )
        trends[key] = {"label": label, "rows": _rows(counts, total)}
    trends["salary"] = _salary_stats(jobs)
    return trends


def _rows(counts: Counter, total: int) -> List[Dict[str, Any]]:
    rows = []
    for value, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        rows.append(
            {
                "value": value,
                "count": count,
                "share": round(count / total, 4) if total else 0.0,
            }
        )
    return rows


def _to_number(value: str):
    if not value:
        return None
    cleaned = _NUMBER.sub("", str(value))
    try:
        return float(cleaned)
    except ValueError:
        return None


def _median(values: List[float]):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _salary_stats(jobs: List[Any]) -> Dict[str, Any]:
    values: List[float] = []
    for posting in jobs:
        for raw in (posting.min_salary, posting.max_salary):
            number = _to_number(raw)
            if number is not None:
                values.append(number)
    currencies = Counter((p.currency or "").strip() for p in jobs if p.currency)
    return {
        "count": len(values),
        "min": min(values) if values else None,
        "median": _median(values),
        "max": max(values) if values else None,
        "currency": currencies.most_common(1)[0][0] if currencies else "",
    }


def write_trends_json(trends: Dict[str, Any], path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(trends, fh, indent=2, ensure_ascii=False)


def build_trends_markdown(trends: Dict[str, Any], date: str) -> str:
    lines: List[str] = [
        f"# Hiring Trends — {date}",
        "",
        f"Total active India postings analysed: **{trends['total_postings']}**. "
        f"Of these, **{trends['with_salary']}** publish a salary range.",
        "",
    ]
    salary = trends.get("salary", {})
    if salary.get("count"):
        unit = f" {salary['currency']}".rstrip()
        lines += [
            "## Published salary (numeric values found)",
            "",
            f"- Values observed: {salary['count']}",
            f"- Minimum: {_fmt(salary['min'])}{unit}",
            f"- Median: {_fmt(salary['median'])}{unit}",
            f"- Maximum: {_fmt(salary['max'])}{unit}",
            "",
        ]
    for key, _attr, _label in BREAKDOWNS:
        block = trends.get(key)
        if not block:
            continue
        lines += [f"## Postings by {block['label']}", "", "| Value | Postings | Share |", "|---|---:|---:|"]
        for row in block["rows"]:
            lines.append(f"| {row['value']} | {row['count']} | {row['share'] * 100:.1f}% |")
        lines.append("")
    return "\n".join(lines)


def _fmt(value) -> str:
    if value is None:
        return "n/a"
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}"
