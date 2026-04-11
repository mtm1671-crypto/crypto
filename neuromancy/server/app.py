"""FastAPI server with WebSocket streaming."""

import json
import logging
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

from ..core.llm import OpenRouterProvider, POPULAR_MODELS
from ..core.memory import MemoryStore
from ..core.models import Session
from ..core.query_loop import QueryLoop
from ..plugins.base import PluginRegistry
from ..plugins.shell import ShellPlugin
from ..plugins.file_ops import FileOpsPlugin
from ..plugins.web_search import WebSearchPlugin
from ..plugins.fetch_page import FetchPagePlugin


def create_app() -> FastAPI:
    app = FastAPI(title="Neuromancy", version="0.1.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # State
    app.state.sessions: dict[str, Session] = {}
    app.state.api_key: str = os.environ.get("OPENROUTER_API_KEY", "")
    app.state.model: str = "anthropic/claude-sonnet-4"
    app.state.query_loops: dict[str, QueryLoop] = {}
    app.state.memory_store = MemoryStore()

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.get("/models")
    async def list_models():
        return {"models": [{"id": m[0], "name": m[1]} for m in POPULAR_MODELS]}

    @app.get("/sessions")
    async def list_sessions():
        return {
            "sessions": [
                {"id": str(s.id), "title": s.title, "model": s.model}
                for s in app.state.sessions.values()
            ]
        }

    @app.get("/memories")
    async def list_memories():
        return {"memories": [m.model_dump(mode="json") for m in app.state.memory_store.get_all()]}

    @app.delete("/memories/{memory_id}")
    async def delete_memory(memory_id: str):
        app.state.memory_store.remove(memory_id)
        return {"status": "ok"}

    @app.post("/config")
    async def update_config(data: dict):
        if "api_key" in data:
            app.state.api_key = data["api_key"]
        if "model" in data:
            app.state.model = data["model"]
        return {"status": "ok"}

    @app.post("/test-connection")
    async def test_connection(data: dict):
        """Send a tiny LLM call to verify the API key and model work."""
        api_key = data.get("api_key", app.state.api_key)
        model = data.get("model", app.state.model)
        if not api_key:
            return {"ok": False, "error": "No API key provided."}
        llm = OpenRouterProvider(api_key, model)
        try:
            resp = await llm.complete(
                [{"role": "user", "content": "Say ok"}],
            )
            choices = resp.get("choices", [])
            if not choices:
                return {"ok": False, "error": "Model returned no response."}
            text = choices[0].get("message", {}).get("content", "")
            return {"ok": True, "reply": text[:100], "model": model}
        except Exception as e:
            msg = str(e)
            # Extract useful part from httpx errors
            if "401" in msg:
                msg = "Invalid API key (401 Unauthorized)."
            elif "404" in msg:
                msg = f"Model '{model}' not found (404)."
            elif "429" in msg:
                msg = "Rate limited (429). Try again shortly."
            return {"ok": False, "error": msg}
        finally:
            await llm.close()

    @app.websocket("/ws/{session_id}")
    async def websocket_endpoint(websocket: WebSocket, session_id: str):
        await websocket.accept()

        # Get or create session
        if session_id not in app.state.sessions:
            app.state.sessions[session_id] = Session(
                id=session_id,
                model=app.state.model,
            )
        session = app.state.sessions[session_id]

        llm = None
        loop = None

        def _rebuild_loop():
            nonlocal llm, loop
            if llm:
                import asyncio
                asyncio.create_task(llm.close())
            llm = OpenRouterProvider(app.state.api_key, app.state.model)
            registry = PluginRegistry()
            registry.register(ShellPlugin())
            registry.register(FileOpsPlugin())
            registry.register(WebSearchPlugin())
            registry.register(FetchPagePlugin())
            loop = QueryLoop(llm, registry, memory_store=app.state.memory_store)
            app.state.query_loops[session_id] = loop

        if app.state.api_key:
            _rebuild_loop()

        try:
            while True:
                raw = await websocket.receive_text()

                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Malformed JSON from client: %s", raw[:200])
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "data": {"message": "Invalid JSON message."},
                        "session_id": session_id,
                    }) + "\n")
                    continue

                if msg.get("type") == "config":
                    if "api_key" in msg:
                        app.state.api_key = msg["api_key"]
                    if "model" in msg:
                        app.state.model = msg["model"]
                    _rebuild_loop()
                    continue

                if not app.state.api_key or not loop:
                    await websocket.send_text(json.dumps({
                        "type": "error",
                        "data": {"message": "No API key configured. Set one in Settings."},
                        "session_id": session_id,
                    }) + "\n")
                    continue

                if msg.get("type") == "message":
                    user_text = msg.get("content", "")
                    try:
                        async for event in loop.run(session, user_text):
                            await websocket.send_text(event.to_ndjson())
                    except Exception as e:
                        logger.exception("Unhandled error in query loop")
                        await websocket.send_text(json.dumps({
                            "type": "error",
                            "data": {"message": f"Internal error: {e}"},
                            "session_id": session_id,
                        }) + "\n")
                        await websocket.send_text(json.dumps({
                            "type": "done",
                            "data": {},
                            "session_id": session_id,
                        }) + "\n")

                elif msg.get("type") == "approval":
                    tool_call_id = msg.get("tool_call_id", "")
                    approved = msg.get("approved", False)
                    loop.resolve_approval(tool_call_id, approved)

        except WebSocketDisconnect:
            pass
        except Exception as e:
            try:
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "data": {"message": str(e)},
                    "session_id": session_id,
                }) + "\n")
            except Exception:
                pass
        finally:
            if llm:
                await llm.close()
            app.state.query_loops.pop(session_id, None)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("NEUROMANCY_PORT", "8000"))
    uvicorn.run("neuromancy.server.app:app", host="0.0.0.0", port=port, reload=True)
