"""Unit tests for snapshotting and change detection (new / modified / closed)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.history import (  # noqa: E402
    build_change_markdown,
    diff_snapshots,
    latest_snapshot_before,
    list_snapshots,
    posting_key,
    save_change_report,
    save_snapshot,
    snapshot_records,
)
from jobintel.schema import JobPosting  # noqa: E402


def _posting(job_id: str, title: str, record_hash: str) -> JobPosting:
    return JobPosting(
        company_name="Acme",
        industry="Technology",
        job_id=job_id,
        job_title=title,
        job_location="Bengaluru, India",
        job_url=f"https://example.test/{job_id}",
        source_company_slug="acme",
        source_record_hash=record_hash,
    )


class TestPostingKey(unittest.TestCase):
    def test_prefers_slug_and_job_id(self):
        self.assertEqual(posting_key(_posting("R1", "Engineer", "h")), "acme:R1")

    def test_falls_back_to_url(self):
        posting = _posting("", "Engineer", "h")
        self.assertEqual(posting_key(posting), "acme:https://example.test/")


class TestDiffSnapshots(unittest.TestCase):
    def test_classifies_new_modified_closed(self):
        previous = {"postings": snapshot_records([
            _posting("R1", "Engineer", "hash-1"),   # unchanged
            _posting("R2", "Analyst", "hash-2"),    # modified below
            _posting("R3", "Recruiter", "hash-3"),  # closed (absent from current)
        ])}
        current = {"postings": snapshot_records([
            _posting("R1", "Engineer", "hash-1"),
            _posting("R2", "Senior Analyst", "hash-2b"),
            _posting("R4", "Designer", "hash-4"),   # new
        ])}

        changes = diff_snapshots(previous, current, compared_to="2026-11-10")

        self.assertFalse(changes["baseline"])
        self.assertEqual(changes["compared_to"], "2026-11-10")
        self.assertEqual(changes["counts"], {"new": 1, "modified": 1, "closed": 1, "unchanged": 1})
        self.assertEqual(changes["new"][0]["key"], "acme:R4")
        self.assertEqual(changes["closed"][0]["key"], "acme:R3")
        self.assertEqual(changes["modified"][0]["after"]["job_title"], "Senior Analyst")

    def test_first_run_is_baseline(self):
        current = {"postings": snapshot_records([_posting("R1", "Engineer", "h")])}
        changes = diff_snapshots(None, current)
        self.assertTrue(changes["baseline"])
        self.assertEqual(changes["counts"]["new"], 0)
        self.assertEqual(changes["counts"]["unchanged"], 1)


class TestSnapshotStore(unittest.TestCase):
    def test_roundtrip_and_latest_before(self):
        with tempfile.TemporaryDirectory() as directory:
            save_snapshot([_posting("R1", "Engineer", "h")], directory, "2026-11-10")
            save_snapshot([_posting("R2", "Analyst", "h2")], directory, "2026-11-11")

            self.assertEqual(len(list_snapshots(directory)), 2)
            path, snapshot = latest_snapshot_before(directory, "2026-11-11")
            self.assertTrue(path.endswith("snapshot_2026-11-10.json"))
            self.assertIn("acme:R1", snapshot["postings"])
            self.assertIsNone(latest_snapshot_before(directory, "2026-11-09"))

            change_path = save_change_report(
                {"baseline": True, "compared_to": "", "counts": {"new": 0, "modified": 0, "closed": 0, "unchanged": 1},
                 "new": [], "modified": [], "closed": []},
                directory,
                "2026-11-11",
            )
            self.assertTrue(os.path.exists(change_path))


class TestChangeMarkdown(unittest.TestCase):
    def test_baseline_and_diff_render(self):
        baseline = build_change_markdown(
            {"baseline": True, "counts": {"new": 0, "modified": 0, "closed": 0, "unchanged": 5}},
            "2026-11-11",
        )
        self.assertIn("Baseline snapshot", baseline)
        self.assertIn("**5**", baseline)

        diff = build_change_markdown(
            {
                "baseline": False,
                "compared_to": "2026-11-10",
                "counts": {"new": 1, "modified": 0, "closed": 1, "unchanged": 3},
                "new": [{"company_name": "Acme", "job_title": "Designer", "job_location": "Pune", "job_url": "u"}],
                "modified": [],
                "closed": [{"company_name": "Acme", "job_title": "Recruiter", "job_location": "Pune", "job_url": "u2"}],
            },
            "2026-11-11",
        )
        self.assertIn("Compared with snapshot dated 2026-11-10", diff)
        self.assertIn("**New:** 1", diff)
        self.assertIn("## Closed postings (1)", diff)


if __name__ == "__main__":
    unittest.main()
