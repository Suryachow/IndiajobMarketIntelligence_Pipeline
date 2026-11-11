"""A small, dependency-free HTTP client used by every extractor.

Provides the reliability primitives the assignment asks for:

* bounded retries with exponential backoff + jitter (handles transient 5xx/429
  and network blips);
* a global client-side rate limiter so we stay within "reasonable request
  limits" and never hammer an employer's portal;
* an on-disk response cache so repeated runs are fast, reproducible and cheap;
* a tiny thread-pool helper for the (I/O bound) per-job detail fetches.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class HttpError(Exception):
    """Raised when a request fails after all retries."""

    def __init__(self, url: str, status: Optional[int], message: str):
        self.url = url
        self.status = status
        super().__init__(f"{status or 'ERR'} {url} :: {message}")


class RateLimiter:
    """Simple thread-safe minimum-interval limiter."""

    def __init__(self, requests_per_second: float):
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
            self._last = time.monotonic()


class HttpClient:
    def __init__(
        self,
        cache_dir: Optional[str] = None,
        requests_per_second: float = 6.0,
        timeout: float = 30.0,
        max_retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
        use_cache: bool = True,
    ):
        self.timeout = timeout
        self.max_retries = max_retries
        self.user_agent = user_agent
        self.use_cache = use_cache and bool(cache_dir)
        self.cache_dir = cache_dir
        if self.cache_dir:
            os.makedirs(self.cache_dir, exist_ok=True)
        self._limiter = RateLimiter(requests_per_second)
        self._stats = {"requests": 0, "cache_hits": 0, "retries": 0, "errors": 0}
        self._stats_lock = threading.Lock()

    # -- public API ---------------------------------------------------------
    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        return self._request_json("GET", url, None, headers)

    def post_json(
        self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None
    ) -> Any:
        return self._request_json("POST", url, payload, headers)

    def post_multipart_json(
        self,
        url: str,
        fields: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """POST ``fields`` as ``multipart/form-data`` and parse a JSON reply.

        Some public ATS search endpoints (e.g. Accenture's Elastic-backed
        ``findjobs``) only accept a multipart body rather than JSON.
        """
        body, content_type = encode_multipart(fields)
        merged = {"Content-Type": content_type}
        merged.update(headers or {})
        raw, _ = self._request("POST", url, None, merged, data=body, cache_key=dict(fields))
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(url, None, f"invalid JSON response: {exc}") from exc

    def get_text(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        body, _ = self._request("GET", url, None, headers)
        return body.decode("utf-8", errors="replace")

    def get_page(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        """Fetch an HTML page's source.

        Identical to :meth:`get_text` here; rendered clients (see
        :mod:`jobintel.render`) override it to return the DOM a headless browser
        produces. Keeping it separate leaves ``robots.txt``/``sitemap.xml`` and
        JSON reads on the fast raw-HTTP path.
        """
        return self.get_text(url, headers)

    def close(self) -> None:
        """Release underlying resources. The default client holds none."""

    @property
    def stats(self) -> Dict[str, int]:
        with self._stats_lock:
            return dict(self._stats)

    def map_concurrent(
        self, items: Iterable[Any], fn: Callable[[Any], Any], workers: int = 4
    ) -> List[Any]:
        """Run ``fn`` over ``items`` with a bounded thread pool, preserving order.

        Failures are captured as ``None`` so one bad job never aborts a company.
        """
        items = list(items)
        if not items:
            return []
        results: List[Any] = [None] * len(items)
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(fn, item): idx for idx, item in enumerate(items)}
            for future, idx in futures.items():
                try:
                    results[idx] = future.result()
                except Exception:  # noqa: BLE001 - isolation is the point
                    results[idx] = None
        return results

    # -- internals ----------------------------------------------------------
    def _request_json(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
    ) -> Any:
        body, _ = self._request(method, url, payload, headers)
        try:
            return json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(url, None, f"invalid JSON response: {exc}") from exc

    def _cache_path(self, method: str, url: str, payload: Optional[Dict[str, Any]]) -> str:
        key = method + "\n" + url + "\n" + json.dumps(payload or {}, sort_keys=True, default=str)
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        return os.path.join(self.cache_dir or "", digest + ".json")

    def _request(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
        data: Optional[bytes] = None,
        cache_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bytes, bool]:
        """Return ``(body, from_cache)``.

        ``payload`` is sent as JSON; pass a pre-encoded ``data`` body instead for
        non-JSON content types (e.g. multipart). ``cache_key`` overrides what the
        on-disk cache is keyed on when ``data`` carries the request.
        """
        key_source = cache_key if cache_key is not None else payload
        cache_path = self._cache_path(method, url, key_source) if self.use_cache else None
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, "rb") as fh:
                    cached = fh.read()
                self._bump("cache_hits")
                cached_body = cached.split(b"\n", 1)[1]
                return cached_body, True
            except OSError:
                pass

        merged = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            merged.update(headers)
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self._limiter.wait()
            req = urllib.request.Request(url, data=data, headers=merged, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                self._bump("requests")
                if cache_path:
                    self._write_cache(cache_path, body)
                return body, False
            except urllib.error.HTTPError as exc:
                self._bump("requests")
                last_error = exc
                if exc.code in RETRY_STATUS and attempt < self.max_retries:
                    self._bump("retries")
                    time.sleep(_backoff(attempt))
                    continue
                self._bump("errors")
                raise HttpError(url, exc.code, _short(exc.read())) from exc
            except Exception as exc:  # noqa: BLE001 - network/SSL/timeout
                last_error = exc
                if attempt < self.max_retries:
                    self._bump("retries")
                    time.sleep(_backoff(attempt))
                    continue
                self._bump("errors")
                raise HttpError(url, None, str(exc)) from exc
        raise HttpError(url, None, f"exhausted retries: {last_error}")

    def _write_cache(self, path: str, body: bytes) -> None:
        try:
            tmp = path + ".tmp"
            with open(tmp, "wb") as fh:
                fh.write(b"\n" + body)
            os.replace(tmp, path)
        except OSError:
            pass

    def _bump(self, key: str, amount: int = 1) -> None:
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + amount


def _backoff(attempt: int) -> float:
    return min(20.0, (2 ** attempt) * 0.5) + random.uniform(0, 0.35)


def encode_multipart(fields: Dict[str, Any]) -> Tuple[bytes, str]:
    """Encode ``fields`` as a ``multipart/form-data`` body.

    Returns ``(body, content_type)`` where ``content_type`` includes the chosen
    boundary.
    """
    boundary = "----JobIntelBoundary" + uuid.uuid4().hex
    chunks: List[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8")
        )
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _short(data: bytes, limit: int = 200) -> str:
    try:
        return data.decode("utf-8", errors="replace")[:limit]
    except Exception:  # noqa: BLE001
        return ""
