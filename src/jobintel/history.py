"""Daily-refresh history: timestamped snapshots and change detection.

Each run writes a snapshot of the consolidated dataset. Comparing consecutive
snapshots classifies every posting as **new**, **modified** or **closed** using
a stable business key plus the content hash, which is exactly what the optional
"detecting newly published, modified and closed postings" and "historical
records" capabilities require.

Storage is plain JSON under ``history/`` so it needs no database:
``history/snapshots/snapshot_<date>.json`` and ``history/changes/changes_<date>.json``.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from .schema import JobPosting

# Fields captured per posting in a snapshot (enough to describe and diff a job).
SNAPSHOT_FIELDS = (
    "job_id",
    "company_name",
    "industry",
    "job_title",
    "job_location",
    "city",
    "state",
    "employment_type",
    "work_arrangement",
    "job_family",
    "seniority_level",
    "min_salary",
    "max_salary",
    "currency",
    "posting_date",
    "job_status",
    "job_url",
    "source_record_hash",
)


def posting_key(posting: JobPosting) -> str:
    """Stable identity for a posting across runs.

    Prefers ``<company_slug>:<job_id>``; falls back to the job URL when no
    requisition id is published.
    """
    company = (posting.source_company_slug or posting.company_name or "").strip().lower()
    ident = (posting.job_id or "").strip() or (posting.job_url or "").strip()
    return f"{company}:{ident}"


def snapshot_records(postings: List[JobPosting]) -> Dict[str, Dict[str, Any]]:
    """Return ``{key: record}`` for the given postings."""
    records: Dict[str, Dict[str, Any]] = {}
    for posting in postings:
        record = {field: str(getattr(posting, field, "") or "") for field in SNAPSHOT_FIELDS}
        records[posting_key(posting)] = record
    return records


def save_snapshot(
    postings: List[JobPosting], history_dir: str, date: str
) -> str:
    """Write ``history/snapshots/snapshot_<date>.json`` and return its path."""
    snapshots_dir = os.path.join(history_dir, "snapshots")
    os.makedirs(snapshots_dir, exist_ok=True)
    path = os.path.join(snapshots_dir, f"snapshot_{date}.json")
    payload = {
        "date": date,
        "generated_at": _dt.datetime.now().replace(microsecond=0).isoformat(),
        "count": len(postings),
        "postings": snapshot_records(postings),
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
    return path


def list_snapshots(history_dir: str) -> List[str]:
    """Return snapshot file paths sorted oldest-first."""
    return sorted(glob.glob(os.path.join(history_dir, "snapshots", "snapshot_*.json")))


def load_snapshot(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def latest_snapshot_before(history_dir: str, date: str) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return the most recent snapshot strictly before ``date`` (or ``None``)."""
    candidates = [p for p in list_snapshots(history_dir) if _date_of(p) < date]
    if not candidates:
        return None
    path = candidates[-1]
    return path, load_snapshot(path)


def _date_of(path: str) -> str:
    base = os.path.basename(path)
    return base[len("snapshot_"):-len(".json")]


def diff_snapshots(
    previous: Optional[Dict[str, Any]], current: Dict[str, Any], compared_to: str = ""
) -> Dict[str, Any]:
    """Classify postings between two snapshots into new / modified / closed.

    With no previous snapshot the current run is a baseline and every posting
    is reported under ``new`` only when ``baseline`` is true.
    """
    if previous is None:
        return {
            "baseline": True,
            "compared_to": "",
            "counts": {
                "new": 0,
                "modified": 0,
                "closed": 0,
                "unchanged": len(current.get("postings", {})),
            },
            "new": [],
            "modified": [],
            "closed": [],
        }

    prev_map: Dict[str, Any] = previous.get("postings", {})
    cur_map: Dict[str, Any] = current.get("postings", {})

    new: List[Dict[str, Any]] = []
    modified: List[Dict[str, Any]] = []
    closed: List[Dict[str, Any]] = []
    unchanged = 0

    for key, record in cur_map.items():
        old = prev_map.get(key)
        if old is None:
            new.append({"key": key, **record})
        elif old.get("source_record_hash") != record.get("source_record_hash"):
            modified.append({"key": key, "before": old, "after": record})
        else:
            unchanged += 1

    for key, record in prev_map.items():
        if key not in cur_map:
            closed.append({"key": key, **record})

    new.sort(key=lambda r: (r.get("company_name", ""), r.get("job_title", "")))
    modified.sort(key=lambda r: (r["after"].get("company_name", ""), r["after"].get("job_title", "")))
    closed.sort(key=lambda r: (r.get("company_name", ""), r.get("job_title", "")))

    return {
        "baseline": False,
        "compared_to": compared_to,
        "counts": {
            "new": len(new),
            "modified": len(modified),
            "closed": len(closed),
            "unchanged": unchanged,
        },
        "new": new,
        "modified": modified,
        "closed": closed,
    }


def save_change_report(changes: Dict[str, Any], history_dir: str, date: str) -> str:
    """Persist a run's change set to ``history/changes/changes_<date>.json``."""
    changes_dir = os.path.join(history_dir, "changes")
    os.makedirs(changes_dir, exist_ok=True)
    path = os.path.join(changes_dir, f"changes_{date}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"date": date, **changes}, fh, indent=2, ensure_ascii=False)
    return path


def build_change_markdown(changes: Dict[str, Any], date: str) -> str:
    """Render a human-readable change report."""
    counts = changes["counts"]
    lines: List[str] = [
        f"# Posting Changes — {date}",
        "",
    ]
    if changes.get("baseline"):
        lines += [
            "Baseline snapshot: this is the first run, so there is no previous state to diff "
            "against. Subsequent runs report new / modified / closed postings here.",
            "",
            f"- Postings in this snapshot: **{counts['unchanged']}**",
            "",
        ]
        return "\n".join(lines)

    lines += [
        f"_Compared with snapshot dated {changes.get('compared_to', 'n/a')}._",
        "",
        f"- **New:** {counts['new']}",
        f"- **Modified:** {counts['modified']}",
        f"- **Closed:** {counts['closed']}",
        f"- Unchanged: {counts['unchanged']}",
        "",
    ]

    def table(title: str, rows: List[Dict[str, Any]], extra=None) -> None:
        lines.append(f"## {title} ({len(rows)})")
        lines.append("")
        if not rows:
            lines.extend(["_None._", ""])
            return
        lines.append("| Company | Job Title | Location | Job URL |")
        lines.append("|---|---|---|---|")
        for row in rows[:200]:
            lines.append(
                f"| {row.get('company_name','')} | {row.get('job_title','')} | "
                f"{row.get('job_location','')} | {row.get('job_url','')} |"
            )
        if len(rows) > 200:
            lines.append(f"| _… {len(rows) - 200} more_ | | | |")
        lines.append("")

    table("New postings", changes["new"])
    table("Modified postings", [m["after"] for m in changes["modified"]])
    table("Closed postings", changes["closed"])
    return "\n".join(lines)
