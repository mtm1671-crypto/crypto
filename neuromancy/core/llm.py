"""OpenRouter LLM provider for multi-model access."""

import json
import logging
from typing import AsyncIterator
import httpx

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


class LLMConnectionError(Exception):
    """Raised when the LLM API is unreachable or returns a transport-level error."""

POPULAR_MODELS = [
    ("anthropic/claude-sonnet-4", "Claude Sonnet 4"),
    ("anthropic/claude-haiku-4", "Claude Haiku 4"),
    ("openai/gpt-4o", "GPT-4o"),
    ("openai/gpt-4o-mini", "GPT-4o Mini"),
    ("meta-llama/llama-3.1-405b-instruct", "Llama 3.1 405B"),
    ("google/gemini-2.0-flash-001", "Gemini 2.0 Flash"),
]

class OpenRouterProvider:
    def __init__(self, api_key: str, model: str = "anthropic/claude-sonnet-4"):
        self.api_key = api_key
        self.model = model
        self._client = httpx.AsyncClient(
            base_url=OPENROUTER_BASE,
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer": "https://github.com/neuromancy",
                "X-Title": "Neuromancy",
            },
            timeout=120.0,
        )

    async def complete(self, messages: list[dict], tools: list[dict] | None = None) -> dict:
        """Non-streaming completion. Returns the full response dict."""
        payload = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        try:
            resp = await self._client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise LLMConnectionError(f"LLM returned HTTP {e.response.status_code}") from e
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.NetworkError) as e:
            raise LLMConnectionError(f"LLM connection failed: {e}") from e

    async def stream_complete(self, messages: list[dict], tools: list[dict] | None = None) -> AsyncIterator[dict]:
        """Streaming completion. Yields SSE chunks as dicts."""
        payload = {"model": self.model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        return
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed SSE chunk: %s", data[:200])
                        continue
        except httpx.HTTPStatusError as e:
            raise LLMConnectionError(f"LLM returned HTTP {e.response.status_code}") from e
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, httpx.NetworkError) as e:
            raise LLMConnectionError(f"LLM connection failed: {e}") from e

    async def close(self):
        await self._client.aclose()
