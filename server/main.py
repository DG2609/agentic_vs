"""
Agentic IDE — Backend Server
Socket.IO + aiohttp replacing FastAPI.
"""
import json
import os
import sys
import uuid
import asyncio
import logging
import subprocess

import socketio
import aiohttp_cors
from aiohttp import web

# Add parent dir to path so we can import agent, models, config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from agent.graph import build_graph
from models.schemas import StreamChunk, ToolExecution
import config

logger = logging.getLogger(__name__)

# ── Socket.IO Server ───────────────────────────────────────
sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins='*',
    logger=False,
    engineio_logger=False,
)

# Global state
graph = None
active_tasks: dict[str, asyncio.Task] = {}  # thread_id -> running task


# ── Socket.IO Events ───────────────────────────────────────

@sio.event
async def connect(sid, environ):
    logger.info(f"Client connected: {sid}")
    await sio.emit('connected', {'status': 'ok', 'sid': sid}, to=sid)


@sio.event
async def disconnect(sid):
    logger.info(f"Client disconnected: {sid}")
    # Cancel any running tasks for this sid
    for tid, task in list(active_tasks.items()):
        if not task.done():
            task.cancel()


@sio.on('chat:message')
async def handle_chat_message(sid, data):
    """Handle incoming chat message from client."""

    message = data.get('message', '').strip()
    thread_id = data.get('thread_id', str(uuid.uuid4()))
    mode = data.get('mode', 'code')

    if not message:
        return

    if not graph:
        await sio.emit('error', {'content': 'Agent not initialized'}, to=sid)
        return

    # Cancel previous task for this thread if still running
    if thread_id in active_tasks and not active_tasks[thread_id].done():
        active_tasks[thread_id].cancel()
        await asyncio.sleep(0.1)

    # Run agent in background task
    task = asyncio.create_task(_run_agent(sid, thread_id, message, mode))
    active_tasks[thread_id] = task


@sio.on('chat:stop')
async def handle_stop(sid, data):
    """Stop a running generation."""
    thread_id = data.get('thread_id', '')
    if thread_id in active_tasks and not active_tasks[thread_id].done():
        active_tasks[thread_id].cancel()
        await sio.emit('done', {'stopped': True}, to=sid)
        logger.info(f"Generation stopped for thread: {thread_id}")


async def _run_agent(sid: str, thread_id: str, message: str, mode: str = "code"):
    """Run the LangGraph agent and stream results via Socket.IO."""
    try:
        graph_config = {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": 100,
        }
        input_state = {
            "messages": [HumanMessage(content=message)],
            "workspace": config.WORKSPACE_DIR,
            "mode": mode,
        }

        async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
            kind = event.get("event", "")


            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk:
                    content = getattr(chunk, "content", None)
                    
                    if content:
                        if isinstance(content, str):
                            await sio.emit('text', {'content': content}, to=sid)
                        elif isinstance(content, list):
                            # Handle mixed content (e.g. reasoning + text)
                            for block in content:
                                if isinstance(block, str):
                                    await sio.emit('text', {'content': block}, to=sid)
                                elif isinstance(block, dict) and "type" in block:
                                     if block["type"] == "text":
                                         await sio.emit('text', {'content': block["text"]}, to=sid)

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_args = event.get("data", {}).get("input", {})
                
                # reply_to_user → emit as text, not tool
                if tool_name == "reply_to_user":
                    msg = tool_args.get("message", "") if isinstance(tool_args, dict) else ""
                    if msg:
                        await sio.emit('text', {'content': msg}, to=sid)
                    continue
                
                tool_data = {
                    'tool_name': tool_name,
                    'tool_id': event.get("run_id", ""),
                    'status': 'running',
                    'arguments': tool_args,
                }
                await sio.emit('tool:start', tool_data, to=sid)

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                
                # reply_to_user → skip tool:end emission
                if tool_name == "reply_to_user":
                    continue
                
                output = event.get("data", {}).get("output", "")
                result_str = str(output.content) if hasattr(output, "content") else str(output)
                display = result_str[:2000] + (f"\n... ({len(result_str)} chars)" if len(result_str) > 2000 else "")
                tool_data = {
                    'tool_name': tool_name,
                    'tool_id': event.get("run_id", ""),
                    'status': 'completed',
                    'result': display,
                }
                await sio.emit('tool:end', tool_data, to=sid)
                
                if tool_name in ["file_edit", "file_write"]:
                    from agent.tools.file_ops import PENDING_DIFFS
                    for fp, diff_info in list(PENDING_DIFFS.items()):
                        rel_path = os.path.relpath(fp, config.WORKSPACE_DIR).replace("\\", "/")
                        await sio.emit('file:diff', {
                            'path': rel_path,
                            'original': diff_info['original'],
                            'modified': diff_info['modified']
                        }, to=sid)
                    PENDING_DIFFS.clear()

        # Check for title
        try:
            final_state = await graph.aget_state(graph_config)
            title = final_state.values.get("title", "")
            if title:
                await sio.emit('title', {'content': title}, to=sid)
        except Exception:
            pass

        await sio.emit('done', {'stopped': False}, to=sid)

    except asyncio.CancelledError:
        await sio.emit('done', {'stopped': True}, to=sid)
    except Exception as e:
        logger.error(f"Agent error: {e}", exc_info=True)
        await sio.emit('error', {'content': str(e)}, to=sid)
    finally:
        active_tasks.pop(thread_id, None)


