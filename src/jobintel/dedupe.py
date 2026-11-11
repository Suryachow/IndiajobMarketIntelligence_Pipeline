"""Duplicate detection and removal.

Two employers can publish the same underlying opening more than once (multiple
locations, reposts, multi-site listings). We apply, in order:

1. an exact content-hash collapse (identical company + requisition + title +
   location);
2. a "business key" collapse on ``(company, requisition_id)`` when the employer
   exposes a requisition id;
3. a fuzzy collapse on ``(company, normalized title, primary city)`` for
   postings with no usable id.

The first occurrence (stable ordering) is kept; every removal is reported so the
run is auditable.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .normalize import normalize_title, slugify
from .schema import JobPosting


def deduplicate(postings: List[JobPosting]) -> Tuple[List[JobPosting], Dict[str, int]]:
    """Return ``(unique_postings, stats)``."""
    kept: List[JobPosting] = []
    seen_hashes: Dict[str, int] = {}
    seen_business: Dict[str, int] = {}
    stats = {"input": len(postings), "removed_exact_hash": 0, "removed_business_key": 0, "removed_fuzzy": 0}

    for posting in postings:
        h = posting.source_record_hash
        if h and h in seen_hashes:
            stats["removed_exact_hash"] += 1
            continue

        company_slug = posting.source_company_slug or slugify(posting.company_name)
        business_key = ""
        if posting.job_id:
            business_key = f"{company_slug}::{posting.job_id.strip().lower()}"
        if business_key and business_key in seen_business:
            stats["removed_business_key"] += 1
            continue

        fuzzy_key = "{}::{}::{}".format(
            company_slug,
            normalize_title(posting.job_title),
            (posting.city or "").strip().lower(),
        )
        if not business_key and fuzzy_key in seen_business:
            stats["removed_fuzzy"] += 1
            continue

        if h:
            seen_hashes[h] = len(kept)
        if business_key:
            seen_business[business_key] = len(kept)
        seen_business[fuzzy_key] = len(kept)
        kept.append(posting)

    stats["output"] = len(kept)
    stats["removed_total"] = stats["input"] - stats["output"]
    return kept, stats
