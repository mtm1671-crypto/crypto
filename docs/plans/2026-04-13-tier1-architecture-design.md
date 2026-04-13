# Tier 1 Architecture — Skills, Gateways, Cron, Subagents

## Overview

Neuromancy currently runs as an Electron app that spawns a Python FastAPI backend as a child process. This topology works for a desktop chat UI but is fatally wrong for the four features that define the "self-improving agent" story:

1. **Skills system** — self-improving procedural memory. Needs persistent storage, a runner, a creation flow, and an evolution path toward compiled Python.
2. **Gateways** — reach the agent from Telegram/CLI/Discord/Slack/etc. Needs a process that runs when your laptop is asleep.
3. **Cron scheduler** — proactive, scheduled agent actions. Needs a long-lived process and a delivery path.
4. **Subagents** — delegate sub-tasks to fresh contexts. Needs a lifecycle manager and optional isolation.

All four features depend on the same architectural commitment: a **headless daemon process** that lives independently of Electron. This document is the unified design for all four features plus the daemon topology they share.

**Session scope**: this design covers all four features. The implementation session that follows ships **only the skills system (plus the memory SQLite+FTS5 migration it depends on)**. Gateways, cron, and subagents are designed here so their interfaces are fixed, but their implementation defers to future sessions. Because the design is unified, the deferred features plug into the skills runtime cleanly when they eventually land — no refactors required.

## Key Decisions

The following design decisions are load-bearing. Reversing any of them means rewriting significant portions of multiple features.

| # | Decision | Chosen | Rationale |
|---|---|---|---|
| 1 | Session scope | Design all 4, implement skills only | Skills is the identity feature; the others compose with it |
| 2 | Skill execution model | Procedure templates primary, Python compiled escape hatch | Safe LLM authoring; evolution path to deterministic code |
| 3 | Skill invocation surface | `run_skill` meta-tool with lightweight catalog in system prompt | Keeps tool list small as skill count grows |
| 4 | Skill creation trigger | Post-turn reflection + pending queue + mandatory user review | Rides existing reflection infra; security gate |
| 5 | Skill evolution ambition | Templates + user review + test grounding + compile scaffolding in v1 | Empirical grounding + forward-compat infra for v2 |
| 6 | Gateway deployment | Headless daemon process, independent of Electron | Unlocks cron; always-reachable from messaging |
| 7 | Gateway platforms | CLI + Telegram concrete, contract layer for the rest | Avoids WhatsApp/Signal operational traps |
| 8 | Subagent isolation | asyncio default, subprocess opt-in for compiled skills | Fast common path + strong isolation when it matters |
| 9 | Template format | Sequential + `when`/`then`/`else` + `terminate_if` (no loops, no graphs) | Branching where it's worth it; compiles cleanly to Python |
| 10 | Subagent tool design | One `spawn_subagent` tool with `background` flag + `wait_subagents` | Matches Claude Code's pattern; simpler than three tools |
| 11 | Subagent profiles | Four canned profiles (research, computation, review, default) | Makes delegation discoverable; addresses over-spawning |
| 12 | Tray integration | Electron's native `Tray` API, not pystray | Battle-tested cross-platform, zero Python tray code |

## Top-Level Architecture

### The daemon process

`neuromancy-daemon` becomes the runtime. It:

- Runs as a Python process started at login or on-demand by the Electron launcher
- Owns all persistent state: `MemoryStore`, `PluginRegistry`, `SkillRegistry`, cron schedule, pending candidates, session history
- Exposes a local HTTP+WebSocket server on `127.0.0.1:8787` as its public surface
- Hosts all gateways, the cron scheduler, and the subagent manager as background tasks in its own event loop
- Survives Electron close; Electron-as-tray keeps running in minimized state to host the tray icon and reopens the UI window when clicked

Electron's role shrinks to **a UI client that connects to the daemon over WebSocket**. When Electron starts, it probes `http://127.0.0.1:8787/health`; if unreachable, it launches the daemon. When Electron closes, the daemon keeps running. No more child-process coupling. The tray menu lives in Electron (using its native `Tray` API, which is mature on all three platforms) but every menu action translates to an HTTP request to the daemon.

### Process topology

```
                                  ┌────────────────────────────┐
                                  │      neuromancy-daemon     │
                                  │   (always-on Python proc)  │
                                  │                            │
  ┌──────────────┐    HTTP/WS     │  ┌──────────────────────┐  │
  │ Electron UI  │◄──────────────►│  │   FastAPI server     │  │
  │ (minimized   │  127.0.0.1:8787│  │   /ws/{session_id}   │  │
  │  to tray)    │                │  │   /skills/*          │  │
  └──────────────┘                │  │   /cron/*            │  │
                                  │  │   /subagents/*       │  │
  ┌──────────────┐   WebSocket    │  └──────────┬───────────┘  │
  │ CLI client   │◄──────────────►│             │              │
  │ (neuromancy) │                │             ▼              │
  └──────────────┘                │  ┌──────────────────────┐  │
                                  │  │  Runtime core:       │  │
  ┌──────────────┐    HTTPS       │  │  - QueryLoop(s)      │  │
  │ Telegram     │◄──────────────►│  │  - PluginRegistry    │  │
  │ Bot API      │                │  │  - MemoryStore       │  │
  └──────────────┘                │  │  - SkillRegistry     │  │
                                  │  │  - SubagentManager   │  │
                                  │  │  - CronScheduler     │  │
                                  │  │  - GatewayManager    │  │
                                  │  └──────────────────────┘  │
                                  └────────────────────────────┘
```

