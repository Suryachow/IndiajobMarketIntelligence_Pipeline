# India Active Jobs — Enterprise Job Market Intelligence

A reproducible, dependency-free data-engineering pipeline that collects **publicly
advertised, active job postings located in India** from the official career portals of
**11 leading employers across 11 industries**, normalizes them into **one common schema**,
removes duplicates, validates required fields, and writes a single consolidated dataset —
regardless of which applicant-tracking system (ATS) each employer uses.

**Final dataset:** [`india_active_jobs_master.csv`](india_active_jobs_master.csv) — 4,596 rows × 26 columns.
**Coverage report:** [`coverage_report.md`](coverage_report.md) · [`coverage_report.csv`](coverage_report.csv).
**Run log (machine-readable):** [`run_log.json`](run_log.json).
**Hiring trends:** [`hiring_trends.md`](hiring_trends.md) · [`hiring_trends.json`](hiring_trends.json).
**Change history:** `history/snapshots/snapshot_<date>.json` · `history/changes/changes_<date>.{json,md}`.
**Query API / dashboard:** `py run.py --api` → <http://127.0.0.1:8000/>.
**Local job browser:** `job_details.html` — click a job title to view its extracted salary and
description, then open the original employer posting if needed.
> trends, and a query API/dashboard — are implemented and are produced by the same `py run.py` run.

---

## 1. Solution architecture

The design follows a clean separation of concerns so that adding an employer or a new
recruitment platform is a small, isolated change.

```
                     config/companies.json        (company registry — the only file you
                              │                     edit to onboard an employer)
                              ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │  pipeline.run_pipeline()   — orchestration, per-employer isolation │
   └───────────────┬──────────────────────────────────────────────────┘
                   ▼
   ┌───────────────────────────┐     platform-specific adapters, one per ATS
   │  extractors/  (REGISTRY)  │──►  workday · amazon · smartrecruiters
   │  BaseExtractor → extract() │     greenhouse · lever · tcs
   └───────────────┬───────────┘
                   │  raw listing + detail JSON
                   ▼
   ┌───────────────────────────┐
   │  http_client.HttpClient   │  retries+backoff+jitter · rate limiter (rps)
   │                           │  on-disk response cache · thread-pool fan-out
   │  render.RenderingClient   │  optional: same interface, renders pages in a
   │  (optional, --render)     │  headless browser for JavaScript/SPA sites
   └───────────────┬───────────┘
                   ▼
   ┌───────────────────────────┐
   │  normalize.py             │  HTML→text · date parsing · India detection
   │  schema.JobPosting/validate│  controlled vocabularies · salary/experience
   └───────────────┬───────────┘
                   ▼
   ┌───────────────────────────┐
   │  dedupe.deduplicate()     │  exact-hash → business-key → fuzzy
   └───────────────┬───────────┘
                   ▼
   ┌───────────────────────────┐
   │  classify.py              │  derived analytics: job_family · seniority_level
   └───────────────┬───────────┘
                   ▼
   ┌───────────────────────────┐
   │  reporting.py             │  india_active_jobs_master.csv · coverage_report.*
   │  history.py               │  run_log.json · daily snapshot · change report (new/
   │  trends.py                │  modified/closed) · hiring trends by company/industry/
   │  api.py  (optional, --api)│  location/family · query API + dashboard
   └───────────────────────────┘
```

**Layers**

| Layer | Module | Responsibility |
|---|---|---|
| Registry (config) | [`config/companies.json`](config/companies.json) | Declares which employers to collect, their industry, careers URL, and which adapter to use. |
| Orchestration | [`pipeline.py`](src/jobintel/pipeline.py) | Iterates employers, isolates failures per employer, triggers classification + dedupe, writes outputs, snapshots history. |
| Adapters | [`extractors/`](src/jobintel/extractors) | One class per ATS. Knows the endpoint shape, pagination and field mapping. |
| Transport | [`http_client.py`](src/jobintel/http_client.py) | Reliable, polite HTTP: retries, backoff, rate limit, cache, concurrency. |
| Rendering (optional) | [`render.py`](src/jobintel/render.py) | Optional Playwright-backed headless browser: a drop-in client that renders JavaScript/SPA pages, used only when `--render` is passed and Playwright is installed. |
| Model | [`schema.py`](src/jobintel/schema.py) | Canonical `JobPosting` + `CSV_FIELDS` + `validate()`. Everything converges here. |
| Normalization | [`normalize.py`](src/jobintel/normalize.py) | Text, dates, locations, controlled vocabularies, salary/experience extraction. |
| Deduplication | [`dedupe.py`](src/jobintel/dedupe.py) | 3-tier duplicate removal with auditable statistics. |
| Classification | [`classify.py`](src/jobintel/classify.py) | Deterministic `job_family` + `seniority_level` (derived analytics columns). |
| History | [`history.py`](src/jobintel/history.py) | Daily snapshots + change detection (new / modified / closed) + history store. |
| Trends | [`trends.py`](src/jobintel/trends.py) | Hiring trends by company / industry / location / family / seniority. |
| Query API | [`api.py`](src/jobintel/api.py) | Optional (stdlib) HTTP API + dashboard over the consolidated dataset. |
| Output | [`reporting.py`](src/jobintel/reporting.py) | Master CSV, coverage CSV/Markdown, run log. |

