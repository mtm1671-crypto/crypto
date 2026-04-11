"""Web search plugin for Neuromancy using DuckDuckGo HTML search."""

import re

import httpx

from .base import BasePlugin, PluginManifest, ToolSchema


class WebSearchPlugin(BasePlugin):
    """Plugin that searches the web via DuckDuckGo HTML endpoint."""

    SEARCH_URL = "https://html.duckduckgo.com/html/"
    MAX_RESULTS = 5

    @property
    def manifest(self) -> PluginManifest:
        return PluginManifest(
            name="web_search",
            version="0.1.0",
            description="Search the web using DuckDuckGo.",
            tools=[
                ToolSchema(
                    name="web_search",
                    description="Search the web and return the top results with titles, URLs, and snippets.",
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "The search query.",
                            },
                        },
                        "required": ["query"],
                    },
                    requires_approval=False,
                ),
            ],
        )

    async def execute(self, tool_name: str, arguments: dict) -> str:
        if tool_name != "web_search":
            raise ValueError(f"Unknown tool: {tool_name}")

        query = arguments["query"]
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                response = await client.get(
                    self.SEARCH_URL,
                    params={"q": query},
                    headers={"User-Agent": "Mozilla/5.0 (compatible; Neuromancy/0.1)"},
                )
                response.raise_for_status()
        except httpx.HTTPError as e:
            return f"Search request failed: {e}"

        return self._parse_results(response.text)

    def _parse_results(self, html: str) -> str:
        """Parse DuckDuckGo HTML search results using simple string/regex parsing."""
        results: list[dict[str, str]] = []

        # DuckDuckGo HTML results are in <a class="result__a" ...> for titles/links
        # and <a class="result__snippet" ...> for snippets.
        # We split on result blocks and extract what we need.
        result_blocks = re.split(r'<div class="result\s', html)

        for block in result_blocks[1:]:  # skip preamble before first result
            if len(results) >= self.MAX_RESULTS:
                break

            # Extract title and URL from result__a anchor
            title_match = re.search(
                r'class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                block,
                re.DOTALL,
            )
            if not title_match:
                continue

            url = title_match.group(1)
            title = re.sub(r"<[^>]+>", "", title_match.group(2)).strip()

            # Extract snippet
            snippet = ""
            snippet_match = re.search(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
                block,
                re.DOTALL,
            )
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

            if title or url:
                results.append({"title": title, "url": url, "snippet": snippet})

        if not results:
            # Check if HTML had result blocks but we failed to extract from them
            if len(result_blocks) > 1:
                return "Search returned results but parsing failed — DuckDuckGo may have changed its HTML format."
            return "No results found."

        lines: list[str] = []
        for i, r in enumerate(results, 1):
            lines.append(f"{i}. {r['title']}")
            lines.append(f"   URL: {r['url']}")
            if r["snippet"]:
                lines.append(f"   {r['snippet']}")
            lines.append("")

        return "\n".join(lines).strip()