### On-disk layout

```
~/.neuromancy/
├── config.json              (ports, gateway creds refs, daemon settings)
├── memory.db                (SQLite with FTS5 — replaces memory.json)
├── sessions/
│   └── {uuid}.json
├── skills/
│   ├── active/
│   │   └── {skill_name}/
│   │       ├── manifest.json
│   │       ├── template.yaml
│   │       ├── compiled.py           (optional — v2 evolution)
│   │       ├── tests/
│   │       │   └── {timestamp}.json  (captured runs)
│   │       └── versions/
│   │           └── v{n}.yaml
│   ├── pending/
│   │   └── {skill_name}/
│   └── quarantine/
│       └── {skill_name}/             (malformed skills)
├── cron/
│   ├── schedule.json
│   └── history/
│       └── {job_id}/
│           └── {timestamp}.json
└── daemon.pid                (single-instance lock)
```

**Every skill is a directory, not a file.** A skill is a bundle of (template + compiled code + test cases + version history), and the directory shape is what lets the evolution story work. A flat-file format would force parallel directories for every new piece of evolutionary state.

## Feature 1: Skills System

### Template format

YAML, not JSON or markdown. LLMs author YAML better, humans review it faster, and diffing YAML templates across versions is vastly more legible.

**v1 supports three step constructs:** `tool_call`, `llm_call`, and `when`/`then`/`else` blocks. Any step can carry a `terminate_if` expression to exit the skill early.

```yaml
name: research_topic
version: 1
description: >
  Research a topic by searching the web and synthesizing the results.
  Use when the user wants a quick overview of something they don't know.

inputs:
  topic:
    type: string
    description: The topic to research
    required: true
  depth:
    type: string
    enum: [shallow, deep]
    default: shallow

steps:
  - id: search
    action: tool_call
    tool: web_search
    arguments:
      query: "{{ inputs.topic }}"
      limit: 5

  - when: "steps.search.output.results.length == 0"
    then:
      - id: fallback_view
        action: tool_call
        tool: view_page
        arguments:
          url: "https://www.google.com/search?q={{ inputs.topic }}"
    else:
      - id: summarize
        action: llm_call
        model: anthropic/claude-haiku-4
        prompt: |
          Summarize these search results for the topic "{{ inputs.topic }}":

          {{ steps.search.output }}

          Produce a 2-paragraph summary with citations.

output: "{{ steps.summarize.output }}"
```

### Condition DSL

Strictly bounded. The grammar is:

- **References**: `inputs.FIELD`, `steps.ID.output[.PATH]`, `steps.ID.error`
- **Literals**: strings, numbers, booleans, `null`
- **Comparisons**: `==`, `!=`, `<`, `<=`, `>`, `>=`
- **Boolean**: `and`, `or`, `not`
- **Length helper**: `.length` on arrays and strings only

No arithmetic, no arbitrary method calls, no regex, no string concatenation, no Python expressions. Parser is recursive-descent, ~100 lines. The grammar is documented in the reflection prompt so the LLM has a definition to author against.

### Templating

Minimal Jinja2 subset: `{{ inputs.X }}`, `{{ steps.X.output }}`, `{{ steps.X.error }}`, dotted-path traversal. Nothing else. No filters except `tojson` for escaping. Keeps both the sandbox small and the compile-to-Python path straightforward.

### Storage structure (per skill)

```
~/.neuromancy/skills/active/{skill_name}/
├── manifest.json       (name, version, input schema, run_count, success_rate, has_compiled, ...)
├── template.yaml       (procedure template — the "source")
├── compiled.py         (optional — created by compile step, preferred at runtime)
├── tests/
│   └── {iso_timestamp}.json  (captured runs)
└── versions/
    └── v{n}.yaml       (template history)
```

`manifest.json` is small and machine-read (JSON for strictness). `template.yaml` is LLM-authored and human-reviewed (YAML for readability).

### The skill runner

`neuromancy/core/skill_runner.py` owns one class:

```python
class SkillRunner:
    def __init__(self, registry: PluginRegistry, llm: OpenRouterProvider,
                 memory_store: MemoryStore, test_capture_dir: Path):
        ...

    async def run(
        self,
        skill: SkillManifest,
        inputs: dict,
        on_event: Callable[[StreamEvent], Awaitable[None]] | None = None,
    ) -> SkillRunResult:
        """Execute a skill. Streams step events. Captures a test case."""
```

Execution flow:

1. **Prefer compiled.** If `compiled.py` exists, import in a fresh module context and call its entry function. Return. (v2 path — v1 never produces compiled.py, but the runner checks from day one.)
2. **Validate inputs** against the skill's `input_schema` (JSON Schema via `jsonschema`).
3. **Initialize context** as `{"inputs": {...validated...}, "steps": {}}`.
4. **Walk the step list.** For each step:
    - If it's a `when` block: evaluate the condition, recurse into `then` or `else`.
    - If it's a regular step with `terminate_if`: evaluate; if true, skip remaining and render output.
    - Render `arguments` (for `tool_call`) or `prompt` (for `llm_call`) against the current context.
    - Emit `skill_step_start`.
    - Execute the step (plugin dispatch or LLM call). Individual tool calls still honor `requires_approval` — if a tool inside a skill needs approval, the skill pauses mid-execution.
    - Store result at `context["steps"][step.id]`.
    - Emit `skill_step_complete`.
    - On exception: record failure, break.
