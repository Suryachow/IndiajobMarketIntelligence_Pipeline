# Executive Summary — India Job Market Intelligence Pipeline

## What was built

A working, dependency-free data-engineering pipeline that collects **publicly advertised,
active job postings located in India** from the official careers portals of **11 leading
employers across 11 industries**, standardizes them into **one common schema**, removes
duplicates, validates required fields, and writes a single consolidated dataset.

It solves the three problems in the brief directly:

- **Accessibility** — six different recruitment technologies are handled behind one interface:
  two proprietary platforms (Amazon, TCS iBegin) plus four commercial ATSs (Workday ×6,
  SmartRecruiters, Greenhouse, Lever). Each is a small, isolated adapter.
- **Standardization** — every posting converges on one canonical `JobPosting` model (26 columns
  incl. the two derived analytics columns `job_family` / `seniority_level`),
  regardless of source. HTML is flattened to text, dates normalized to ISO, locations parsed
  into city/state/country, and employment type / work arrangement mapped to controlled
  vocabularies.
- **Reliability** — retries with backoff, a client-side rate limiter, an on-disk response cache,
  per-item failure isolation, 3-tier deduplication, and required-field validation.

## What worked

- **Full, verifiable coverage.** All 11 employers were extracted to their portal's own advertised
  India-active total: **4,635 postings advertised, 4,626 extracted (99.8%), 0 exhausted retries**,
  deduped to **4,596 unique records** (30 duplicates removed). Coverage denominators come from each
  source's own counts (never estimated); where a denominator cannot be established the report says
  `unknown`.
- **Six platform families proven** by live acquisition, including automatic discovery of each
  Workday tenant's India facet (with a full-scan fallback for the one tenant that exposes none).
- **Reproducible by design.** Pure Python standard library — no `pip install`, no credentials, no
  paid services. One command (`py run.py`) regenerates every deliverable.
- **Extensible by design.** Onboarding an employer already on a supported platform is a
  **config-only** change (`config/companies.json`); a new platform needs one ~100-line adapter.

## Advanced capabilities (optional challenge — all six built)

Beyond the core requirements, every optional capability is implemented and produced by the same
`py run.py` run — still stdlib-only:

- **Automated daily refresh** — runs are idempotent and cached; schedule `py run.py` daily.
- **New / modified / closed detection** — each run writes a JSON snapshot keyed on
  `(company_slug, job_id)` + `source_record_hash`, diffs it against the previous snapshot, and
  emits a change report (`history/changes/changes_<date>.{json,md}`).
- **Historical records** — timestamped snapshots under `history/snapshots/` preserve the state of
  the dataset over time (no database required).
- **Job-family & seniority classification** — deterministic `job_family` (18 standardized
  families) and `seniority_level` (Entry→Executive) columns; `job_family` is populated for every
  row and `seniority_level` for 4,151 of 4,596 (the rest are left blank rather than guessed).
- **Hiring trends** — `hiring_trends.{md,json}` with share-of-total breakdowns by company,
  industry, location, city, job family, seniority, work arrangement, employment type and platform.
- **Query API & dashboard** — `py run.py --api` serves a read-only HTTP API
  (`/api/jobs`, `/api/jobs/<key>`, `/api/stats`, `/api/trends`) plus a self-contained HTML
  dashboard.

## What did not / honest limitations

- **Salary data is sparse** — only 1 of 4,596 rows publishes a salary range. This reflects **what
  the sources actually disclose**, not a defect. Salary is parsed whenever a source publishes it
  (e.g. a Sutherland posting states "Salary: ₹18k - ₹40k in-hand per month", annualised to
  ₹216,000–₹480,000); structured ATS pay fields (Greenhouse `pay_input_ranges`, Lever
  `salaryRange`) and JSON-LD `baseSalary` are also mapped. Where no amount is published the two
  salary columns read **`Not published`** — per the brief, no figure is fabricated.
- **Work arrangement is complete but defaults to `Onsite`.** Only a minority of postings state an
  arrangement, so `Remote`/`Hybrid` are set solely from an explicit signal (structured ATS fields,
  a `Remote`/`… - Virtual` location, or an explicit phrase); all remaining rows are labelled
  `Onsite`. The reference run has **no blanks** (4,194 Onsite / 237 Hybrid / 165 Remote), so the
  Onsite value is a documented default rather than a source-published fact.
- **No employer publishes machine-readable pay for India** in these sources: a scan of all live
  responses found zero structured pay fields, so the near-zero salary coverage is a property of the
  sources rather than a parsing gap.
- **Several large Indian employers** (e.g. HDFC, Airtel) run proprietary portals without a
  clean public JSON API and were excluded rather than scraped in a fragile or non-compliant way.
- **Row counts drift slightly between runs.** A few TCS iBegin postings differ run-to-run because
  the portal drives a live browser session against a server-side re-sorted "recent" feed; the
  change-detection report (above) surfaces this rather than hiding it.

## Built to scale

The pipeline is shaped for production growth:

- **Scheduling** — the run is idempotent and cached; schedule it daily (Task Scheduler / cron).
- **Change tracking & history** — snapshots + the new/modified/closed diff are already produced
  every run (see Advanced capabilities).
- **Observability** — `run_log.json` records per-company coverage, notes and errors every run for
  alerting.
- **New employers** — registry-driven adapters mean most additions are configuration, not code.

## Bottom line

A smaller, honest, fully reproducible solution that reaches **99.8% coverage of the discoverable
India-active postings across 11 employers and six recruitment platforms** — with clear, auditable
reporting of what was collected, what was missing, and why — rather than a larger dataset built on
unverifiable or fabricated data.

**Key artifacts:** `india_active_jobs_master.csv` (4,596 rows × 26 columns) · `coverage_report.md` ·
`run_log.json` · `hiring_trends.{md,json}` · `history/snapshots/` + `history/changes/` ·
`run.py` + `src/jobintel/` · `README.md` (full technical documentation).
