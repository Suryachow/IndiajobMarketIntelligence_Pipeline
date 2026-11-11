"""Lightweight query API + dashboard over the consolidated dataset.

A zero-dependency HTTP service (Python standard library only) that exposes the
master dataset for ad-hoc querying — the optional "lightweight API or dashboard"
capability. The dataset is loaded once into memory, so queries are served
without touching the network.

Endpoints
---------
``GET /``               self-contained HTML dashboard (filters + live query)
``GET /api/stats``      summary counts for the whole dataset
``GET /api/jobs``       filtered/sorted/paged postings (JSON)
``GET /api/jobs/<key>`` a single posting by ``job_id`` (or ``company:job_id``)
``GET /api/trends``     the hiring-trend summary computed on demand
``GET /api/health``     liveness probe

Run it after a crawl, over the file the pipeline just wrote::

    py run.py --api            # serves ./india_active_jobs_master.csv
"""

from __future__ import annotations

import csv
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote, urlparse

from .normalize import slugify
from .schema import CSV_FIELDS, has_salary
from .trends import compute_trends

# Free-text search covers these fields.
_SEARCH_FIELDS = (
    "job_title",
    "company_name",
    "job_location",
    "job_family",
    "job_category",
    "job_description",
)

# Exact-match filters: query-parameter name -> dataset column.
_FILTERS = {
    "company": "company_name",
    "industry": "industry",
    "location": "job_location",
    "state": "state",
    "city": "city",
    "family": "job_family",
    "seniority": "seniority_level",
    "arrangement": "work_arrangement",
    "employment_type": "employment_type",
    "status": "job_status",
    "platform": "source_platform",
}

_SORTABLE = set(CSV_FIELDS)
_OPTION_FIELDS = {
    "company": "company_name",
    "location": "job_location",
    "state": "state",
    "industry": "industry",
}


class Dataset:
    """In-memory view over ``india_active_jobs_master.csv``."""

    def __init__(self, jobs: List[Dict[str, str]]):
        self.jobs = jobs

    @classmethod
    def load(cls, csv_path: str) -> "Dataset":
        with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            jobs = [{(k or ""): (v or "") for k, v in row.items()} for row in reader]
        return cls(jobs)

    # -- querying -----------------------------------------------------------
    def query(self, params: Dict[str, List[str]]) -> Tuple[List[Dict[str, str]], int]:
        rows = self.jobs

        query = _first(params, "q").strip().lower()
        if query:
            rows = [r for r in rows if any(query in (r.get(f, "") or "").lower() for f in _SEARCH_FIELDS)]

        for param, column in _FILTERS.items():
            wanted = _first(params, param).strip().lower()
            if wanted:
                rows = [r for r in rows if wanted == (r.get(column, "") or "").strip().lower()]

        has_salary_param = _first(params, "has_salary").strip().lower()
        if has_salary_param in {"1", "true", "yes"}:
            rows = [
                r for r in rows
                if has_salary(r.get("min_salary", ""), r.get("max_salary", ""))
            ]

        sort = _first(params, "sort").strip()
        if sort and sort.lstrip("-") in _SORTABLE:
            field = sort.lstrip("-")
            rows = sorted(rows, key=lambda r: (r.get(field, "") or "").lower(), reverse=sort.startswith("-"))

        total = len(rows)
        offset = _int(params, "offset", 0)
        limit = _int(params, "limit", 50)
        if offset:
            rows = rows[offset:]
        if limit and limit > 0:
            rows = rows[:limit]
        return rows, total

    def find(self, key: str) -> Optional[Dict[str, str]]:
        key = unquote(key).strip().lower()
        for row in self.jobs:
            if (row.get("job_id", "") or "").strip().lower() == key:
                return row
        for row in self.jobs:  # "<company_slug>:<job_id>" form
            company_slug = slugify(row.get("company_name", ""))
            if company_slug + ":" + row.get("job_id", "").lower() == key:
                return row
        return None

    def stats(self) -> Dict[str, Any]:
        return compute_trends(_rows_as_objects(self.jobs))

    def options(self) -> Dict[str, List[str]]:
        return {
            name: sorted(
                {row.get(column, "").strip() for row in self.jobs if row.get(column, "").strip()},
                key=str.casefold,
            )
            for name, column in _OPTION_FIELDS.items()
        }


def _first(params: Dict[str, List[str]], name: str) -> str:
    values = params.get(name) or []
    return values[0] if values else ""


def _int(params: Dict[str, List[str]], name: str, default: int) -> int:
    try:
        return int(_first(params, name))
    except (TypeError, ValueError):
        return default


class _Object:
    """Minimal attribute bag so trend code can run over raw CSV rows."""

    def __init__(self, row: Dict[str, str]):
        for field in CSV_FIELDS:
            setattr(self, field, row.get(field, ""))


