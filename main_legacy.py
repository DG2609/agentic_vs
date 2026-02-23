"""
Main entry point — FastAPI server with WebSocket chat + REST file APIs.
"""
import json
import os
import uuid
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from agent.graph import build_graph
from models.schemas import StreamChunk, ToolExecution
import config


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    db_path = config.DATA_DIR / "graph.db"
    
    print(f"🚀 Building agent graph with SQLite persistence: {db_path}")
    
    # Initialize async checkpointer
    async with AsyncSqliteSaver.from_conn_string(str(db_path)) as checkpointer:
        # Build graph with checkpointer
        app.state.graph = build_graph(checkpointer)
        print(f"✅ Agent ready! Workspace: {config.WORKSPACE_DIR}")
        yield
    
    print("👋 Shutting down...")


app = FastAPI(title="Agentic IDE", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(config.STATIC_DIR / "index.html"))


# ── File API ────────────────────────────────────────────────

@app.get("/api/files")
async def list_files(path: str = ""):
    """List files/dirs in workspace. Returns tree-friendly JSON."""
    target = os.path.join(config.WORKSPACE_DIR, path) if path else config.WORKSPACE_DIR
    if not os.path.isdir(target):
        return JSONResponse({"error": "Not a directory"}, status_code=404)

    ignore = {"__pycache__", ".git", "node_modules", ".venv", "venv", "env",
              "dist", "build", ".next", ".cache", ".tox", "target", ".idea", ".vscode"}
    items = []
    try:
        for entry in sorted(os.listdir(target)):
            if entry in ignore or entry.startswith('.'):
                continue
            full = os.path.join(target, entry)
            rel = os.path.relpath(full, config.WORKSPACE_DIR).replace("\\", "/")
            is_dir = os.path.isdir(full)
            item = {"name": entry, "path": rel, "is_dir": is_dir}
            if not is_dir:
                try:
                    item["size"] = os.path.getsize(full)
                except OSError:
                    item["size"] = 0
            items.append(item)
    except PermissionError:
        return JSONResponse({"error": "Permission denied"}, status_code=403)

    return {"path": path, "items": items}


@app.get("/api/file")
async def read_file(path: str):
    """Read a file's content."""
    full = os.path.join(config.WORKSPACE_DIR, path)
    if not os.path.isfile(full):
        return JSONResponse({"error": "File not found"}, status_code=404)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return {"path": path, "content": content, "size": len(content)}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/file")
async def write_file(payload: dict):
    """Write/save a file."""
    path = payload.get("path", "")
    content = payload.get("content", "")
    full = os.path.join(config.WORKSPACE_DIR, path)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "ok", "path": path}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/workspace")
async def workspace_info():
    """Return workspace path."""
    return {"workspace": config.WORKSPACE_DIR}


# ── WebSocket Chat ──────────────────────────────────────────

@app.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    await websocket.accept()
    thread_id = str(uuid.uuid4())

    try:
        while True:
            data = await websocket.receive_text()
            payload = json.loads(data)
            user_message = payload.get("message", "")
            thread_id = payload.get("thread_id", thread_id)

            if not user_message:
                continue

            # Access graph from app state
            graph = websocket.app.state.graph
            graph_config = {
                "configurable": {"thread_id": thread_id},
                "recursion_limit": 100,
            }
            input_state = {
                "messages": [HumanMessage(content=user_message)],
                "workspace": config.WORKSPACE_DIR,
            }

            async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
                kind = event.get("event", "")

                if kind == "on_chat_model_stream":
                    chunk = event.get("data", {}).get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        if isinstance(chunk.content, str):
                            await _send(websocket, StreamChunk(type="text", content=chunk.content))

                elif kind == "on_tool_start":
                    await _send(websocket, StreamChunk(
                        type="tool_start",
                        tool=ToolExecution(
                            tool_name=event.get("name", "unknown"),
                            tool_id=event.get("run_id", ""),
                            status="running",
                            arguments=event.get("data", {}).get("input", {}),
                        ),
                    ))

                elif kind == "on_tool_end":
                    output = event.get("data", {}).get("output", "")
                    result_str = str(output.content) if hasattr(output, "content") else str(output)
                    display = result_str[:2000] + (f"\n... ({len(result_str)} chars)" if len(result_str) > 2000 else "")
                    await _send(websocket, StreamChunk(
                        type="tool_end",
                        tool=ToolExecution(
                            tool_name=event.get("name", "unknown"),
                            tool_id=event.get("run_id", ""),
                            status="completed",
                            result=display,
                        ),
                    ))

            # Check if title was generated
            try:
                final_state = await graph.aget_state(graph_config)
                title = final_state.values.get("title", "")
                if title:
                    await _send(websocket, StreamChunk(type="title", content=title))
            except Exception:
                pass

            await _send(websocket, StreamChunk(type="done"))

    except WebSocketDisconnect:
        print(f"Client disconnected (thread: {thread_id})")
    except Exception as e:
        try:
            await _send(websocket, StreamChunk(type="error", content=str(e)))
        except Exception:
            pass
        print(f"Error in WebSocket: {e}")


async def _send(ws: WebSocket, chunk: StreamChunk):
    await ws.send_text(chunk.model_dump_json())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=True,
        reload_excludes=["data/*", "*.db", "env/*", "__pycache__/*", "workspace/*"],
    )