# ── HTTP Routes (File API) ─────────────────────────────────

routes = web.RouteTableDef()


@routes.get('/api/files')
async def list_files(request: web.Request):
    """List files/dirs in workspace."""
    path = request.query.get('path', '')
    target = os.path.join(config.WORKSPACE_DIR, path) if path else config.WORKSPACE_DIR
    if not os.path.isdir(target):
        return web.json_response({"error": "Not a directory"}, status=404)

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
        return web.json_response({"error": "Permission denied"}, status=403)

    return web.json_response({"path": path, "items": items})


@routes.get('/api/file')
async def read_file(request: web.Request):
    """Read a file's content."""
    path = request.query.get('path', '')
    full = os.path.join(config.WORKSPACE_DIR, path)
    if not os.path.isfile(full):
        return web.json_response({"error": "File not found"}, status=404)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return web.json_response({"path": path, "content": content, "size": len(content)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@routes.post('/api/file')
async def write_file(request: web.Request):
    """Write/save a file."""
    payload = await request.json()
    path = payload.get("path", "")
    content = payload.get("content", "")
    full = os.path.join(config.WORKSPACE_DIR, path)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return web.json_response({"status": "ok", "path": path})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@routes.post('/api/file/revert')
async def revert_file(request: web.Request):
    """Revert a file to its original content after rejecting an AI edit."""
    payload = await request.json()
    path = payload.get("path", "")
    content = payload.get("content", "")
    full = os.path.join(config.WORKSPACE_DIR, path)
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return web.json_response({"status": "ok", "path": path})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


@routes.get('/api/workspace')
async def workspace_info(request: web.Request):
    """Return workspace path."""
    return web.json_response({"workspace": config.WORKSPACE_DIR})


@routes.post('/api/workspace')
async def set_workspace(request: web.Request):
    """Update workspace path."""
    payload = await request.json()
    new_path = payload.get("workspace", "").strip()
    if not new_path or not os.path.isdir(new_path):
        return web.json_response({"error": "Invalid or non-existent directory path"}, status=400)
    config.WORKSPACE_DIR = new_path
    return web.json_response({"status": "ok", "workspace": config.WORKSPACE_DIR})


@routes.get('/api/model')
async def model_info(request: web.Request):
    """Return current model and provider info."""
    provider = config.LLM_PROVIDER
    if provider == "ollama":
        model = config.OLLAMA_MODEL
    elif provider == "openai":
        model = config.OPENAI_MODEL
    elif provider == "anthropic":
        model = getattr(config, 'ANTHROPIC_MODEL', 'unknown')
    elif provider == "gemini":
        model = getattr(config, 'GEMINI_MODEL', 'unknown')
    else:
        model = "unknown"
    return web.json_response({"provider": provider, "model": model})


@routes.get('/api/search')
async def search_files(request: web.Request):
    """Search for text in workspace files."""
    query = request.query.get('q', '')
    if not query:
        return web.json_response({"results": []})

    results = []
    for root, dirs, files in os.walk(config.WORKSPACE_DIR):
        # Skip ignored dirs
        dirs[:] = [d for d in dirs if d not in {
            "__pycache__", ".git", "node_modules", ".venv", "venv", "env",
            "dist", "build", ".next", ".cache"
        }]
        for fname in files:
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, config.WORKSPACE_DIR).replace("\\", "/")
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if query.lower() in line.lower():
                            results.append({
                                "file": rel,
                                "line": i,
                                "content": line.strip()[:200],
                            })
                            if len(results) >= 50:
                                return web.json_response({"results": results})
            except (OSError, UnicodeDecodeError):
                continue
    return web.json_response({"results": results})


@routes.get('/api/git/status')
async def get_git_status(request: web.Request):
    """Run git status and return parsed file changes."""
    try:
        result = subprocess.run(
            ['git', 'status', '--porcelain'],
            cwd=config.WORKSPACE_DIR,
            capture_output=True,
            text=True,
            check=True
        )
        lines = result.stdout.strip().split('\n')
        files = []
        for line in lines:
            if not line:
                continue
            status = line[:2]
            path = line[3:]
            files.append({"status": status, "path": path})
            
        return web.json_response({"status": "ok", "files": files})
    except subprocess.CalledProcessError as e:
        stderr = getattr(e, "stderr", "") or ""
        stdout = getattr(e, "stdout", "") or ""
        msg = stdout + "\n" + stderr
        if "not a git repository" in msg.lower():
            return web.json_response({"error": "Not a git repository. Please initialize git with `git init`."}, status=200)
        return web.json_response({"error": f"Git command failed: {msg.strip()}"}, status=200)
    except FileNotFoundError:
        return web.json_response({"error": "Git is not installed on this system."}, status=200)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=200)


