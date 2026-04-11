"""Page fetching plugin — downloads a URL and extracts readable content."""

from __future__ import annotations

import time
from typing import Optional

import httpx

from .base import BasePlugin, PluginManifest, ToolSchema

# Lazy import — trafilatura is heavy, only load on first use
_trafilatura = None


def _get_trafilatura():
    global _trafilatura
    if _trafilatura is None:
        import trafilatura as _tf
        _trafilatura = _tf
    return _trafilatura


class _Cache:
    """Simple TTL cache for fetched pages."""

    def __init__(self, ttl: float = 300.0):
        self._ttl = ttl
        self._store: dict[str, tuple[str, float]] = {}

    def get(self, url: str) -> Optional[str]:
        entry = self._store.get(url)
        if entry and (time.monotonic() - entry[1]) < self._ttl:
            return entry[0]
        return None

    def set(self, url: str, content: str) -> None:
        self._store[url] = (content, time.monotonic())


class FetchPagePlugin(BasePlugin):
    """Plugin that fetches a web page and extracts its readable text content."""

    MAX_CHARS = 12_000
    TIMEOUT = 20.0

    def __init__(self) -> None:
        self._cache = _Cache(ttl=300.0)

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="fetch_page",
            version="0.1.0",
            description="Fetch a web page and extract its readable text content.",
            tools=[
                ToolSchema(
                    name="fetch_page",
                    description=(
                        "Download a web page by URL and return its main text content "
                        "(articles, docs, etc.) with HTML boilerplate removed. "
                        "Use this after web_search to read a specific result page."
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "url": {
                                "type": "string",
                                "description": "The URL of the page to fetch.",
                            },
                        },
                        "required": ["url"],
                    },
                    requires_approval=False,
                ),
            ],
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "fetch_page":
            raise ValueError(f"Unknown tool: {tool_name}")

        url: str = arguments["url"]

        # Check cache first
        cached = self._cache.get(url)
        if cached is not None:
            return cached

        # Fetch the page
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self.TIMEOUT,
                headers={"User-Agent": "Mozilla/5.0 (compatible; Neuromancy/0.1)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"Failed to fetch {url}: {e}"

        content_type = resp.headers.get("content-type", "")

        # Non-HTML content — return raw text truncated
        if "html" not in content_type:
            text = resp.text[:self.MAX_CHARS]
            self._cache.set(url, text)
            return text

        # Extract readable content with trafilatura
        text = self._extract(resp.text, url)
        self._cache.set(url, text)
        return text

    def _extract(self, html: str, url: str) -> str:
        """Extract main content from HTML using trafilatura with fallback."""
        tf = _get_trafilatura()

        try:
            # trafilatura's extract gives clean text from article/main content
            text = tf.extract(
                html,
                url=url,
                include_links=True,
                include_tables=True,
                favor_recall=True,
                output_format="txt",
            )

            if not text:
                # Fallback: try bare extraction without heuristics
                text = tf.extract(html, url=url, favor_precision=False, output_format="txt")
        except Exception as e:
            return f"Content extraction failed for {url}: {e}"

        if not text:
            return f"Could not extract readable content from {url}. The page may require JavaScript."

        # Truncate to budget
        if len(text) > self.MAX_CHARS:
            text = text[:self.MAX_CHARS] + "\n\n[... truncated]"

        return text
