"""Optional headless-browser support for client-rendered careers sites.

The crawler core is standard-library only. Two capabilities are provided here,
both *optional* and both degrading gracefully when Playwright is unavailable:

* :class:`HeadlessSession` - a disposable browser session used by adapters that
  must talk to a public JSON API gated by a session cookie / XSRF token (the TCS
  iBegin adapter is the reference case).
* :class:`RenderingClient` - an :class:`~jobintel.http_client.HttpClient`
  drop-in that renders HTML pages in a real browser instead of fetching raw
  bytes. Because every adapter already speaks to a "client", enabling it is a
  one-flag change that gives the *universal* crawler JavaScript/SPA support
  without touching adapter code. JSON and ``robots.txt``/``sitemap.xml`` reads
  stay on the standard library, so only real pages pay the rendering cost.

One-off setup::

    pip install playwright
    python -m playwright install chromium

This module only drives public pages and their public APIs, keeping whatever
session cookie the site itself issues. It does not defeat CAPTCHAs, WAFs or
authentication.
"""

from __future__ import annotations

import importlib.util
import threading
import time
from typing import Any, Dict, List, Optional

from .http_client import HttpClient

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Executed inside the page so the browser supplies its own headers, cookies and
# CORS handling - which is what a request from a plain HTTP client lacks.
_JSON_FETCH_JS = """
async ([method, url, headers, body]) => {
    try {
        const opts = {method: method, headers: Object.assign({}, headers || {})};
        if (body !== null && body !== undefined) {
            opts.body = JSON.stringify(body);
            const hasType = Object.keys(opts.headers).some(h => h.toLowerCase() === 'content-type');
            if (!hasType) { opts.headers['Content-Type'] = 'application/json'; }
        }
        const r = await fetch(url, opts);
        if (!r.ok) { return {__http_status__: r.status}; }
        return {__json__: await r.json()};
    } catch (e) {
        return {__error__: String(e)};
    }
}
"""

# Concurrent variant: fetch many same-origin endpoints at once, preserving order.
_BATCH_FETCH_JS = """
async ([urls, headers]) => {
    const one = async (u) => {
        try {
            const r = await fetch(u, {headers: headers || {}});
            if (!r.ok) { return null; }
            return await r.json();
        } catch (e) { return null; }
    };
    return await Promise.all(urls.map(one));
}
"""


class RenderError(Exception):
    """Raised when a headless session or an in-session request fails."""


def available() -> bool:
    """True when the optional Playwright runtime can be imported.

    Uses a cheap module lookup so the standard-library core never pays the
    import cost unless a site actually needs the renderer.
    """
    try:
        return importlib.util.find_spec("playwright") is not None
    except Exception:  # noqa: BLE001 - any import machinery failure means "no"
        return False


