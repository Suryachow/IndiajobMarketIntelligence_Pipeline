"""Unit tests for the optional headless-render module (stdlib unittest).

The real browser is never started: the session and the Playwright availability
probe are stubbed, so these tests run offline and fast.

Run with:  py -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from jobintel.render import (  # noqa: E402
    HeadlessSession,
    RenderingClient,
    RenderError,
    available,
)


class TestAvailability(unittest.TestCase):
    def test_returns_bool(self):
        self.assertIsInstance(available(), bool)


class TestHeadlessSessionGuards(unittest.TestCase):
    """Calling a closed session must fail loudly, never silently."""

    def test_get_json_before_open_raises(self):
        with self.assertRaises(RenderError):
            HeadlessSession().get_json("https://example.com/api")

    def test_post_json_before_open_raises(self):
        with self.assertRaises(RenderError):
            HeadlessSession().post_json("https://example.com/api", {"a": 1})

    def test_html_before_open_raises(self):
        with self.assertRaises(RenderError):
            HeadlessSession().html()

    def test_cookie_before_open_raises(self):
        with self.assertRaises(RenderError):
            HeadlessSession().cookie("XSRF-TOKEN")


class _FakeSession:
    """Minimal stand-in for HeadlessSession."""

    def __init__(self, html: str = ""):
        self._html = html
        self.goto_calls = []

    def goto(self, url: str, wait_until: str = "networkidle") -> str:
        self.goto_calls.append(url)
        return url

    def html(self) -> str:
        return self._html

    def close(self) -> None:
        pass


class TestRenderingClient(unittest.TestCase):
    def test_render_disabled_uses_raw_http(self):
        client = RenderingClient(render=False)
        with mock.patch.object(RenderingClient, "get_text", return_value="<html>raw</html>"):
            self.assertEqual(client.get_page("https://example.com/x"), "<html>raw</html>")

    def test_unavailable_renderer_falls_back_to_raw_http(self):
        client = RenderingClient(render=True)
        with mock.patch("jobintel.render.available", return_value=False):
            with mock.patch.object(RenderingClient, "get_text", return_value="raw"):
                self.assertEqual(client.get_page("https://example.com/x"), "raw")

    def test_rendered_dom_is_returned(self):
        client = RenderingClient(render=True)
        session = _FakeSession("<html><body>rendered</body></html>")
        client._session = session
        with mock.patch("jobintel.render.available", return_value=True):
            out = client.get_page("https://example.com/x")
        self.assertEqual(out, "<html><body>rendered</body></html>")
        self.assertEqual(session.goto_calls, ["https://example.com/x"])

    def test_render_error_falls_back_to_raw_http(self):
        client = RenderingClient(render=True)

        class _Boom(_FakeSession):
            def goto(self, url, wait_until="networkidle"):
                raise RenderError("navigation exploded")

        client._session = _Boom()
        with mock.patch("jobintel.render.available", return_value=True):
            with mock.patch.object(RenderingClient, "get_text", return_value="raw"):
                self.assertEqual(client.get_page("https://example.com/x"), "raw")

    def test_close_releases_the_session(self):
        client = RenderingClient(render=True)
        session = _FakeSession()
        closed = {"count": 0}
        session.close = lambda: closed.__setitem__("count", closed["count"] + 1)
        client._session = session
        client.close()
        self.assertEqual(closed["count"], 1)
        self.assertIsNone(client._session)

    def test_close_is_idempotent(self):
        client = RenderingClient(render=True)
        client.close()  # never opened -> no error


if __name__ == "__main__":
    unittest.main()