5. **Render the `output` expression** using the final context.
6. **Write test capture** to `skills/active/{name}/tests/{timestamp}.json` — `{inputs, steps, branch_path, final_output, duration_ms, cost_usd, succeeded}`. The `branch_path` field records which step ids actually executed (critical for the v2 compile step).
7. **Update manifest stats atomically.**

The test-capture step is what makes the evolution story work. Every execution, successful or failed, leaves a recording that future refinement can be gated against. v1 doesn't use the gate, but the data accumulates from day one.

### The `run_skill` meta-tool

A new class `SkillMetaTool` that looks like a plugin to `PluginRegistry` but is owned by `SkillRegistry`. It exposes exactly one tool:

```python
{
  "type": "function",
  "function": {
    "name": "run_skill",
    "description": "Execute a previously-learned skill by name. "
                   "Available skills are listed in the system prompt.",
    "parameters": {
      "type": "object",
      "properties": {
        "skill_name": {"type": "string"},
        "inputs": {"type": "object"}
      },
      "required": ["skill_name", "inputs"]
    }
  },
  "requires_approval": false
}
```

The LLM does not see full input schemas for every skill in the tool list. Instead, at the start of every turn, `QueryLoop` injects a lightweight **skill catalog** into the system prompt:

```
Available skills:
- research_topic: Research a topic by searching the web and synthesizing results. Inputs: topic, depth
- draft_email: Draft an email based on a bullet-point outline. Inputs: recipient, subject, points
- morning_briefing: Assemble a morning briefing from RSS feeds and calendar. Inputs: (none)
```

The catalog sits in its own prompt cache block (see Prompt Cache Segmentation below) so adding/removing skills invalidates only that segment, not the entire system prompt.

### Approval semantics

Two-layer model:

1. **The skill itself never requires approval to run** — it was approved once when promoted from `pending/` to `active/`.
2. **Individual steps inside a running skill still require approval if the tool does.** A skill that calls `run_command` will surface an approval prompt mid-skill, exactly as direct tool calls do. The skill pauses until approval resolves.
3. **A `trust_this_skill` flag in the manifest** can suppress individual-step approvals for skills the user has fully vetted. Default is `false`.

Rationale: the worst failure mode is an LLM-authored skill silently running a destructive command because approval was bypassed. The default keeps the security model identical to direct tool calls.

### Creation flow (reflection → pending → review → active)

Hooks into the existing `_reflect()` method in `query_loop.py`. A second pass runs — cheap, via Haiku — that examines the last turn and decides whether it was "procedural enough" to become a skill. The bar:

