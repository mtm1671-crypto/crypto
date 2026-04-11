"""Tests for the FetchPagePlugin."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from neuromancy.plugins.fetch_page import FetchPagePlugin, _Cache


# ---------------------------------------------------------------------------
# Cache tests
# ---------------------------------------------------------------------------

class TestCache:
    def test_set_and_get(self):
        cache = _Cache(ttl=60.0)
        cache.set("https://example.com", "hello")
        assert cache.get("https://example.com") == "hello"

    def test_miss_on_empty(self):
        cache = _Cache(ttl=60.0)
        assert cache.get("https://example.com") is None

    def test_expired_entry(self):
        cache = _Cache(ttl=0.01)
        cache.set("https://example.com", "hello")
        time.sleep(0.02)
        assert cache.get("https://example.com") is None

    def test_different_urls(self):
        cache = _Cache(ttl=60.0)
        cache.set("https://a.com", "aaa")
        cache.set("https://b.com", "bbb")
        assert cache.get("https://a.com") == "aaa"
        assert cache.get("https://b.com") == "bbb"


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------

class TestFetchPageManifest:
    def test_tool_name(self):
        plugin = FetchPagePlugin()
        assert plugin.manifest.tools[0].name == "fetch_page"

    def test_no_approval_required(self):
        plugin = FetchPagePlugin()
        assert plugin.manifest.tools[0].requires_approval is False

    def test_url_parameter_required(self):
        plugin = FetchPagePlugin()
        params = plugin.manifest.tools[0].parameters
        assert "url" in params["properties"]
        assert "url" in params["required"]


# ---------------------------------------------------------------------------
# Execute — with mocked HTTP
# ---------------------------------------------------------------------------

def _mock_response(text: str, content_type: str = "text/html", status: int = 200):
    """Create a mock httpx.Response."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status
    resp.headers = {"content-type": content_type}
    resp.raise_for_status = MagicMock()
    if status >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestFetchPageExecute:
    @pytest.mark.asyncio
    async def test_unknown_tool_raises(self):
        plugin = FetchPagePlugin()
        with pytest.raises(ValueError, match="Unknown tool"):
            await plugin.execute("bogus", {"url": "https://example.com"})

    @pytest.mark.asyncio
    async def test_returns_extracted_content(self):
        html = """
        <html><body>
        <article><p>This is the main article content about Python programming.</p></article>
        <footer>Copyright 2024</footer>
        </body></html>
        """
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response(html))
            mock_client_cls.return_value = mock_client

            result = await plugin.execute("fetch_page", {"url": "https://example.com/article"})

        # trafilatura should extract something (or fallback message)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_non_html_returns_raw_text(self):
        raw = "line1\nline2\nline3"
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response(raw, "text/plain"))
            mock_client_cls.return_value = mock_client

            result = await plugin.execute("fetch_page", {"url": "https://example.com/data.txt"})

        assert result == raw

    @pytest.mark.asyncio
    async def test_http_error_returns_message(self):
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response("", status=404))
            mock_client_cls.return_value = mock_client

            result = await plugin.execute("fetch_page", {"url": "https://example.com/missing"})

        assert "Failed to fetch" in result

    @pytest.mark.asyncio
    async def test_caches_result(self):
        html = "<html><body><p>Cached content here</p></body></html>"
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response(html))
            mock_client_cls.return_value = mock_client

            result1 = await plugin.execute("fetch_page", {"url": "https://example.com"})
            result2 = await plugin.execute("fetch_page", {"url": "https://example.com"})

        # Second call should hit cache — only 1 HTTP call
        assert mock_client.get.call_count == 1
        assert result1 == result2

    @pytest.mark.asyncio
    async def test_truncates_long_content(self):
        # Create content exceeding MAX_CHARS
        long_text = "word " * 5000  # ~25k chars
        html = f"<html><body><article><p>{long_text}</p></article></body></html>"
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response(html))
            mock_client_cls.return_value = mock_client

            result = await plugin.execute("fetch_page", {"url": "https://example.com/long"})

        assert len(result) <= plugin.MAX_CHARS + 50  # +50 for truncation marker

    @pytest.mark.asyncio
    async def test_non_html_truncates(self):
        long_raw = "x" * 20_000
        plugin = FetchPagePlugin()

        with patch("neuromancy.plugins.fetch_page.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=_mock_response(long_raw, "application/json"))
            mock_client_cls.return_value = mock_client

            result = await plugin.execute("fetch_page", {"url": "https://example.com/big.json"})

        assert len(result) == plugin.MAX_CHARS