# ── App Factory ─────────────────────────────────────────────

async def create_app():
    """Create and configure the aiohttp application."""
    global graph

    # Ensure directories exist
    os.makedirs(config.WORKSPACE_DIR, exist_ok=True)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    app = web.Application()

    # Configure CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
        )
    })

    sio.attach(app)
    app.router.add_routes(routes)

    # Enable CORS on all added routes
    print("Configuring CORS for routes:")
    for route in list(app.router.routes()):
        # socket.io handles its own CORS, adding it again causes ValueError
        if hasattr(route.resource, 'canonical') and str(route.resource.canonical).startswith('/socket.io'):
            continue
            
        print(f"  - {route}")
        try:
            cors.add(route)
        except ValueError:
            print(f"    (Skipped: already has CORS/OPTIONS handler)")

    # Serve frontend static files (Next.js build output)
    frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'frontend', 'out')
    static_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static')

    if os.path.isdir(frontend_dir):
        # Serve Next.js static export
        app.router.add_static('/static/', frontend_dir)

        async def serve_index(request: web.Request):
            return web.FileResponse(os.path.join(frontend_dir, 'index.html'))
        app.router.add_get('/', serve_index)
    elif os.path.isdir(static_dir):
        # Fallback: serve legacy static files
        app.router.add_static('/static/', static_dir)

        async def serve_index(request: web.Request):
            return web.FileResponse(os.path.join(static_dir, 'index.html'))
        app.router.add_get('/', serve_index)

    # Initialize agent
    db_path = config.DATA_DIR / "graph.db"
    print(f"🚀 Building agent graph with SQLite persistence: {db_path}")

    checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(db_path))
    saver = await checkpointer_cm.__aenter__()
    graph = build_graph(saver)

    async def cleanup(app):
        await checkpointer_cm.__aexit__(None, None, None)
        print("👋 Shutting down...")

    app.on_cleanup.append(cleanup)

    print(f"✅ Agent ready! Workspace: {config.WORKSPACE_DIR}")
    print(f"⚡ Socket.IO server on http://{config.HOST}:{config.PORT}")

    return app


# ── Entry Point ─────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    web.run_app(create_app(), host=config.HOST, port=config.PORT)