- ≥3 tool calls in the turn
- Tool calls formed a coherent sequence (each consumed the prior's output or was parameterized by the original input)
- The final assistant message was a synthesized result, not a clarifying question

If all three hit, the reflection LLM drafts a candidate: name, description, inferred input schema, and a `template.yaml`. The candidate is written to `~/.neuromancy/skills/pending/{auto_name}/`, and a `skill_candidate` stream event fires to any connected UI.

The Electron Skills panel shows pending candidates as review cards: name, description, template (syntax-highlighted YAML), source session link, and three buttons: **Approve**, **Edit & Approve**, **Reject**. Pending skills are **invisible to the LLM** until promoted — they do not appear in the catalog.

### Compile scaffolding (v1 infrastructure for v2 evolution)

Even though v1 does not auto-compile skills, the infrastructure ships on day one:

- Endpoint `POST /skills/{name}/compile` triggers a compile attempt
- Module `neuromancy/core/skill_compiler.py` with a function `compile_skill(skill_dir, llm) -> CompileResult`:
  1. Loads `template.yaml` and all `tests/*.json`
  2. Returns `insufficient_data` if fewer than 5 successful test captures exist
  3. Prompts the LLM: "Write a Python async function `run(inputs, plugins, llm) -> str` replicating this template. Imports limited to stdlib, `asyncio`, `json`. No FS/network outside provided plugins."
  4. Parses the returned Python, runs it against each captured test case in a **subprocess subagent** (subagent option D from question 7), compares outputs, writes `compiled.py` only if all tests pass
- The endpoint is **exposed but not called from anywhere in v1**. No auto-trigger, no UI button. v2 will add a scheduler that calls it on qualifying skills.

Why build it now if unused: building the endpoint and function together with the runner fixes the interface. When v2 wires up auto-compile, it's a 50-line scheduler, not a 500-line refactor.

### Prompt cache segmentation

The existing `prompt_cache.py` injects Anthropic cache markers into the system prompt. The skill catalog changes every time a skill is added, removed, or renamed. If the catalog is inside the same cached block as the rest of the system prompt, every skill change invalidates the whole prompt cache.

**Fix:** the system prompt is segmented into three cache blocks:

```
[stable system preamble] [cache marker 1]
[skill catalog]          [cache marker 2]
[memory context]         [cache marker 3]
```

Skill additions invalidate only the catalog segment. Memory changes invalidate only the memory segment. The stable preamble stays cached across the entire session. One-line change in `prompt_cache.py` marker placement.

### Integration with QueryLoop

Minimal:

1. `create_app()` instantiates `SkillRegistry(Path.home() / ".neuromancy" / "skills" / "active")`. Passes it to `QueryLoop`.
2. `QueryLoop.__init__` accepts `skill_registry: SkillRegistry | None = None`.
3. In `QueryLoop.run()`, if `skill_registry` is set: append `run_skill` to the tool schemas; inject skill catalog into `system_prefix`.
4. Tool dispatch: branch for `tool_call.name == "run_skill"` routes to `SkillMetaTool.execute()`.
5. `_reflect()` calls `_reflect_skill_candidate()` as a second step after the existing memory extraction.

Net change in `query_loop.py`: ~60 lines added, zero removed.

## Feature 2: Gateways

### The Gateway contract

One abstract base class in `neuromancy/gateways/base.py`:

```python
class Gateway(ABC):
    name: str                  # "cli", "telegram", etc.
    platform: str              # "terminal", "telegram", etc.

    @abstractmethod
    async def start(self, runtime: DaemonRuntime) -> None: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def send_event(self, user_id: str, event: StreamEvent) -> None: ...

    @abstractmethod
    async def send_notification(self, user_id: str, title: str, body: str) -> None: ...

    async def handle_approval(self, user_id: str, approval: ApprovalRequest) -> bool:
        """Optional override for inline approval UI (keyboards, y/n prompts)."""
```

Gateway implementations ship only for CLI (as a client, see below) and Telegram in v1. Discord, Slack, WhatsApp, Signal are designed into the contract but not implemented.

### Inbound message flow

All gateways route inbound messages through one shared dispatcher:

```python
async def dispatch_inbound(self, gateway, user_id, content, attachments=None):
    if not self._authz.is_allowed(gateway.name, user_id):
        await gateway.send_notification(user_id, "Not authorized", "...")
        return
    if not self._rate_limiter.allow(f"{gateway.name}:{user_id}"):
        await gateway.send_notification(user_id, "Slow down", "...")
        return
    session_id = f"{gateway.name}:{user_id}"
    session = self._sessions.get_or_create(session_id, model=self._config.default_model)
    loop = self._query_loops.get_or_build(session_id)
    async for event in loop.run(session, content, attachments=attachments):
        await gateway.send_event(user_id, event)
```

**Sessions are keyed `{gateway_name}:{user_id}`** and persist across reconnects. A Telegram user who comes back three days later hits the same session. A CLI user in a new terminal hits the same session. This is what makes "your agent reaches you anywhere" feel continuous.

**v1 is single-user.** The daemon has one owner identity. Multi-user routing defers to v2.

### CLI gateway — client mode

The CLI gateway is **a client, not a daemon-side subclass**. The `neuromancy` command is a standalone Python program that connects to `127.0.0.1:8787` via WebSocket, renders events to the terminal via `rich`, handles approvals with inline `y/N` prompts, and handles cancellation via Ctrl+C. From the daemon's perspective, the CLI looks like Electron — another WebSocket client.

On handshake, the CLI sends `{"type": "cli_handshake", "username": "mtm16", "hostname": "desktop"}` and the daemon keys the session as `cli:mtm16@desktop`.

### Telegram gateway — daemon-side

`python-telegram-bot>=21.0`. Polling-based (not webhook — webhooks require a public HTTPS endpoint). Config in `~/.neuromancy/config.json`:

```json
{
  "gateways": {
    "telegram": {
      "enabled": false,
      "bot_token_ref": "telegram_bot_token",
      "allowed_chat_ids": [123456789]
    }
  }
}
```

The `bot_token_ref` references an entry in Electron's encrypted `safeStorage`. The daemon reads it via IPC (when Electron is running) or an encrypted fallback file (when it isn't).

**Rendering strategy** (Telegram rate-limits bot messages aggressively):
- Start a placeholder `"*(thinking...)*"` message
- Buffer text deltas; edit the message every 1–2 seconds with accumulated text
- Tool calls render as separate messages: `🔧 Running web_search...` → `✅ Completed in 1.2s`
- Approvals use inline keyboards (Yes/No buttons); callback resolves into `QueryLoop.resolve_approval()`
- Skill step events render compactly: `📋 Running skill research_topic (step 2/5)...`
- Long outputs split at paragraph boundaries to stay under 4096 chars
- Markdown V2 escaping via a known-good helper

**Voice memos, documents, attachments** defer to optional v1 polish — skip if session time is tight.

### Authorization

- **CLI**: trust-on-localhost. Anyone who can run the CLI as your user already owns your machine.
- **Telegram**: allowlist by chat ID. Messages from other chat IDs get rejected.
- **Future gateways**: platform-idiomatic auth against a single configured owner.

### Rate limiting

Existing `RateLimiter` from `server/app.py` is promoted to `runtime/rate_limit.py`, keyed on `(gateway_name, user_id)`. Default 2 messages/second per user, burst of 5.

### What gateways do NOT do in v1

