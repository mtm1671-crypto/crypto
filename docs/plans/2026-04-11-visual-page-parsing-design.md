# Visual Page Parsing — Design Plan

## Problem

`fetch_page` extracts text, but many pages are layout-dependent — dashboards, charts, infographics, complex tables, SPAs that render client-side, pages behind JS rendering. The agent needs to **see** pages as a human would: take a screenshot, send it to a vision model, and get structured observations back.

## Architecture

```
               ┌──────────────┐
               │  view_page   │  ← new tool the LLM calls
               └──────┬───────┘
                      │
          ┌───────────┼───────────┐
          │           │           │
    ScreenshotSvc  VisionSvc   Cache
    (headless      (send image  (URL→result
     browser)       to model)    TTL cache)
```

### Flow

1. LLM calls `view_page(url, question?)` — question is optional focus prompt like "what are the prices in this table?"
2. Plugin launches headless browser, navigates to URL, waits for page load
3. Takes a full-page screenshot (PNG)
4. Sends screenshot to a vision-capable model with a structured prompt
5. Returns the model's textual description/extracted data to the LLM
6. Result is cached by (url, question) key

### Why a Separate Tool (Not Replacing fetch_page)

- `fetch_page` is fast, cheap, and works great for articles/docs — no browser overhead
- `view_page` is slow (browser launch + vision API call) and expensive — only use when text extraction fails or layout matters
- The agent learns when to use which via the memory system (e.g., "fetch_page returned empty for SPAs, use view_page")

## Components

### 1. Screenshot Service

**Approach: Playwright (headless Chromium)**

- `playwright` Python package — async API, headless by default, cross-platform
- Single browser instance shared across calls (reuse context, not cold-start per call)
- Configurable viewport (default 1280x900)
- Wait strategies: `networkidle` for SPAs, `domcontentloaded` for static pages
- Full-page screenshot or viewport-only (configurable)
- Timeout: 15s for navigation + render

```python
class ScreenshotService:
    def __init__(self):
        self._browser: Browser | None = None

    async def _ensure_browser(self) -> Browser:
        if not self._browser:
            pw = await async_playwright().start()
            self._browser = await pw.chromium.launch(headless=True)
        return self._browser

    async def capture(self, url: str, full_page: bool = False) -> bytes:
        browser = await self._ensure_browser()
        page = await browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            await page.goto(url, wait_until="networkidle", timeout=15000)
            return await page.screenshot(full_page=full_page, type="png")
        finally:
            await page.close()
```

**Alternative: Browserless API / external screenshot service**
- Could use a hosted service (browserless.io, screenshotapi.net) to avoid bundling Chromium
- Tradeoff: adds external dependency but removes ~300MB Chromium download
- Decision: start with Playwright locally, add API fallback later

### 2. Vision Service

**Sends screenshot to a vision-capable model and gets structured observations.**

- Uses the same OpenRouter provider already in the codebase
- Model: configurable, default `anthropic/claude-sonnet-4` (vision-capable)
- Prompt structure:

```python
messages = [
    {"role": "system", "content": (
        "You are a visual page analyzer. Describe what you see on this web page. "
        "Extract: page title, main content, navigation structure, key data "
        "(tables, prices, stats), interactive elements, and any errors/warnings. "
        "Be factual and structured. Use markdown formatting."
    )},
    {"role": "user", "content": [
        {"type": "image_url", "image_url": {
            "url": f"data:image/png;base64,{b64_screenshot}"
        }},
        {"type": "text", "text": question or "Describe this page and extract its key content."},
    ]},
]
```

- Returns the model's text response directly to the calling LLM

### 3. ViewPage Plugin

```python
class ViewPagePlugin(BasePlugin):
    tools = [
        ToolSchema(
            name="view_page",
            description=(
                "Take a screenshot of a web page and analyze it visually. "
                "Use this when fetch_page returns empty/useless content, "
                "or when you need to understand page layout, charts, images, "
                "or JavaScript-rendered content."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to view."},
                    "question": {
                        "type": "string",
                        "description": "Optional specific question about the page, e.g. 'what are the pricing tiers?'",
                    },
                },
                "required": ["url"],
            },
            requires_approval=False,
        )
    ]
```

### 4. Cache

Same `_Cache` pattern as fetch_page but keyed on `(url, question)` tuple. TTL of 5 minutes.

### 5. Multi-Frame Support (Future)

For pages that require scrolling or interaction:
- Take multiple viewport screenshots (scroll down in increments)
- Send as a sequence of images to the vision model
- Or stitch into one tall image first
- Decision: start with single viewport screenshot, add scrolling later based on need

## Files to Create/Modify

### New files
1. `neuromancy/plugins/view_page.py` — ViewPagePlugin, ScreenshotService, VisionService

### Modified files
2. `neuromancy/server/app.py` — register ViewPagePlugin, pass LLM provider to it
3. `pyproject.toml` — add `playwright` dependency
4. `tests/test_view_page.py` — unit tests with mocked browser + vision

## Dependencies

- `playwright>=1.40` — headless browser automation
- Post-install: `playwright install chromium` (downloads ~130MB browser binary)
- No new frontend changes — the tool result is text that flows through existing chat UI

## Plugin Base Change

The ViewPagePlugin needs access to the LLM provider (for the vision call). Current `BasePlugin.execute()` only receives `(tool_name, arguments)`. Two options:

**Option A: Pass LLM via constructor**
```python
class ViewPagePlugin(BasePlugin):
    def __init__(self, llm: OpenRouterProvider):
        self.llm = llm
```
Simple, but couples plugin to LLM lifecycle (LLM is recreated per WebSocket session).

**Option B: Execution context dict**
```python
# In BasePlugin
async def execute(self, tool_name: str, arguments: dict, context: dict | None = None) -> str:

# In QueryLoop, pass context:
output = await plugin.execute(tool_name, args, context={"llm": self.llm})
```
More flexible, doesn't break existing plugins (they ignore `context`).

**Recommendation: Option B** — it's backwards-compatible and allows future plugins to access session state, memory store, etc. without changing their constructor.

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| Playwright Chromium is large (~130MB) | Lazy download on first use; document in README |
| Screenshot + vision call is slow (5-10s) | Cache aggressively; stream "taking screenshot..." status to UI |
| Vision model costs per call | Default to cheaper model (haiku); make configurable |
| Some pages block headless browsers | Set realistic user-agent; add stealth plugin option |
| Base64 screenshots are large in API payload | Resize/compress PNG before encoding (max 1280px wide) |
| Rate limiting on vision API | Respect OpenRouter rate limits; queue concurrent calls |

## Implementation Order

1. Add `context` parameter to `BasePlugin.execute()` signature (backwards-compatible default `None`)
2. Update `QueryLoop` to pass `{"llm": self.llm}` as context to plugin execute calls
3. Build `ScreenshotService` with Playwright
4. Build `VisionService` that calls vision model via OpenRouter
5. Build `ViewPagePlugin` combining both services
6. Register in app.py
7. Tests with mocked browser + mocked vision response
8. Add `playwright` to pyproject.toml + install instructions

## Verification

1. `view_page("https://example.com")` → returns descriptive text of the page
2. `view_page("https://example.com", question="what is the heading?")` → focused answer
3. SPA page that `fetch_page` can't extract → `view_page` succeeds
4. Cache hit on repeated call → no second browser launch
5. Graceful error on unreachable URL
6. `pytest tests/test_view_page.py` passes