def _rows_as_objects(rows: List[Dict[str, str]]) -> List["_Object"]:
    return [_Object(r) for r in rows]


def make_handler(dataset: Dataset):
    """Build a request handler bound to ``dataset``."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "JobIntelAPI/1.0"

        def log_message(self, fmt, *args):  # quieter default logging
            return

        def do_GET(self):  # noqa: N802 - stdlib naming
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            path = parsed.path.rstrip("/") or "/"

            if path == "/":
                self._send_html(DASHBOARD_HTML)
            elif path == "/api/health":
                self._send_json({"status": "ok", "postings": len(dataset.jobs)})
            elif path == "/api/stats":
                self._send_json(dataset.stats())
            elif path == "/api/options":
                self._send_json(dataset.options())
            elif path == "/api/trends":
                self._send_json(dataset.stats())
            elif path == "/api/jobs":
                rows, total = dataset.query(params)
                self._send_json(
                    {
                        "total": total,
                        "count": len(rows),
                        "offset": _int(params, "offset", 0),
                        "limit": _int(params, "limit", 50),
                        "jobs": rows,
                    }
                )
            elif path.startswith("/api/jobs/"):
                row = dataset.find(path[len("/api/jobs/"):])
                if row is None:
                    self._send_json({"error": "not found"}, status=404)
                else:
                    self._send_json(row)
            else:
                self._send_json({"error": "not found"}, status=404)

        # -- helpers --------------------------------------------------------
        def _send_json(self, payload: Any, status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_html(self, document: str) -> None:
            body = document.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def serve(csv_path: str, host: str = "127.0.0.1", port: int = 8000, log=print) -> None:
    """Load ``csv_path`` and serve the API/dashboard until interrupted."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"dataset not found: {csv_path}")
    dataset = Dataset.load(csv_path)
    server = ThreadingHTTPServer((host, port), make_handler(dataset))
    log(f"Job API serving {len(dataset.jobs)} postings from {csv_path}")
    log(f"  dashboard : http://{host}:{port}/")
    log(f"  jobs API  : http://{host}:{port}/api/jobs?company=NVIDIA&limit=5")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("\nstopping API server")
    finally:
        server.server_close()


DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>India Job Market Intelligence</title>
<style>
body{font:15px system-ui,sans-serif;max-width:1200px;margin:2rem auto;padding:0 1rem;color:#111827}
h1{margin-bottom:.2rem}.muted{color:#6b7280}
form{display:flex;flex-wrap:wrap;gap:.5rem;margin:1rem 0}
input,select,button{padding:.5rem;border:1px solid #d1d5db;border-radius:6px;font:inherit}
button{background:#075985;color:#fff;border-color:#075985;cursor:pointer}
button.secondary{background:#fff;color:#075985}
button:disabled{opacity:.5;cursor:not-allowed}
details.options{position:relative}
details.options summary{padding:.5rem;border:1px solid #d1d5db;border-radius:6px;cursor:pointer;list-style:none;background:#fff}
details.options summary::-webkit-details-marker{display:none}
details.options summary:after{content:"▾";margin-left:.5rem}
details.options[open] summary:after{content:"▴"}
.options-panel{position:absolute;z-index:5;top:calc(100% + .35rem);right:0;display:grid;grid-template-columns:repeat(2,minmax(170px,1fr));gap:.5rem;padding:.75rem;background:#fff;border:1px solid #d1d5db;border-radius:8px;box-shadow:0 6px 18px #0002;min-width:380px}
@media(max-width:600px){.options-panel{position:static;grid-template-columns:1fr;min-width:0}}
table{border-collapse:collapse;width:100%;margin-top:1rem}
th,td{border:1px solid #e5e7eb;padding:.5rem;text-align:left;vertical-align:top}
th{background:#f3f4f6;position:sticky;top:0}
th button{background:transparent;color:#111827;border:0;padding:0;font-weight:700}
th button:hover,a:hover{text-decoration:underline}
.cards{display:flex;gap:1rem;flex-wrap:wrap;margin-top:1rem}
.card{border:1px solid #e5e7eb;border-radius:8px;padding:.75rem 1rem;min-width:150px}
.card b{display:block;font-size:1.4rem}
h2{margin:1.5rem 0 .25rem}
.toolbar{display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.pager{display:flex;align-items:center;gap:.5rem}
.pager button{padding:.35rem .7rem}
.modal-backdrop{display:none;position:fixed;inset:0;background:#111827aa;z-index:10;padding:5vh 1rem}
.modal-backdrop.open{display:block}
.modal{background:#fff;max-width:760px;max-height:90vh;overflow:auto;margin:auto;border-radius:10px;padding:1.25rem;box-shadow:0 10px 30px #0005}
.modal-header{display:flex;justify-content:space-between;align-items:start;gap:1rem}
.modal-close{background:transparent;color:#111827;border:0;font-size:1.5rem;padding:0}
.detail-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:.75rem;margin:1rem 0}
.detail-grid dt{font-size:.8rem;color:#6b7280}.detail-grid dd{margin:.1rem 0 0}
.description{white-space:pre-wrap;line-height:1.5}
.rank{display:flex;flex-wrap:wrap;gap:.5rem;padding:.75rem;border:1px dashed #cbd5e1;border-radius:8px;min-height:58px}
.rank-card{display:flex;align-items:center;gap:.5rem;border:1px solid #e5e7eb;border-left:4px solid #075985;border-radius:8px;padding:.4rem .75rem;background:#fff;cursor:grab;user-select:none}
.rank-card.dragging{opacity:.4}
.rank-card.over{outline:2px solid #075985;outline-offset:2px}
.rank-badge{font-weight:700;color:#075985;min-width:1.4rem}
.rank-card .n{color:#6b7280;font-size:.85rem}
a{color:#075985}
</style></head><body>
<h1>India Job Market Intelligence</h1>
<p class="muted">Query the consolidated employer dataset. Data from the employer's own career portals.</p>
<form id="filters">
  <input name="q" placeholder="search title, company, description…" style="flex:1;min-width:240px">
  <select name="company" data-csv-options="company"><option value="">any company</option></select>
  <select name="location" data-csv-options="location"><option value="">any location</option></select>
  <input name="family" placeholder="job family">
  <details class="options">
    <summary>Options</summary>
    <div class="options-panel">
      <select name="state" data-csv-options="state"><option value="">any state</option></select>
      <input name="seniority" placeholder="seniority">
      <select name="industry" data-csv-options="industry"><option value="">any industry</option></select>
      <select name="employment_type"><option value="">any employment type</option>
        <option>Full-time</option><option>Part-time</option><option>Contract</option><option>Internship</option></select>
      <select name="arrangement"><option value="">any arrangement</option>
        <option>Onsite</option><option>Hybrid</option><option>Remote</option></select>
      <select name="status"><option value="">any status</option><option>Active</option><option>Closed</option></select>
      <select name="has_salary"><option value="">salary: any</option>
        <option value="1">publishes salary</option></select>
    </div>
  </details>
  <select name="limit" title="Results per page"><option value="25">25 per page</option>
    <option value="50" selected>50 per page</option><option value="100">100 per page</option></select>
  <button type="submit">Search</button>
  <button type="button" class="secondary" id="clear">Clear</button>
</form>
<div class="cards" id="stats"></div>
<div class="toolbar"><p class="muted" id="count"></p><div class="pager">
  <button type="button" class="secondary" id="previous">Previous</button>
  <span id="page"></span>
  <button type="button" class="secondary" id="next">Next</button>
</div></div>
<table id="results"><thead><tr>
  <th><button type="button" data-sort="job_title">Job Title</button></th>
  <th><button type="button" data-sort="company_name">Company</button></th>
  <th><button type="button" data-sort="job_location">Location</button></th>
  <th><button type="button" data-sort="job_family">Family</button></th>
  <th><button type="button" data-sort="seniority_level">Seniority</button></th>
  <th><button type="button" data-sort="work_arrangement">Arrangement</button></th>
  <th>Salary</th></tr></thead><tbody></tbody></table>
<div class="modal-backdrop" id="job-modal" role="dialog" aria-modal="true" aria-labelledby="modal-title">
  <article class="modal"><div class="modal-header"><h2 id="modal-title">Job details</h2>
    <button type="button" class="modal-close" aria-label="Close">&times;</button></div>
    <p class="muted" id="modal-status"></p><dl class="detail-grid" id="modal-fields"></dl>
    <h3>Description</h3><div class="description" id="modal-description"></div>
    <p><a id="modal-link" target="_blank" rel="noopener">Open original posting</a></p>
  </article>
</div>
<script>
const form = document.getElementById('filters');
const body = document.querySelector('#results tbody');
const modal = document.getElementById('job-modal');
let offset = 0;
let sort = '';
const esc = (v) => (v ?? '').toString().replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function loadOptions() {
  const res = await fetch('/api/options');
  if (!res.ok) throw new Error(`Could not load filter options (${res.status})`);
  const options = await res.json();
  document.querySelectorAll('[data-csv-options]').forEach(select => {
    const values = options[select.dataset.csvOptions] || [];
    select.innerHTML = `<option value="">${esc(select.options[0]?.textContent || 'Any')}</option>` +
      values.map(value => `<option value="${esc(value)}">${esc(value)}</option>`).join('');
  });
}

async function load() {
  const params = new URLSearchParams(new FormData(form));
  for (const [k, v] of [...params]) { if (!v) params.delete(k); }
  params.set('offset', offset);
  if (sort) params.set('sort', sort);
  const res = await fetch('/api/jobs?' + params.toString());
  if (!res.ok) throw new Error(`Search failed (${res.status})`);
  const data = await res.json();
  const start = data.total ? offset + 1 : 0;
  const end = offset + data.count;
  document.getElementById('count').textContent = `Showing ${start}-${end} of ${data.total} matching postings`;
  document.getElementById('page').textContent =
    `Page ${data.total ? Math.floor(offset / data.limit) + 1 : 0} of ${data.total ? Math.ceil(data.total / data.limit) : 0}`;
  document.getElementById('previous').disabled = offset === 0;
  document.getElementById('next').disabled = end >= data.total;
  body.innerHTML = (data.jobs || []).map(j => {
    const notPub = (v) => !v || /^not published$/i.test(String(v).trim());
    const salary = (notPub(j.min_salary) && notPub(j.max_salary))
      ? 'Not published'
      : `${j.min_salary}${j.max_salary && j.max_salary !== j.min_salary ? ' - ' + j.max_salary : ''} ${j.currency || ''}`.trim();
    const key = encodeURIComponent(`${j.company_name}:${j.job_id}`);
    return `<tr><td><a href="#" class="job-details" data-key="${key}">${esc(j.job_title)}</a></td>
      <td>${esc(j.company_name)}</td><td>${esc(j.job_location)}</td><td>${esc(j.job_family)}</td>
      <td>${esc(j.seniority_level)}</td><td>${esc(j.work_arrangement)}</td><td>${esc(salary)}</td></tr>`;
  }).join('');
}

async function showDetails(key) {
  modal.classList.add('open');
  document.getElementById('modal-status').textContent = 'Loading…';
  document.getElementById('modal-fields').innerHTML = '';
  document.getElementById('modal-description').textContent = '';
  try {
    const res = await fetch('/api/jobs/' + key);
    if (!res.ok) throw new Error(`Could not load job (${res.status})`);
    const j = await res.json();
    document.getElementById('modal-title').textContent = j.job_title || 'Job details';
    document.getElementById('modal-status').textContent = `${j.company_name || ''} · ${j.job_location || ''}`;
    const fields = [['Industry', j.industry], ['Employment', j.employment_type],
      ['Arrangement', j.work_arrangement], ['Experience', j.experience_required],
      ['Posting date', j.posting_date], ['Status', j.job_status]];
    document.getElementById('modal-fields').innerHTML = fields
      .map(([label, value]) => `<div><dt>${esc(label)}</dt><dd>${esc(value || 'Not specified')}</dd></div>`).join('');
    document.getElementById('modal-description').textContent = j.job_description || 'No description available.';
    const link = document.getElementById('modal-link');
    link.href = j.job_url || '#';
    link.hidden = !j.job_url;
    document.getElementById('modal-status').textContent = `${j.company_name || ''} · ${j.job_location || ''}`;
  } catch (error) {
    document.getElementById('modal-status').textContent = error.message;
  }
}

async function loadStats() {
  const res = await fetch('/api/stats');
  if (!res.ok) throw new Error(`Could not load stats (${res.status})`);
  const s = await res.json();
  const cards = [
    ['Postings', s.total_postings],
    ['Publish salary', s.with_salary],
    ['Companies', (s.by_company?.rows || []).length],
    ['Job families', (s.by_job_family?.rows || []).length],
    ['States', (s.by_location?.rows || []).length],
  ];
  document.getElementById('stats').innerHTML = cards
    .map(([label, value]) => `<div class="card"><b>${value ?? 0}</b>${label}</div>`).join('');
}

form.addEventListener('submit', e => { e.preventDefault(); offset = 0; load().catch(e => document.getElementById('count').textContent = e.message); });
document.getElementById('clear').addEventListener('click', () => { form.reset(); offset = 0; load(); });
document.getElementById('previous').addEventListener('click', () => { offset = Math.max(0, offset - Number(form.limit.value)); load(); });
document.getElementById('next').addEventListener('click', () => { offset += Number(form.limit.value); load(); });
document.querySelectorAll('[data-sort]').forEach(button => button.addEventListener('click', () => {
  const field = button.dataset.sort;
  sort = sort === field ? `-${field}` : field;
  offset = 0;
  load();
}));
body.addEventListener('click', e => {
  const link = e.target.closest('.job-details');
  if (link) { e.preventDefault(); showDetails(link.dataset.key); }
});
document.querySelector('.modal-close').addEventListener('click', () => modal.classList.remove('open'));
modal.addEventListener('click', e => { if (e.target === modal) modal.classList.remove('open'); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') modal.classList.remove('open'); });
loadOptions().catch(e => document.getElementById('count').textContent = e.message);
load().catch(e => document.getElementById('count').textContent = e.message);
loadStats().catch(e => document.getElementById('stats').textContent = e.message);
</script></body></html>
"""