- Multi-user auth (single owner only)
- Webhooks (polling only)
- Hot gateway reload without daemon restart
- Session linking across gateways (each gateway has its own session)
- Discord, Slack, WhatsApp, Signal (contract ships, implementations don't)
- Voice synthesis on outbound
- Group chats (bot only replies in 1:1 DMs)

## Feature 3: Cron Scheduler

### Scheduling engine

`APScheduler` (`apscheduler>=3.10`) with `AsyncIOScheduler`. We bypass its built-in job store (opaque pickle format) and use our own JSON file as the source of truth. On daemon startup, we read `schedule.json` and add each job to APScheduler in memory. Changes write both at once.

### Job format

```json
{
  "id": "morning_briefing",
  "description": "Every day at 7am, summarize RSS feeds and calendar",
  "schedule": {
    "type": "cron",
    "expression": "0 7 * * *",
    "source": "every day at 7am",
    "timezone": "America/New_York"
  },
  "trigger": {
    "type": "skill",
    "skill_name": "morning_briefing",
    "inputs": {}
  },
  "delivery": {
    "targets": ["telegram", "cli"],
    "on_no_reachable_gateway": "memory_note"
  },
  "trusted_tools": ["web_search", "fetch_page", "rss_fetch"],
  "stateful_session": true,
  "misfire_grace_seconds": 3600,
  "enabled": true,
  "created_at": "2026-04-13T...",
  "last_fired_at": null,
  "last_status": null,
  "total_runs": 0,
  "total_failures": 0,
  "total_cost_usd": 0.0
}
```

### Natural-language schedule parsing

The LLM provides the cron expression directly (reliable) and the natural-language source as a separate field. We validate the expression with `croniter.is_valid()` before accepting it. No dedicated NL parsing library.

### Fire-time execution

Every job fire is a full `QueryLoop.run()` against a cron-owned session (`cron:{job_id}`), using a **restricted `PluginRegistry`** built from the job's `trusted_tools` allowlist. Tool calls outside the allowlist fail the fire. Skill triggers use the same restriction — if a skill inside a cron run calls a non-allowlisted tool, the run fails.

Delivery: for each configured gateway target, if the gateway is running, call `gateway.send_notification()`. If nothing delivered, apply `on_no_reachable_gateway`: write a memory note (default), retry next tick, or drop.

Three consecutive failures auto-disable the job.

### Trust model

`trusted_tools` is the security mechanism. Cron jobs run without a user present, so approval-on-risky-tools doesn't work. Explicit per-job allowlists keep the security surface tight. Empty list means no tools (effectively LLM-only reasoning).

### Job management tools (for the LLM)

- `schedule_job(...)` — creates a job, **requires_approval: true** (commitment to future autonomous action)
- `list_jobs()` — read-only
- `update_job(id, ...)` — in-place edits, no approval
- `delete_job(id)` — **requires_approval: true** (destroys user intent)

### Misfire handling

Default `misfire_grace_seconds: 3600`. Missed fires within the grace window fire on next daemon wake; beyond the grace window, they're skipped.

### What cron does NOT do in v1

- Complex schedule types (holidays, intervals, date lists)
- Job chaining ("after A, run B")
- Conditional firing
- Fire-time parameters
- Parallel fires (sequential within an asyncio lock)
- Distributed scheduling
- Webhook triggers

## Feature 4: Subagents

### SubagentManager

`neuromancy/core/subagent_manager.py` owns:

- Dict of active subagents by id
- Concurrency semaphore (global cap 10, per-parent cap 3)
- Two execution-mode dispatchers (asyncio and subprocess)
- Lifecycle supervision (cancellation propagation, timeout enforcement, shutdown)
- Subprocess IPC layer (pipes, pickling, result channels)

### Spawn interface — two tools

**`spawn_subagent`** — parameters include `task`, `context`, `isolated`, `timeout_seconds`, `background`, `profile`. When `background: false` (default), blocks and returns the result string. When `background: true`, returns a subagent ID immediately.

**`wait_subagents`** — takes a list of subagent IDs, waits for them, returns their results in input order. Only used when `background: true` was set.

Two tools instead of three. Parallelism is expressed via multiple `spawn_subagent` calls in one tool-call block. Matches Claude Code's pattern.

### Canned profiles

`~/.neuromancy/config.json` under `subagents.profiles`:

```json
{
  "research": {
    "description": "Read-only research: search, fetch, PDF, RSS. Cannot modify files or run commands.",
    "tool_allowlist": ["web_search", "fetch_page", "view_page", "pdf_reader", "rss_fetch", "youtube"],
    "system_prompt_preamble": "You are a research subagent..."
  },
  "computation": {
    "description": "Code execution and shell: run_command, code_runner, file_ops.",
    "tool_allowlist": ["shell", "code_runner", "file_ops", "system_info"],
    "system_prompt_preamble": "You are a computation subagent..."
  },
  "review": {
    "description": "Read-only review and analysis: read files, fetch, search. No mutation.",
    "tool_allowlist": ["file_ops:read", "fetch_page", "web_search", "github:read"],
    "system_prompt_preamble": "You are a review subagent..."
  },
  "default": {
    "description": "Full inherited permissions. No restriction.",
    "tool_allowlist": null,
    "system_prompt_preamble": null
  }
}
```

The `spawn_subagent` tool description lists available profiles. This bakes in sensible defaults, makes delegation discoverable, and addresses the "LLMs over-spawn" risk architecturally rather than via prompt engineering.

### Execution mode 1: asyncio (default)

A subagent is a new `QueryLoop` instance running as an `asyncio.Task` inside the daemon. It shares:
- `PluginRegistry` (by reference, with parent's trust scoping inherited)
- `MemoryStore` (by reference — reflection writes are visible to parent)
- `SkillRegistry` (by reference)
- Cost accounting into shared `pricing_cache`

It does not share:
- Parent's conversation history (starts with only `task` + `context`)
- Parent's session ID (ephemeral `subagent:{id}` session)

Spawn cost: microseconds. This is the 95% path.

### Execution mode 2: subprocess (isolated)

A subagent is a child Python process via `multiprocessing.spawn`. Child:
- Reconstructs a fresh `PluginRegistry` from serialized config (no shared references across process boundary)
- Reconstructs a fresh `OpenRouterProvider`
- **Does NOT construct a `MemoryStore`** — no memory access in subprocess mode in v1
- Runs a single turn and returns the result bundle via a pipe
- Exits

Spawn cost: 50–500ms. Used for:
- Running LLM-generated Python from compiled skills (`compiled.py` execution)
- Any subagent with `isolated: true`

Resource limits on the child: `RLIMIT_AS` at 512 MB (Unix), CPU wall-clock bounded by timeout (cross-platform). Windows has no equivalent to `RLIMIT_AS`; we rely on timeout and document the gap.

### Memory sharing across modes

| Aspect | asyncio mode | subprocess mode |
|---|---|---|
| `MemoryStore` reads | Shared by reference | No access |
| `MemoryStore` writes | Shared (reflection runs normally) | Returned as proposed entries; parent auto-commits |
| `PluginRegistry` | Shared with trust scoping | Reconstructed from snapshot |
| `SkillRegistry` | Shared | Not available |
| Session | Fresh `subagent:{id}` | Fresh `subagent:{id}` |
| Conversation history from parent | Not inherited (only task + context) | Not inherited |
| Cancellation | `asyncio.Task.cancel()` | Process termination (SIGTERM → SIGKILL) |
| Cost accounting | Accumulates in shared `pricing_cache` | Returned in result bundle |

### Concurrency limits

- Global cap: 10 concurrent subagents
- Per-parent cap: 3
- Depth limit: 1 (subagents cannot spawn further subagents in v1)
- Spawn queue timeout: 30s

### Cancellation semantics

- Parent turn cancelled → all child subagents cancelled via shared `cancel_event`
- Daemon shutting down → all subagents cancelled with 10s grace window
- Subagent times out → only that subagent cancelled; siblings continue
- Subagent fails internally → parent sees error in tool result; decides whether to retry

### Integration with QueryLoop

- `QueryLoop.__init__` accepts optional `subagent_manager: SubagentManager | None = None`
- If set, `spawn_subagent` and `wait_subagents` are appended to the tool schema list
- Tool dispatch branches to `SubagentManager.spawn()` / `.wait()`
- Parent session ID is threaded through so the manager knows who spawned what

Net change: ~40 lines added to `query_loop.py`.

### What subagents do NOT do in v1

- Spawn further subagents (depth limit 1)
- Access `MemoryStore` in subprocess mode
- Communicate with other subagents (no blackboard)
- Stream partial progress to parent UI (final result only)
- Survive daemon restarts
- Run in containers
- Expose their own stream to gateways

## Complete File Layout

Items marked **[BUILD]** ship this session. Items marked **[DEFER]** are design-only.

```
neuromancy/
├── core/
│   ├── skill_models.py               [BUILD]  Pydantic: SkillManifest, SkillTemplate, etc.
│   ├── skill_condition.py            [BUILD]  Condition DSL parser + evaluator
│   ├── skill_templating.py           [BUILD]  Jinja subset rendering
│   ├── skill_registry.py             [BUILD]  Load/index active + pending skills
│   ├── skill_runner.py               [BUILD]  Execute templates; capture tests
│   ├── skill_reflection.py           [BUILD]  Extract candidates from turns
│   ├── skill_meta_tool.py            [BUILD]  run_skill tool
│   ├── skill_compiler.py             [BUILD]  Template → Python (unused v1, stub)
│   ├── memory.py                     [MODIFY] SQLite+FTS5 migration
│   ├── cron_models.py                [DEFER]
│   ├── cron_scheduler.py             [DEFER]
│   ├── cron_executor.py              [DEFER]
│   ├── subagent_models.py            [DEFER]
│   ├── subagent_manager.py           [DEFER]
│   ├── subagent_meta_tool.py         [DEFER]
│   ├── subagent_profiles.py          [DEFER]
│   ├── subagent_child.py             [DEFER]
│   ├── query_loop.py                 [MODIFY] Skill catalog injection + meta-tool dispatch
│   └── prompt_cache.py               [MODIFY] Cache segmentation for skill catalog
├── gateways/                         [DEFER directory]
│   ├── base.py
│   ├── manager.py
│   ├── cli/client.py
│   └── telegram/gateway.py
├── runtime/                          [DEFER directory]
│   ├── daemon_runtime.py
│   ├── rate_limit.py
│   └── session_store.py
├── server/
│   └── app.py                        [MODIFY] Add /skills/* endpoints
├── plugins/
│   └── base.py                       [MODIFY] Add PluginRegistry.filtered()
└── cli/main.py                       [DEFER]

~/.neuromancy/
├── memory.db                         [BUILD]  SQLite with FTS5
├── skills/
│   ├── active/                       [BUILD]
│   ├── pending/                      [BUILD]
│   └── quarantine/                   [BUILD]
├── cron/                             [DEFER]
└── daemon.pid                        [DEFER]

frontend/src/components/
└── SkillsPanel.tsx                   [BUILD]  Review pending + list active + run history

tests/
├── test_memory_sqlite.py             [BUILD]
├── test_skill_models.py              [BUILD]
├── test_skill_condition.py           [BUILD]
├── test_skill_templating.py          [BUILD]
├── test_skill_registry.py            [BUILD]
├── test_skill_runner.py              [BUILD]
├── test_skill_reflection.py          [BUILD]
├── test_skill_meta_tool.py           [BUILD]
├── test_skill_compiler.py            [BUILD]
└── test_query_loop_skills.py         [BUILD]  Integration test
```

**This session:** 11 new modules in `core/`, 3 modifications (`memory.py`, `query_loop.py`, `prompt_cache.py`), 1 modification (`plugins/base.py`), 1 new panel (`SkillsPanel.tsx`), 10 test modules. Roughly **3500–4500 new lines** + ~800 lines of modifications.

**Deferred:** the `gateways/`, `runtime/`, and `cli/` trees, plus cron and subagent core modules. Each lands in a focused future session against this design.

## Implementation Order

### Phase 1 — Plugin base prep (~30 min)
Add `PluginRegistry.filtered(allowlist: set[str] | None)` method in `plugins/base.py`. Returns a wrapper registry that raises on any tool lookup outside the allowlist. Used by cron (future) and by skill execution to scope permissions.

### Phase 2 — Memory SQLite+FTS5 migration (~5 hours)
Rewrite `neuromancy/core/memory.py` to use SQLite with FTS5 indexing. Schema:

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session TEXT,
    confidence REAL NOT NULL DEFAULT 0.5,
    times_reinforced INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    last_used TEXT
);

CREATE VIRTUAL TABLE memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
END;
```

Preserve the existing `MemoryStore` public API (`add`, `reinforce`, `get_relevant`, `find_similar`, `get_all`, `remove`). Rewrite internals to use `aiosqlite` (already in `pyproject.toml`). Migration code runs on first startup: if `memory.json` exists, load it, insert every entry into SQLite, back up `memory.json` to `memory.json.pre-sqlite.bak`.

`get_relevant()` replaces word-overlap with an FTS5 `MATCH` query, re-ranked by `confidence * rank`. `find_similar()` uses the same path with a higher threshold.

Tests: migration from a known JSON file, FTS5 search correctness, atomic writes, concurrent read/write safety.

### Phase 3 — Skill data models (~3 hours)
`skill_models.py` with Pydantic classes: `SkillManifest`, `SkillTemplate`, `SkillStep`, `SkillStepAction` (enum: tool_call, llm_call, when), `SkillWhenBlock`, `SkillRunResult`, `SkillTestCase`. YAML loading via `pyyaml`. JSON Schema validation via `jsonschema`. Tests for valid/invalid templates, schema drift, missing fields.

### Phase 4 — Condition DSL (~4 hours)
`skill_condition.py`. Recursive-descent parser producing an AST. Evaluator takes context dicts. Tests covering all operators, short-circuit semantics, missing references, type mismatches, grammar edge cases. Grammar documented inline for the reflection prompt to consume.

### Phase 5 — Templating (~2 hours)
`skill_templating.py`. Jinja-subset renderer. Whitelist of supported constructs only. Tests for renders, missing variables, nested paths, escaping.

### Phase 6 — SkillRegistry (~3 hours)
`skill_registry.py`. Loads `skills/active/*/manifest.json` on construction. Builds name→manifest map. Provides lookup and catalog generation. Moves malformed skills to `quarantine/`. Tests with temp directories.

### Phase 7 — SkillRunner (~6 hours) — LARGEST
`skill_runner.py`. The core execution engine. Handles sequential + `when` + `terminate_if`. Dispatches to `PluginRegistry` and `OpenRouterProvider`. Writes test captures. Updates manifest stats atomically. Tests with mocked plugins and LLM.

### Phase 8 — run_skill meta-tool (~2 hours)
`skill_meta_tool.py`. Delegates to `SkillRunner`. Forwards events via callback. Tests.

### Phase 9 — QueryLoop integration (~3 hours)
Modify `query_loop.py`. Accept `skill_registry`. Inject catalog. Append tool schema. Dispatch. Integration test running a fake skill end-to-end.

### Phase 10 — Prompt cache segmentation (~1 hour)
Modify `prompt_cache.py`. Three cache blocks: preamble, skill catalog, memory context. Tests verifying marker placement.

### Phase 11 — Reflection-based extraction (~4 hours)
`skill_reflection.py`. Second pass in `_reflect()`. Canned LLM prompts in tests.

### Phase 12 — HTTP endpoints (~3 hours)
`/skills`, `/skills/pending`, `/skills/{name}/approve`, `/skills/{name}/reject`, `/skills/{name}/edit`, `/skills/{name}/compile`, `/skills/{name}/tests`, `/skills/{name}/history`.

### Phase 13 — Compile scaffolding (~3 hours)
`skill_compiler.py`. Not auto-triggered. One test verifying parseable Python output.

### Phase 14 — Frontend Skills panel (~6 hours)
`SkillsPanel.tsx`. Review cards, YAML editor, run history.

### Phase 15 — Directory bootstrap (~1 hour)
Ensure `~/.neuromancy/skills/{active,pending,quarantine}/` on startup.

**Total: ~47 hours, ~6 work days.** Critical path: Phases 1→2→3→4→5→6→7→8→9 (~28 hours sequential). Phases 10–15 can parallelize after Phase 9.

## Risk Register

### High severity

1. **LLM-authored skill running unauthorized commands.** Mitigated by mandatory user review gate, individual step approvals, `trust_this_skill=false` default, explicit `trusted_tools` for cron.
2. **Compiled Python skill containing hostile code.** Mitigated by subprocess isolation for all compiled execution, `setrlimit` memory/CPU caps, test-case equivalence gate before promotion. Latent in v1 (no auto-compile); revisit before wiring up v2 auto-compile.
3. **Approval bypass via skill-step nesting.** Baked out by the two-layer approval model in Phase 7.

### Medium severity

4. **Test capture disk growth.** Retention policy: keep last 100 per skill, rotate older into monthly zip archives, purge after 1 year. Implement in Phase 6.
5. **Pending skill pollution.** Cap at 20 pending candidates total, GC oldest on overflow.
6. **Context bloat from large tool outputs accumulating in skill runs.** No enforcement in v1; monitor.
7. **Prompt cache invalidation from frequent skill changes.** Addressed via Phase 10 segmentation.
8. **LLM over-spawning subagents** (deferred feature). Addressed via profiles (subagent Revision B) and tool description guidance.
9. **Memory migration data loss.** Mitigated by backing up `memory.json` before any writes to SQLite; row-count verification; integration test that round-trips a known JSON file.

### Low severity

10. **Compile scaffolding code rot from lack of exercise.** One always-run parseability test.
11. **Subprocess memory limits on Windows** (deferred). `setrlimit` unavailable; rely on timeout + document gap.
12. **Gateway contract drift** (deferred). Revisit when implementation lands.

## Testing Strategy

**Unit test coverage target: ≥85%** across new `skill_*` modules. Every new module ships with its test file. Fast, deterministic, mocks external services.

**Integration tests:**
- `test_query_loop_skills.py` — fake skill through real `QueryLoop` against mocked provider and plugins
- `test_memory_sqlite.py` — migration from JSON to SQLite, FTS5 correctness, API compat with existing tests
- Reflection extraction with canned transcripts
- Approval flow for skill steps with `requires_approval` tools

**Manual smoke test before declaring complete:**
1. Boot Electron, open a new session
2. Run a multi-step turn (search web → fetch pages → summarize)
3. Check Skills panel — a candidate appears in Pending
4. Click Approve
5. New session
6. "Use the research_topic skill to explore X"
7. Observe `run_skill` call, skill execution, returned result
8. Check `~/.neuromancy/skills/active/research_topic/tests/` for capture file
9. Check `memory.db` persists and loads correctly across daemon restart

## Deferred Items & v2 Candidates

These are explicitly out of scope for the session that implements this design, but their interfaces are defined here so the future work plugs in cleanly.

### From the skills system
- Auto-compile scheduler (wires the existing `compile_skill` endpoint to a background job)
- A/B testing of skill versions against captured test cases
- Loops and parallel steps in templates
- Sub-skill invocation (`invoke_skill` step type)
- Skills Hub (sharing, discovery)
- Stateful knowledge sidecars per skill
- Failure-recovery branches as a first-class step type (partially already expressible via `when: steps.X.error`)

### From the gateways
- Real Discord, Slack implementations
- WhatsApp (official or unofficial — requires explicit decision on operational tradeoffs)
- Signal (requires resolution of phone-number dependency)
- Multi-user authentication and session linking
- Webhook mode for Telegram
- Voice synthesis on outbound
- Group chat support

### From cron
- Complex schedule types
- Job chaining
- Conditional firing
- Fire-time parameters
- Distributed scheduling
- Webhook triggers

### From subagents
- Recursive spawning (depth > 1)
- Container isolation backends (Docker, Daytona, Modal)
- Subagent hibernation across daemon restarts
- Python script RPC for non-LLM callers
- Real-time subagent event streaming to parent UI

### From the daemon topology
- Server-hosted mode (daemon on a VPS, Electron as pure remote client)
- Auto-restart on crash via supervisor
- IPC between Electron and daemon beyond HTTP (native messaging?)

## Verification

This design is considered complete and ready for implementation when:

1. All 8 core design decisions from the Key Decisions table are locked
2. The `docs/plans/2026-04-13-tier1-architecture-design.md` file exists and is committed
3. The writing-plans skill has produced a detailed implementation plan for the skills system specifically (`docs/plans/2026-04-13-skills-system-plan.md`)
4. The implementation plan has checkpoint markers that allow suspension and resumption across sessions
5. The risk register above has been reviewed; mitigations are either baked into the implementation plan or explicitly accepted as v2 work

Implementation proceeds phase-by-phase from the plan. Each phase ends with its tests green before moving to the next. Manual smoke test at the end.