class HeadlessSession:
    """A disposable Chromium session whose requests share the page's cookies.

    Typical use::

        with HeadlessSession() as session:
            session.goto(LANDING)                   # establish session cookies
            token = session.cookie("XSRF-TOKEN")    # read a cookie the SPA set
            data = session.post_json(API, body, headers={"next-xsrf-token": token})
    """

    def __init__(
        self,
        headless: bool = True,
        timeout_ms: int = 60000,
        user_agent: str = DEFAULT_USER_AGENT,
        locale: str = "en-IN",
        min_interval: float = 0.15,
    ):
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.user_agent = user_agent
        self.locale = locale
        self.min_interval = max(0.0, min_interval)
        self._last_request = 0.0
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "HeadlessSession":
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:  # noqa: BLE001 - optional dependency
            raise RenderError(
                "Playwright is not installed; run 'pip install playwright' and "
                "'python -m playwright install chromium'"
            ) from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(
                user_agent=self.user_agent, locale=self.locale
            )
            self._page = self._context.new_page()
            self._page.set_default_timeout(self.timeout_ms)
        except Exception as exc:  # noqa: BLE001 - launch/binary problems
            self.close()
            raise RenderError(f"could not start headless browser: {exc}") from exc
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:  # noqa: BLE001 - best-effort teardown
                pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._context = self._browser = self._pw = None

    # -- navigation / session state ----------------------------------------
    def goto(self, url: str, wait_until: str = "networkidle") -> str:
        """Load ``url`` and return the resolved URL once the page settles."""
        self._require_open()
        try:
            self._page.goto(url, wait_until=wait_until, timeout=self.timeout_ms)
        except Exception:  # noqa: BLE001 - SPAs often never reach networkidle
            try:
                self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            except Exception as exc:  # noqa: BLE001
                raise RenderError(f"navigation failed for {url}: {exc}") from exc
        return self._page.url

    def html(self) -> str:
        """Return the current page's fully-rendered DOM as HTML."""
        self._require_open()
        try:
            return self._page.content()
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"could not read rendered HTML: {exc}") from exc

    def cookie(self, name: str) -> str:
        """Return the value of cookie ``name`` (``''`` when absent)."""
        self._require_open()
        for item in self._context.cookies():
            if item.get("name") == name:
                return item.get("value") or ""
        return ""

    # -- HTTP, carrying the browser session's cookies ----------------------
    def get_json(self, url: str, headers: Optional[Dict[str, str]] = None) -> Any:
        return self._json("GET", url, None, headers)

    def post_json(
        self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None
    ) -> Any:
        return self._json("POST", url, payload, headers)

    def get_json_many(
        self,
        urls: List[str],
        headers: Optional[Dict[str, str]] = None,
        chunk_size: int = 8,
    ) -> List[Any]:
        """Fetch several same-origin endpoints concurrently, preserving order.

        A failed entry is returned as ``None`` so one bad URL cannot abort the
        batch. Concurrency is bounded by ``chunk_size`` to stay courteous.
        """
        self._require_open()
        results: List[Any] = []
        step = max(1, chunk_size)
        for start in range(0, len(urls), step):
            batch = urls[start : start + step]
            self._throttle()
            try:
                batch_results = self._page.evaluate(_BATCH_FETCH_JS, [batch, headers or {}])
            except Exception as exc:  # noqa: BLE001
                raise RenderError(f"batch fetch failed: {exc}") from exc
            results.extend(batch_results if isinstance(batch_results, list) else [None] * len(batch))
        return results

    # -- internals ---------------------------------------------------------
    def _json(
        self,
        method: str,
        url: str,
        payload: Optional[Dict[str, Any]],
        headers: Optional[Dict[str, str]],
    ) -> Any:
        self._require_open()
        self._throttle()
        try:
            outcome = self._page.evaluate(_JSON_FETCH_JS, [method, url, headers or {}, payload])
        except Exception as exc:  # noqa: BLE001
            raise RenderError(f"request failed for {url}: {exc}") from exc
        if not isinstance(outcome, dict):
            raise RenderError(f"unexpected response for {url}")
        if "__json__" in outcome:
            return outcome["__json__"]
        if "__http_status__" in outcome:
            raise RenderError(f"HTTP {outcome['__http_status__']} for {url}")
        raise RenderError(f"request failed for {url}: {outcome.get('__error__', 'unknown error')}")

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        delta = time.monotonic() - self._last_request
        if delta < self.min_interval:
            time.sleep(self.min_interval - delta)
        self._last_request = time.monotonic()

    def _require_open(self) -> None:
        if self._page is None or self._context is None:
            raise RenderError("headless session is not open")


class RenderingClient(HttpClient):
    """An :class:`HttpClient` that renders HTML pages with a headless browser.

    Only :meth:`get_page` is overridden - JSON/API calls and
    ``robots.txt``/``sitemap.xml`` keep using the fast standard-library path.
    Every adapter therefore gains SPA support for free; if the browser cannot be
    started the client transparently falls back to raw HTTP.

    A single browser is started lazily and reused for the whole run. Calls are
    serialized with a lock because Playwright's synchronous API is not
    thread-safe.
    """

    def __init__(self, *args: Any, render: bool = True, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.render = render
        self._session: Optional[HeadlessSession] = None
        self._lock = threading.Lock()
        self._render_disabled = False

    # -- overrides ---------------------------------------------------------
    def get_page(self, url: str, headers: Optional[Dict[str, str]] = None) -> str:
        """Return the *rendered* HTML for ``url`` (raw HTTP when unavailable)."""
        if not self.render or self._render_disabled or not available():
            return self.get_text(url, headers)
        with self._lock:
            session = self._session_for_use()
            if session is None:
                return self.get_text(url, headers)
            self._limiter.wait()
            try:
                session.goto(url)
                return session.html()
            except RenderError:
                return self.get_text(url, headers)

    def close(self) -> None:
        with self._lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    # -- internals ---------------------------------------------------------
    def _session_for_use(self) -> Optional[HeadlessSession]:
        """Open the shared browser once; disable rendering if that fails."""
        if self._session is not None:
            return self._session
        try:
            self._session = HeadlessSession(
                timeout_ms=int(self.timeout * 1000),
                user_agent=self.user_agent,
            ).__enter__()
        except RenderError:
            self._render_disabled = True
            self._session = None
        return self._session