The same building blocks also power a **universal crawler** for any employer — not just the ten
in the registry — via the detector + router + generic (sitemap + JSON-LD) fallback. See
[§13](#13-universal-crawler-for-any-company).

The pipeline is **pure Python standard library** (verified on CPython 3.14; runs on 3.9+).
There are **no third-party runtime dependencies** — no `pip install` is required to
reproduce the dataset. See [§11](#11-installation--execution).

---

## 2. Selected companies and source URLs

Eleven employers — the ten-industry core set, plus **TCS** (added on request) — each with an
official careers portal and publicly discoverable India-based openings:

| # | Company | Industry | Recruitment platform | Official careers portal |
|---|---|---|---|---|
| 1 | Amazon | E-commerce & Cloud Computing | Amazon (proprietary) | https://www.amazon.jobs/en/ |
| 2 | Barclays | Banking & Financial Services | Workday | https://barclays.wd3.myworkdayjobs.com/External_Career_Site_Barclays |
| 3 | Verizon | Telecommunications | Workday | https://verizon.wd12.myworkdayjobs.com/verizon-careers |
| 4 | NVIDIA | Semiconductors | Workday | https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite |
| 5 | Cisco | Technology Hardware & Networking | Workday | https://cisco.wd5.myworkdayjobs.com/Cisco_Careers |
| 6 | Pfizer | Pharmaceuticals & Life Sciences | Workday | https://pfizer.wd1.myworkdayjobs.com/PfizerCareers |
| 7 | Salesforce | Enterprise Software | Workday | https://salesforce.wd12.myworkdayjobs.com/External_Career_Site |
| 8 | Sutherland | IT Services & BPM | SmartRecruiters | https://jobs.smartrecruiters.com/Sutherland |
| 9 | Groww | Financial Technology (Fintech) | Greenhouse | https://boards.greenhouse.io/groww |
| 10 | Meesho | E-commerce & Internet | Lever | https://jobs.lever.co/meesho |
| 11 | Tata Consultancy Services | IT Services & Consulting | TCS iBegin (proprietary; headless) | https://ibegin.tcsapps.com/candidate/next/en-IN/landing |

**Why these companies.** They span distinct industries, giving genuine schema-heterogeneity
(the assignment's core challenge) while all operating substantial India-based functions. They
deliberately cover **six different recruitment technologies** — a proprietary JSON API
(Amazon), four commercial ATSs (Workday / SmartRecruiters / Greenhouse / Lever), and a
**client-rendered Angular SPA** (TCS iBegin) — so the solution demonstrates that a common data
model can be produced regardless of the underlying system.

**Source policy.** Every source is the **employer's own official careers portal**. Third-party
job aggregators are not used as a primary source. No authentication, CAPTCHA, or access control
is bypassed; the pipeline calls the same public, unauthenticated JSON endpoints that the
employers' own careers pages call in the browser.

---

## 3. Data acquisition approach per company

Each adapter in [`src/jobintel/extractors/`](src/jobintel/extractors) implements the same
contract — `BaseExtractor.extract() -> ExtractionResult` — but talks to the platform in the
way that platform requires.

### Workday (Barclays, Verizon, NVIDIA, Cisco, Pfizer, Salesforce)

Workday tenants expose an unauthenticated "CXS" JSON API:

1. `POST https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` returns a
   **facet tree** plus a page of `jobPostings`.
2. The adapter **discovers the India facet automatically**. Tenants differ, so two strategies
   are tried in order:
   - **country facet** — a flat facet (e.g. `Location_Country`) whose value literally is
     "India" (used by Pfizer, Salesforce);
   - **grouped location facet** — `locationMainGroup` → a `Sites`/`Locations` sub-group whose
     leaf descriptors contain "India"; the leaf IDs are passed via the generic `locations`
     filter key (used by Verizon, NVIDIA, Cisco).
3. It **pages** through the India-filtered result set (20 per request) and then fetches each
   posting's detail via `GET .../{externalPath}` for the full description.

If **no** India facet can be discovered (this happens for **Barclays**), the adapter falls back
to a **full scan** of the unfiltered result set and keeps postings whose `locationsText`
matches India. The fallback is recorded in the coverage report for transparency.

**Observed Workday quirk handled:** Workday returns the authoritative `total` **only on the
first page**; later pages report `total: 0`. The adapter latches the first non-null total so
pagination terminates correctly instead of stopping after page 1.

### Amazon (Amazon)

`https://www.amazon.jobs/en/search.json?country=IND&result_limit=100&offset=…&sort=recent`
returns a paged JSON payload that already embeds the **full description and qualifications**,
so a single pass suffices (no per-job detail request). The job description is composed from
`description` + `basic_qualifications` + `preferred_qualifications`. India is scoped server-side
via `country=IND`.

### SmartRecruiters (Sutherland)

`GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=…` is paged
over the whole board; postings whose `location.country` resolves to India are retained, then a
**per-posting detail** call (`…/postings/{id}`) fetches the job advertisement sections.

### Greenhouse (Groww)

`GET https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true` returns the entire
board (with HTML content) in one response — no pagination. Postings whose `location.name`
matches India are retained. Because Greenhouse often publishes only a city/state, the India
matcher uses the city/state vocabulary (see [§5](#5-normalization-logic)).

### Lever (Meesho)

`GET https://api.lever.co/v0/postings/{slug}?mode=json` returns every live posting with
description in a single JSON array. Postings whose `categories.location` matches India are
retained; description is composed from `descriptionPlain` + the `lists[]` blocks + `additional`.

### TCS iBegin (Tata Consultancy Services) — headless browser

The odd one out, and the reason the optional renderer exists. `ibegin.tcsapps.com` is a
**client-rendered Angular SPA**: every path (including `/robots.txt`) returns the same
JavaScript shell, and `www.tcs.com` itself sits behind a WAF that answers non-browser clients
with `403`. The data is reachable only through the SPA's own **public** JSON API, and only from
a real browser session — the API is gated by an `XSRF-TOKEN` cookie that must be echoed back in
a `next-xsrf-token` request header.

The adapter therefore opens one headless Chromium session and, inside the loaded page (so the
browser supplies its own cookies and headers), calls:

1. `POST {base}/api/{locale}/search/jobs` — the paginated index (`resultsPerPage` must be
   10/25/50); it returns `totalElements`/`totalPages`, giving an exact coverage denominator.
2. `GET {base}/api/{locale}/job/desc/{id}` — the full description, qualifications and function
   for one posting, fetched **concurrently per batch** to stay quick but polite.

Every posting maps to the canonical schema: `job_id`←`id`, `job_title`←`title`, `city/state/
country`←`location`, `job_category`←`domain`, `business_division`←`role`, `experience_required`
←the published `min/max` years, `job_description`←the detail payload, and `job_url`←
`{base}/{locale}/jobs/{id}`. `job_status` is `Active`; postings whose published `applyBy` date has
already passed are filtered out as closed. **No login, CAPTCHA or WAF is defeated** — the adapter
drives the public page exactly as a visitor's browser does and keeps only the session cookie the
site itself issues.

### Reliability primitives applied to every source

- **Retry with exponential backoff + jitter** on `408/425/429/5xx` and transient network errors
  ([`http_client.py`](src/jobintel/http_client.py)).
- **Client-side rate limiter** (default `6 requests/second`, adjustable via `--rps`) to stay
  within reasonable request limits.
- **On-disk response cache** so re-runs are fast, cheap and reproducible.
- **Thread-pool fan-out** (default 4 workers) for the I/O-bound per-job detail fetches, with
  per-item failure isolation.

---

## 4. Consolidated data model

Every posting from every employer is normalized into the canonical
[`JobPosting`](src/jobintel/schema.py) dataclass and emitted with exactly the columns in
`CSV_FIELDS` (26 columns):

| Column | Required? | Notes |
|---|---|---|
| `company_name` | ✔ required | From the registry. |
| `industry` | ✔ required | From the registry. |
| `job_id` | where available | Employer requisition ID (Workday `jobReqId`, Amazon `id_icims`, etc.). |
| `job_title` | ✔ required | Published title. |
| `job_description` | where accessible | Full advertisement as clean plain text. |
| `job_category` | where available | Functional classification / job family. |
| `business_division` | where available | Business unit / department / team. |
| `job_location` | where published | Raw location string as published. |
| `city`, `state`, `country` | — | Parsed components (extra convenience fields). |
| `employment_type` | where available | Controlled vocabulary (Full-time, Part-time, …). |
| `work_arrangement` | where available | Remote / Hybrid / Onsite. Remote/Hybrid only from an explicit signal; defaults to `Onsite`. |
| `experience_required` | where available | Only when explicitly stated at source. |
| `min_salary`, `max_salary`, `currency` | where available | From published text, structured ATS pay fields, or JSON-LD; salary columns read `Not published` when no amount is disclosed. |
| `posting_date` | where available | Normalized to ISO `YYYY-MM-DD`. |
| `job_status` | ✔ required | `Active` (Workday reports `Closed` when `canApply=false`). |
| `job_url` | ✔ required | Direct link to the official posting. |
| `extraction_date` | ✔ required | Date the data was retrieved. |
| `job_family` | derived | Standardized job family (18 families, `Other` fallback) — see [§12](#12-advanced-capabilities-optional-challenge). |
| `seniority_level` | derived | Standardized seniority (Executive…Internship); blank when the source carries no signal. |
| `source_platform` | lineage | e.g. `workday`, `lever`. |

Lineage keys used for deduplication and change detection are retained internally and in history
snapshots, but are not exposed as CSV columns. The file is written as **UTF-8 with BOM**
(`utf-8-sig`) so it opens cleanly in Excel while
remaining valid UTF-8.

---

## 5. Normalization logic

[`normalize.py`](src/jobintel/normalize.py) is deliberately **conservative**: heuristics fire
only on explicit textual signals, so the output stays auditable against the source.

- **HTML → plain text** — strips `<script>`/`<style>`, converts `<br>`/block tags to newlines,
  unescapes entities, collapses whitespace.
- **Dates** — handles Workday relative strings (`Posted Today`, `Posted Yesterday`,
  `Posted 30+ Days Ago`), absolute formats (`November 5, 2026`, ISO, `dd/mm/yyyy`), and epoch
  seconds/millis; emits ISO `YYYY-MM-DD` or blank.
- **India detection** — matches the country name, the ISO code `IN`/`IND` as a *standalone*
  token (so `"Indiana, US"` is correctly rejected), any Indian state, or any major Indian city.
  The city/state vocabulary is essential because Lever/Greenhouse frequently publish bare
  `"Bengaluru"` or `"Tamil Nadu"` with no country.
- **Location parsing** — splits into `(city, state, country)` across Workday's two orderings
  (`India, Karnataka, Bengaluru` and `Bangalore, Karnataka, India`) and Amazon's
  (`IN, MH, Maharashtra`).
- **Controlled vocabularies** — `employment_type` ∈ {Full-time, Part-time, Internship,
  Apprenticeship, Contract, Temporary, Casual, Fixed-term}; `work_arrangement` ∈
  {Remote, Hybrid, Onsite} — only from explicit signals.
- **Experience** — extracts `"5-8 years"`, `"Minimum 3 years"`, `"8+ years"` only when the text
  states it; otherwise blank.
- **Salary** — parses an explicit published range from the posting text (supports `INR`, `₹`, `Rs`,
  `$`, `USD`, and `lpa`/`lakh`/`k` units; monthly figures are annualised ×12), plus structured pay
  published by the ATS itself (Greenhouse `pay_input_ranges`, Lever `salaryRange`) and JSON-LD
  `baseSalary`. Business-metric prose (revenue, deal size, head-count, loans) is rejected by a
  noise filter. **If no pay is published, salary fields stay blank — nothing is ever fabricated.**
  This behaviour is locked down by unit tests.

---

## 6. Deduplication logic

[`dedupe.py`](src/jobintel/dedupe.py) applies three tiers in order; the first occurrence is kept
and every removal is counted for auditability:

1. **Exact content hash** — identical `(company, requisition id, title, location)`
   (a derived internal content hash).
2. **Business key** — `(company, requisition id)` when the employer exposes a requisition ID.
3. **Fuzzy key** — `(company, normalized title, primary city)` for postings with **no** usable ID
   (normalized title lowercases, strips bracketed text, punctuation and boilerplate such as
   "urgent", "immediate joiner").

Multi-location duplicates and reposts are therefore collapsed while genuinely distinct roles at
different cities are preserved. In the reference run, 4,626 raw postings collapsed to **4,596**
(29 exact-hash, 1 business-key). A multi-country Workday posting is represented by its India
location and kept; any posting with no India location at all is dropped by the India gate (§7).

---

## 7. Validation and missing-data handling

- **Required-field validation** (`schema.validate`) — a record missing any of `company_name`,
  `industry`, `job_title`, `job_status`, `job_url`, `extraction_date` is **excluded** and the
  reason is recorded in the run log. `job_url` must be absolute.
- **Salary sanity** — salary present without currency, non-numeric salary, or `min > max` is
  rejected rather than silently kept.
- **Missing information is labelled, never estimated.** Text fields stay blank when a source
  publishes nothing, and the two salary columns carry the explicit label **`Not published`** when
  no amount is disclosed — so "no compensation advertised" is visible rather than looking like a
  parsing gap. Nothing is ever fabricated: the reference run reflects this honestly, with only 1
  of 4,596 rows carrying a real salary range, because that is all the sources actually disclose.
- **India-only gate** — a final pass keeps only postings whose location evidence places them in
  India (`is_india_location(job_location)` or `country ∈ {India, IN}`); anything else is dropped
  and counted in the run log. This guarantees the dataset is India-scoped even if a source returns
  a multi-country posting.
- **Work arrangement** — `Remote` / `Hybrid` are only ever set from an explicit source signal:
  a structured ATS field (Workday `remoteType`, Lever `workplaceType`, SmartRecruiters
  `location.remote`, Ashby `workplaceType`, Workable `telecommuting`), a location value
  (`Remote`, Amazon's `… - Virtual`), or an explicit arrangement phrase in the posting text.
  When a source states nothing, the value is left as the default `Onsite` rather than blanked,
  because India postings are overwhelmingly on-site; a posting with no location at all is left
  blank rather than guessed.
- **Failure isolation** — a failing detail fetch marks that one posting as an error without
  aborting the employer; a failing employer is caught so it cannot abort the whole run.

---

## 8. Coverage methodology

For each employer the pipeline reports:

- **Portal Job Count (India scope)** — the number of India-scoped **active** postings the
  employer's portal advertises. This is the coverage **denominator**, and it is obtained from
  the source itself:
  - Workday → the `total` reported by the India-filtered search, or the count of
    `locationsText`-India matches during a full scan;
  - Amazon → the `hits` count for `country=IND`;
  - SmartRecruiters → the count of India postings found while scanning the entire board;
  - Greenhouse / Lever → the count of India postings in the full board payload.
- **Jobs Extracted** — records successfully retrieved and validated.
- **Coverage %** — `extracted / portal_total`. If the denominator **cannot** be established
  reliably, the pipeline reports **`unknown`** rather than inventing an estimate.

Completeness is verified by driving the source's own pagination to exhaustion (the loop
terminates only when the server's total is reached or an empty page is returned), so the
extracted set equals the discoverable set for every employer in the reference run.

---

## 9. Reference run results

Run of **2026-11-11** (1,139 cached responses, **0 exhausted retries**):

| Company | Industry | Platform | Portal Job Count (India) | Jobs Extracted | Coverage % | Status |
|---|---|---|---:|---:|---:|---|
| Amazon | E-commerce & Cloud Computing | amazon | 2102 | 2103 | 100.0% | OK |
| Barclays | Banking & Financial Services | workday | 282 | 282 | 100.0% | OK |
| Verizon | Telecommunications | workday | 31 | 31 | 100.0% | OK |
| NVIDIA | Semiconductors | workday | 248 | 248 | 100.0% | OK |
| Cisco | Technology Hardware & Networking | workday | 297 | 297 | 100.0% | OK |
| Pfizer | Pharmaceuticals & Life Sciences | workday | 27 | 27 | 100.0% | OK |
| Salesforce | Enterprise Software | workday | 96 | 96 | 100.0% | OK |
| Sutherland | IT Services & BPM | smartrecruiters | 52 | 52 | 100.0% | OK |
| Groww | Financial Technology (Fintech) | greenhouse | 9 | 9 | 100.0% | OK |
| Meesho | E-commerce & Internet | lever | 55 | 55 | 100.0% | OK |
| Tata Consultancy Services | IT Services & Consulting | tcs | 1436 | 1426 | 99.3% | OK |
| **Total** | | | **4635** | **4626** | **99.8%** | |

**Master dataset:** 4,596 unique rows (after removing 30 duplicates) × 26 columns.
**Derived analytics:** `job_family` is populated for all 4,596 rows (`Engineering` 1,317,
`Operations` 514, `IT & Infrastructure` 406, …, `Other` 117); `seniority_level` is populated for
4,151 rows (`Mid` 1,407, `Manager` 852, `Senior` 684, `Entry` 602, `Lead` 413, …) and left blank
for the 445 rows whose title and text carry no level signal.
**Additional outputs for this run:** `hiring_trends.{md,json}`,
`history/snapshots/snapshot_2026-11-11.json`, `history/changes/changes_2026-11-11.{json,md}`.

---

## 10. Known limitations

- **Salary sparsity is a property of the sources, not a bug.** Most Indian postings simply do not
  publish compensation; a scan of every live response found **zero** structured pay fields. The
  pipeline parses pay whenever a source publishes it — from the description text (e.g. Sutherland:
  "Salary: ₹18k - ₹40k in-hand per month" → ₹216,000–₹480,000 annual), from structured ATS fields
  (Greenhouse `pay_input_ranges`, Lever `salaryRange`) and from JSON-LD `baseSalary` — and labels
  the rest **`Not published`**, per the assignment's no-fabrication rule (no amount is invented).
- **Work arrangement defaults to `Onsite`.** Only a minority of postings state an arrangement, so
  `Remote`/`Hybrid` are set solely from an explicit signal and every other posting is labelled
  `Onsite` (see §7). In the reference run this yields 4,194 Onsite, 237 Hybrid and 165 Remote with
  **no blanks**; the Onsite value is therefore a documented default, not a source-published fact.
- **The exact row count drifts slightly between runs, because live portals change.** Most sources
  are served from the on-disk HTTP cache and are byte-for-byte reproducible, but the TCS iBegin
  index is read through a browser session (it needs the portal's `XSRF-TOKEN` handshake, so it is
  not cached) and its "recent" feed is re-sorted server-side while it is being paged through. A
  few postings can therefore be skipped or double-listed from one run to the next (the 3-tier
  dedupe absorbs duplicates; genuine skips show up honestly as lower coverage). This is a
  property of crawling a live portal, not of the pipeline — and it is exactly what the
  new/modified/closed report in [§12](#12-advanced-capabilities-optional-challenge) is designed
  to surface rather than hide.
- **Completeness is bounded by portal pagination.** If a portal silently caps the result set
  below its advertised total, coverage would show <100% rather than being masked.
- **Workday facet heuristics.** Tenant configurations vary; the adapter handles the two common
  facet shapes plus a full-scan fallback. A tenant with a third, unseen shape would fall back to
  the (slower but correct) full scan.
- **Point-in-time snapshot.** The dataset reflects the moment of extraction; postings churn
  daily, so a single run is not itself a history — the timestamped snapshot and change report in
  [§12](#12-advanced-capabilities-optional-challenge) are.
- **Job descriptions are plain text.** Formatting is intentionally flattened for a consistent,
  comparable schema.
- **Not every major Indian employer is on a supported platform.** Several large Indian
  companies (HDFC, Airtel, …) run proprietary portals without a clean public JSON API;
  they were excluded from the *reference dataset* in favour of employers whose public portals
  could be accessed legitimately and completely. They can still be attempted individually with
  the universal crawler — see [§13](#13-universal-crawler-for-any-company) and
  [§14](#14-adding-a-new-company).
- **The universal crawler cannot execute JavaScript.** A careers site that renders all content
  client-side, with neither a sitemap nor JSON-LD, needs a headless browser and is therefore out
  of scope for this dependency-free solution (reported as `FAILED`, not silently empty).
- **`experience_required`, `job_category`, `business_division`** are populated only where the
  source exposes the concept or states it explicitly.

---

## 11. Installation & execution

**Requirements:** Python 3.9+ (no third-party packages).

```powershell
# 1. (optional) create a virtual environment — stdlib only, but keeps things tidy
py -m venv .venv ; .\.venv\Scripts\Activate.ps1

# 2. Full run — extracts all configured employers and writes the deliverables
py run.py
```

For a single, arbitrary employer (any company, any platform), use the universal crawler:

```powershell
py crawl.py https://careers.example.com --name "Example Corp" --industry "Manufacturing"
```

Outputs are written next to the script:

```
india_active_jobs_master.csv     # Deliverable 1 — consolidated dataset
coverage_report.csv              # Deliverable 2 — coverage summary (machine-readable)
coverage_report.md               # Deliverable 2 — coverage summary (human-readable)
run_log.json                     # full run metadata + per-company notes/errors
hiring_trends.{md,json}          # optional #5 — hiring trends
history/snapshots/snapshot_*.json  # optional #1–#3 — daily state for diffing
history/changes/changes_*.{json,md}# optional #1–#3 — new / modified / closed report
```

### Useful options

```powershell
py run.py --companies NVIDIA,Pfizer        # subset of employers
py run.py --limit 25 --no-cache            # quick smoke test
py run.py --rps 3 --workers 3              # gentler on the portals
py run.py --out ..\out --cache ..\.cache   # custom output / cache locations
py run.py --timeout 45 --retries 4         # transport tuning
py run.py --api --port 8000                # serve the query API + dashboard (no crawling)
```

| Flag | Default | Purpose |
|---|---|---|
| `--registry` | `config/companies.json` | Company registry path. |
| `--out` | project root | Output directory. |
| `--cache` | `.cache/http` | On-disk HTTP cache directory. |
| `--companies` | all | Comma-separated subset. |
| `--limit` | none | Cap records per employer (debug). |
| `--workers` | 4 | Concurrent detail fetches per employer. |
| `--rps` | 6.0 | Max HTTP requests/second. |
| `--no-cache` | off | Ignore the response cache. |
| `--timeout` | 30.0 | Per-request timeout (s). |
| `--retries` | 3 | Max retries per request. |
| `--api` | off | Serve the query API + dashboard over the existing master CSV, without crawling. |
| `--host` / `--port` | `127.0.0.1` / `8000` | API bind address. |

### Tests

```powershell
py -m unittest discover -s tests -v
```

Covers India detection (incl. negative cases like `"Indiana, US"`), location parsing, date
parsing, controlled vocabularies, salary no-fabrication, HTML cleaning, and all three
deduplication tiers — plus the universal layer (platform detection, `robots.txt`/sitemap
discovery including sitemap-index and malformed-XML, job-URL filtering, schema.org JSON-LD and
microdata extraction, the generic crawler's India filter/expiry handling, and the router) and the
advanced layer (job-family/seniority classification, snapshot diffing into new/modified/closed,
trend aggregation, and the dataset query/filter layer behind the API).
The suite is **154 tests** and runs offline with no dependencies.

### External services / credentials

**None.** No paid services, API keys, credentials or infrastructure are required. Only public,
unauthenticated employer endpoints are used, at a polite, rate-limited request pace.

---

## 12. Advanced capabilities (optional challenge)

Beyond the core acquisition and normalization requirements, **all six** optional capabilities
are implemented, and every one of them is produced by the same `py run.py` run — no extra
services, no third-party packages.

| # | Capability | Where it lives | Output |
|---|---|---|---|
| 1 | Daily refresh | `run.py` (idempotent, cached) | fresh `india_active_jobs_master.csv` + snapshot |
| 2 | Detect new / modified / closed | [`history.py`](src/jobintel/history.py) | `history/changes/changes_<date>.{json,md}` |
| 3 | Historical records of changes | [`history.py`](src/jobintel/history.py) | `history/snapshots/snapshot_<date>.json` |
| 4 | Job families & seniority | [`classify.py`](src/jobintel/classify.py) | `job_family` · `seniority_level` columns |
| 5 | Hiring trends | [`trends.py`](src/jobintel/trends.py) | `hiring_trends.{md,json}` |
| 6 | Query API / dashboard | [`api.py`](src/jobintel/api.py) | `py run.py --api` → <http://127.0.0.1:8000/> |

### 1–3 · Refresh, change detection & history

Each run writes a **snapshot** of the consolidated dataset keyed by a stable business key
(`<company-slug>:<job_id>`) plus a derived internal content hash. The next run loads
the most recent *earlier* snapshot, diffs it against the current output, and classifies every
posting as **new**, **modified** (same key, changed content hash), or **closed** (present in the
snapshot, absent now):

```powershell
py run.py                       # run 1 — writes a baseline snapshot, no diff yet
py run.py                       # run 2 — diffs against run 1 and writes a change report
```

Snapshots are plain JSON (no database) under `history/snapshots/`; change reports are written as
both JSON and Markdown under `history/changes/`. Schedule `py run.py` daily via Task Scheduler /
cron for an automated refresh; the on-disk HTTP cache keeps repeat runs cheap.

### 4 · Job-family & seniority classification

[`classify.py`](src/jobintel/classify.py) derives two controlled-vocabulary columns from the job
title and description — deterministically, with no model calls:

- **`job_family`** — one of 18 standardized families (e.g. `Engineering`, `Operations`,
  `IT & Infrastructure`, `Data & Analytics`, `Sales`, `Finance & Accounting`, `Human Resources`,
  `Marketing`, `Product`, `Design`, …), with an `Other` fallback. Populated for **every** row.
- **`seniority_level`** — `Entry` · `Mid` · `Senior` · `Lead` · `Manager` · `Director` ·
  `Executive` · `Internship`, inferred from title/description signals. Rows with no detectable
  signal are left blank in the CSV (shown as `Unknown` in the trend aggregation) rather than
  guessed.

### 5 · Hiring trends

[`trends.py`](src/jobintel/trends.py) aggregates the consolidated dataset into `hiring_trends.md`
(and machine-readable `hiring_trends.json`) with share-of-total breakdowns by **company**,
**industry**, **location** (state), **city**, **job family**, **seniority**, **work
arrangement**, **employment type** and **platform**, plus salary statistics over the rows that
publish a range.

### 6 · Lightweight query API & dashboard

[`api.py`](src/jobintel/api.py) serves the master CSV over a tiny stdlib HTTP server:

```powershell
py run.py --api --port 8000     # then open http://127.0.0.1:8000/
```

| Endpoint | Purpose |
|---|---|
| `GET /` | self-contained HTML dashboard (filter form + stat cards + results table). |
| `GET /api/health` | liveness + row count. |
| `GET /api/jobs` | free-text `q`, exact filters (`company`/`industry`/`state`/`city`/`family`/`seniority`/`arrangement`/`employment_type`/`status`/`platform`), `has_salary`, `sort`/`-field`, `offset`, `limit`. |
| `GET /api/jobs/<key>` | one posting by `job_id` or `<company_slug>:<job_id>`. |
| `GET /api/stats` · `GET /api/trends` | the aggregation from capability #5 as JSON. |

The server is read-only and binds to `127.0.0.1` by default.

---

## 13. Universal crawler for any company

The ten-employer pipeline answers the assignment. The **universal crawler** generalizes the
same machinery so the solution is not limited to a fixed list: give it *any* employer's
official careers URL — or many at once — and it classifies each platform, routes the request,
and emits schema-identical dataset(s).

```powershell
# one company
py crawl.py https://careers.example.com --name "Example Corp" --industry "Manufacturing"

# many companies -> one consolidated dataset (each employer isolated: a bad
# URL is reported as FAILED and does not abort the run)
py crawl.py https://jobs.lever.co/acme https://boards.greenhouse.io/beta https://careers.gamma.com

# or drive it from a JSON file (same format as config/companies.json)
py crawl.py --companies my_companies.json --out .\out
```

With a single URL the outputs are `<company>_jobs.csv` / `<company>_coverage.csv`; with
several they are `<dataset-name>_jobs.csv` / `<dataset-name>_coverage.csv` (default
`universal_*`). Both use the exact same 26-column schema as the main deliverable.

### How it decides — detector → router → crawler

| Layer | Module | What it does |
|---|---|---|
| 1. Detector | [`detect.py`](src/jobintel/detect.py) | Fingerprints the URL (and, if needed, the landing-page HTML) against 13 ATS signatures — `myworkdayjobs.com`, `jobs.lever.co`, `boards.greenhouse.io`, `jobs.smartrecruiters.com`, `amazon.jobs`, Ashby, Teamtailor, Recruitee, Workable, iCIMS, SuccessFactors, Zoho Recruit, Phenom. |
| 2. Router | [`build_for_url()`](src/jobintel/extractors/__init__.py) | Known platform → the matching **typed adapter** (fast, rich fields). Anything else → the **generic crawler**. Derives the employer name and adapter config straight from the URL. |
| 3. Generic crawler | [`sitemap.py`](src/jobintel/sitemap.py) · [`jsonld.py`](src/jobintel/jsonld.py) · [`generic.py`](src/jobintel/extractors/generic.py) | Discovers postings from the site's own `robots.txt` → `sitemap.xml` (including sitemap **indexes**), keeps job-like URLs, and reads each posting from its embedded **schema.org `JobPosting`** — JSON-LD first, then **microdata** (`itemprop=`, what SuccessFactors/Phenom portals publish), then `og:`/`<title>` metadata. |
| 4. Shared pipeline | `normalize` · `dedupe` · `schema.validate` · `reporting` | Unchanged — the generic crawler emits the same `JobPosting` objects, so output is column-for-column identical to the typed adapters. |

### Why sitemap + schema.org is the right universal contract

- **One cheap request gives the completeness denominator.** `sitemap.xml` frequently carries a
  per-URL `lastmod`, which both bounds coverage and enables incremental refresh — no listing
  page needs to be scraped.
- **schema.org is vendor-neutral.** Whether a site encodes it as JSON-LD or as HTML microdata,
  it is the one job-posting contract that ATSs and custom portals publish to the same
  specification, so a single extractor covers the long tail of platforms with no typed adapter.
- **Compliant by construction.** Only public `robots.txt` / `sitemap.xml` / job pages are
  requested; no authentication, CAPTCHA or access control is touched.

### Verified against a real SuccessFactors portal

`https://careers.sunpharma.com` is served by **SAP SuccessFactors** behind a branded domain, with
no public API (the OData endpoint is credential-gated) and **no JSON-LD**. The crawler discovers
266 job URLs from its sitemap and extracts **143 India postings at 100 % coverage, 0 errors**,
reading each posting from the page's schema.org **microdata**; titles, locations, descriptions,
posting dates and experience are populated from the source.

### Where "universal" ends

A site that renders **everything client-side** (a single-page app) *and* publishes neither a
sitemap nor schema.org data cannot be crawled without executing JavaScript. This
standard-library solution deliberately does not ship a headless browser; such a site is reported
as a `FAILED` result with an explanatory note rather than silently returning nothing.

### Options

| Flag | Purpose |
|---|---|
| `URL [URL ...]` | One or more employer careers URLs, crawled in sequence. |
| `--companies` | JSON file of employer specs (registry format or a bare list), merged with any positional URLs. |
| `--name` / `--industry` | Employer name / industry label (single-URL runs only). Name is derived from the URL if omitted; industry defaults to `Unspecified` (it is a required dataset column). |
| `--sitemap` | Override the sitemap entry point when the site does not advertise one. |
| `--dataset-name` | Output file stem for multi-company runs (default `universal`). |
| `--limit` | Cap records per company (quick smoke test). |
| `--out` / `--cache` | Output directory / HTTP cache directory. |
| `--workers` / `--rps` / `--timeout` / `--retries` / `--no-cache` | Same transport tuning as `run.py`. |

Outputs: `<slug>_jobs.csv` (identical 26-column schema) and `<slug>_coverage.csv`.

---

## 14. Adding a new company

Because the adapters are registry-driven, onboarding is usually a **config-only** change.

**Case A — the employer is already on a supported platform** (Workday / Amazon /
SmartRecruiters / Greenhouse / Lever): add one object to
[`config/companies.json`](config/companies.json) and re-run.

```json
{
  "company_name": "Acme Corp",
  "industry": "Manufacturing",
  "careers_url": "https://acme.wd1.myworkdayjobs.com/AcmeCareers",
  "extractor": "workday",
  "config": { "tenant": "acme", "wd": "wd1", "site": "AcmeCareers" }
}
```

No code change is required; the Workday adapter auto-discovers the India facet for the new tenant.

**Case B — the employer uses a *different, known* ATS:** let the router do the work. Run the
universal crawler — it detects the platform and, for the typed platforms above, builds the
same config automatically:

```powershell
py crawl.py https://acme.wd1.myworkdayjobs.com/AcmeCareers --name "Acme Corp" --industry "Manufacturing"
```

To fold a *new* platform into the master dataset instead, add one adapter class implementing
`BaseExtractor.extract()` that returns `ExtractionResult`, register it in
[`extractors/__init__.py`](src/jobintel/extractors/__init__.py) `REGISTRY`, and reference it from
the registry. The adapter only needs to produce `JobPosting` objects via
`self.base_posting(...)` plus `make_record_hash(...)`; normalization, dedupe, validation,
coverage accounting and reporting are inherited automatically. A working adapter is typically
80–130 lines (see [`amazon.py`](src/jobintel/extractors/amazon.py)).

**Case C — the ATS is unknown or proprietary:** no code is needed at all.

```powershell
py crawl.py https://careers.example.com --name "Example Corp"
```

The detector returns `unknown`, the router selects the generic crawler, and discovery +
extraction fall back to the site's `robots.txt`/`sitemap.xml` and embedded `schema.org`
`JobPosting` JSON-LD (see [§13](#13-universal-crawler-for-any-company)). The only hard limit is
a client-rendered SPA that publishes neither a sitemap nor JSON-LD.

---

## 15. Project layout

```
Crawl/
├── config/
│   └── companies.json              # company registry (edit to onboard employers)
├── src/jobintel/
│   ├── schema.py                   # JobPosting model, CSV_FIELDS, validate()
│   ├── normalize.py                # HTML/date/location/vocab/salary normalization
│   ├── http_client.py              # retries, backoff, rate limit, cache, concurrency
│   ├── dedupe.py                   # 3-tier duplicate removal
│   ├── pipeline.py                 # orchestration
│   ├── reporting.py                # CSV / Markdown / run-log writers
│   ├── detect.py                   # universal crawler: ATS fingerprinting + routing config
│   ├── sitemap.py                  # universal crawler: robots.txt/sitemap discovery
│   ├── jsonld.py                   # universal crawler: schema.org JobPosting (JSON-LD + microdata)
│   └── extractors/
│       ├── base.py                 # BaseExtractor + ExtractionResult contract
│       ├── __init__.py             # REGISTRY + build_extractor() + build_for_url()
│       ├── generic.py              # universal fallback adapter (sitemap + schema.org)
│       ├── workday.py  amazon.py  smartrecruiters.py
│       └── greenhouse.py  lever.py
├── tests/
│   ├── test_normalize.py           # normalization unit tests
│   ├── test_dedupe.py              # dedupe + validation unit tests
│   ├── test_detect.py              # platform-detection unit tests
│   ├── test_sitemap.py             # robots.txt/sitemap discovery unit tests
│   ├── test_jsonld.py              # JSON-LD + meta-fallback unit tests
│   └── test_generic_crawler.py     # generic adapter + router unit tests
├── run.py                          # CLI entry point (10-company pipeline)
├── crawl.py                        # CLI entry point (universal: any company)
├── requirements.txt                # documents the (empty) dependency set
├── india_active_jobs_master.csv    # Deliverable 1
├── coverage_report.{csv,md}        # Deliverable 2
└── run_log.json                    # run metadata
```

---

## 16. Deliverable map

| Assignment deliverable | Where |
|---|---|
| 1. Consolidated job dataset | [`india_active_jobs_master.csv`](india_active_jobs_master.csv) |
| 2. Company coverage summary | [`coverage_report.md`](coverage_report.md) / [`coverage_report.csv`](coverage_report.csv) |
| 3. Working technical solution | [`run.py`](run.py), [`src/jobintel/`](src/jobintel), [`config/companies.json`](config/companies.json), [`tests/`](tests) |
| 4. Technical documentation | This README |
| 5. Brief executive summary | [`EXECUTIVE_SUMMARY.md`](EXECUTIVE_SUMMARY.md) |
