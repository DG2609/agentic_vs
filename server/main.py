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
from uuid import uuid4

import socketio
import aiohttp_cors
from aiohttp import web

# Add parent dir to path so we can import agent, models, config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from agent.graph import build_graph
from agent.team.prompt_intel import PromptIntelTeam
from agent.team.quality_intel import QualityIntelTeam
from agent.plugins.manager import PluginManager, PluginError
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

# PromptIntel global state
intel_team: "PromptIntelTeam | None" = None
intel_task: "asyncio.Task | None" = None

# QualityIntel global state
quality_team: "QualityIntelTeam | None" = None
quality_task: "asyncio.Task | None" = None

# Plugin manager global
plugin_manager: "PluginManager | None" = None


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
    """Return current model and provider info (all 19 providers)."""
    provider = config.LLM_PROVIDER
    model_attr_map = {
        "ollama":            "OLLAMA_MODEL",
        "openai":            "OPENAI_MODEL",
        "anthropic":         "ANTHROPIC_MODEL",
        "google":            "GOOGLE_MODEL",
        "groq":              "GROQ_MODEL",
        "azure":             "AZURE_OPENAI_MODEL",
        "vllm":              "VLLM_MODEL",
        "llamacpp":          "LLAMACPP_MODEL",
        "lmstudio":          "LMSTUDIO_MODEL",
        "openai_compatible": "OPENAI_COMPATIBLE_MODEL",
        "vertex_ai":         "VERTEX_AI_MODEL",
        "github_copilot":    "GITHUB_COPILOT_MODEL",
        "aws_bedrock":       "BEDROCK_MODEL",
        "mistral":           "MISTRAL_MODEL",
        "together":          "TOGETHER_MODEL",
        "fireworks":         "FIREWORKS_MODEL",
        "deepseek":          "DEEPSEEK_MODEL",
        "perplexity":        "PERPLEXITY_MODEL",
        "xai":               "XAI_MODEL",
    }
    attr = model_attr_map.get(provider, "")
    model = getattr(config, attr, "") if attr else ""
    return web.json_response({
        "provider": provider,
        "model": model or "unknown",
        "is_local": provider in {"ollama", "vllm", "llamacpp", "lmstudio", "openai_compatible"},
    })


_MODEL_ATTR_MAP = {
    "ollama":            ("OLLAMA_MODEL",            "local"),
    "openai":            ("OPENAI_MODEL",            "cloud"),
    "anthropic":         ("ANTHROPIC_MODEL",         "cloud"),
    "google":            ("GOOGLE_MODEL",            "cloud"),
    "groq":              ("GROQ_MODEL",              "cloud"),
    "azure":             ("AZURE_OPENAI_MODEL",      "cloud"),
    "vllm":              ("VLLM_MODEL",              "local"),
    "llamacpp":          ("LLAMACPP_MODEL",          "local"),
    "lmstudio":          ("LMSTUDIO_MODEL",          "local"),
    "openai_compatible": ("OPENAI_COMPATIBLE_MODEL", "local"),
    "vertex_ai":         ("VERTEX_AI_MODEL",         "cloud"),
    "github_copilot":    ("GITHUB_COPILOT_MODEL",    "cloud"),
    "aws_bedrock":       ("BEDROCK_MODEL",           "cloud"),
    "mistral":           ("MISTRAL_MODEL",           "cloud"),
    "together":          ("TOGETHER_MODEL",          "cloud"),
    "fireworks":         ("FIREWORKS_MODEL",         "cloud"),
    "deepseek":          ("DEEPSEEK_MODEL",          "cloud"),
    "perplexity":        ("PERPLEXITY_MODEL",        "cloud"),
    "xai":               ("XAI_MODEL",              "cloud"),
}

_PROVIDER_LABELS = {
    "ollama": "Ollama", "openai": "OpenAI", "anthropic": "Anthropic",
    "google": "Google Gemini", "groq": "Groq", "azure": "Azure OpenAI",
    "vllm": "vLLM", "llamacpp": "llama.cpp", "lmstudio": "LM Studio",
    "openai_compatible": "OpenAI-compatible",
    "vertex_ai": "Vertex AI (GCP)", "github_copilot": "GitHub Copilot",
    "aws_bedrock": "AWS Bedrock", "mistral": "Mistral AI",
    "together": "Together AI", "fireworks": "Fireworks AI",
    "deepseek": "DeepSeek", "perplexity": "Perplexity AI", "xai": "xAI / Grok",
}


@routes.get('/api/providers')
async def list_providers(request: web.Request):
    """List all 19 providers with their current model and type."""
    providers = []
    for pid, (attr, ptype) in _MODEL_ATTR_MAP.items():
        model = getattr(config, attr, "") or ""
        providers.append({
            "id": pid,
            "label": _PROVIDER_LABELS.get(pid, pid),
            "type": ptype,
            "model": model,
        })
    return web.json_response({
        "current": config.LLM_PROVIDER,
        "providers": providers,
    })


@routes.post('/api/provider')
async def set_provider(request: web.Request):
    """Switch the active LLM provider (and optionally model) at runtime."""
    payload = await request.json()
    provider = payload.get("provider", "").strip()
    model = payload.get("model", "").strip()

    if provider not in _MODEL_ATTR_MAP:
        return web.json_response(
            {"error": f"Unknown provider '{provider}'. Valid: {sorted(_MODEL_ATTR_MAP)}"},
            status=400,
        )

    config.LLM_PROVIDER = provider
    attr, ptype = _MODEL_ATTR_MAP[provider]
    if model:
        setattr(config, attr, model)

    current_model = getattr(config, attr, "") or "unknown"
    return web.json_response({
        "status": "ok",
        "provider": provider,
        "label": _PROVIDER_LABELS.get(provider, provider),
        "model": current_model,
        "is_local": ptype == "local",
    })


_KNOWN_MODELS: dict[str, list[str]] = {
    "openai":         ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4", "gpt-3.5-turbo", "o1", "o1-mini", "o3-mini"],
    "anthropic":      ["claude-opus-4-6", "claude-sonnet-4-6", "claude-haiku-4-5-20251001",
                       "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"],
    "google":         ["gemini-2.0-flash", "gemini-2.0-flash-thinking-exp",
                       "gemini-1.5-pro", "gemini-1.5-flash", "gemini-1.5-flash-8b"],
    "groq":           ["llama-3.3-70b-versatile", "llama-3.1-8b-instant",
                       "mixtral-8x7b-32768", "gemma2-9b-it"],
    "azure":          ["gpt-4o", "gpt-4-turbo", "gpt-35-turbo"],
    "vertex_ai":      ["gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash"],
    "github_copilot": ["gpt-4o", "claude-3.5-sonnet", "o1-preview", "o3-mini"],
    "aws_bedrock":    ["anthropic.claude-3-5-sonnet-20241022-v2:0",
                       "anthropic.claude-3-haiku-20240307-v1:0",
                       "amazon.titan-text-express-v1", "meta.llama3-70b-instruct-v1:0"],
    "mistral":        ["mistral-large-latest", "mistral-medium-latest", "mistral-small-latest",
                       "codestral-latest", "open-mistral-nemo"],
    "together":       ["meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
                       "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",
                       "mistralai/Mixtral-8x7B-Instruct-v0.1",
                       "Qwen/Qwen2.5-72B-Instruct-Turbo"],
    "fireworks":      ["accounts/fireworks/models/firefunction-v2",
                       "accounts/fireworks/models/llama-v3p1-70b-instruct",
                       "accounts/fireworks/models/mixtral-8x7b-instruct"],
    "deepseek":       ["deepseek-chat", "deepseek-reasoner"],
    "perplexity":     ["llama-3.1-sonar-huge-128k-online",
                       "llama-3.1-sonar-large-128k-online",
                       "llama-3.1-sonar-small-128k-online"],
    "xai":            ["grok-2", "grok-beta", "grok-vision-beta"],
}


@routes.get('/api/models')
async def list_models(request: web.Request):
    """Return known/available models for a provider."""
    provider = request.query.get('provider', config.LLM_PROVIDER)
    models = list(_KNOWN_MODELS.get(provider, []))

    # For Ollama: try dynamic fetch from local API
    if provider == "ollama" and not models:
        try:
            import aiohttp as _aiohttp
            async with _aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:11434/api/tags",
                    timeout=_aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        models = [m["name"] for m in data.get("models", [])]
        except Exception:
            pass

    attr, _ = _MODEL_ATTR_MAP.get(provider, ("", ""))
    current = (getattr(config, attr, "") or "") if attr else ""

    return web.json_response({
        "provider": provider,
        "current": current,
        "models": models,
    })


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


@routes.get('/health')
async def health(request: web.Request):
    """Health check endpoint — returns server status, tool count and current model."""
    provider = config.LLM_PROVIDER
    if provider == "ollama":
        model = config.OLLAMA_MODEL
    elif provider == "openai":
        model = config.OPENAI_MODEL
    elif provider == "anthropic":
        model = getattr(config, "ANTHROPIC_MODEL", "unknown")
    elif provider == "google":
        model = getattr(config, "GOOGLE_MODEL", "unknown")
    elif provider == "groq":
        model = getattr(config, "GROQ_MODEL", "unknown")
    elif provider == "azure":
        model = getattr(config, "AZURE_OPENAI_MODEL", "unknown")
    else:
        model = "unknown"

    try:
        from agent.graph import ALL_TOOLS
        tool_count = len(ALL_TOOLS)
    except Exception:
        tool_count = 0

    return web.json_response({"status": "ok", "tools": tool_count, "model": model})


@routes.post('/stream')
async def stream_endpoint(request: web.Request):
    """SSE streaming endpoint — runs the agent and streams token/tool events."""
    try:
        body = await request.json()
    except Exception:
        return web.Response(status=400, text="Invalid JSON body")

    prompt = body.get("prompt", "").strip()
    if not prompt:
        return web.Response(status=400, text="'prompt' is required")

    thread_id = body.get("thread_id") or str(uuid4())
    workspace = body.get("workspace") or config.WORKSPACE_DIR

    if not graph:
        resp = web.StreamResponse(status=503)
        resp.content_type = "text/event-stream"
        await resp.prepare(request)
        err = json.dumps({"type": "error", "message": "Agent not initialized"})
        await resp.write(f"data: {err}\n\n".encode())
        await resp.write_eof()
        return resp

    resp = web.StreamResponse(status=200)
    resp.content_type = "text/event-stream"
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    await resp.prepare(request)

    graph_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100,
    }
    input_state = {
        "messages": [HumanMessage(content=prompt)],
        "workspace": workspace,
    }

    try:
        async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
            kind = event.get("event", "")

            if kind == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk:
                    content = getattr(chunk, "content", None)
                    if isinstance(content, str) and content:
                        payload = json.dumps({"type": "token", "content": content})
                        await resp.write(f"data: {payload}\n\n".encode())
                    elif isinstance(content, list):
                        for block in content:
                            text = ""
                            if isinstance(block, str):
                                text = block
                            elif isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                            if text:
                                payload = json.dumps({"type": "token", "content": text})
                                await resp.write(f"data: {payload}\n\n".encode())

            elif kind == "on_tool_start":
                tool_name = event.get("name", "unknown")
                tool_args = event.get("data", {}).get("input", {})
                payload = json.dumps({"type": "tool_start", "name": tool_name, "args": tool_args})
                await resp.write(f"data: {payload}\n\n".encode())

            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                output = event.get("data", {}).get("output", "")
                result_str = str(output.content) if hasattr(output, "content") else str(output)
                display = result_str[:2000] + (f"\n...({len(result_str)} chars)" if len(result_str) > 2000 else "")
                payload = json.dumps({"type": "tool_end", "name": tool_name, "result": display})
                await resp.write(f"data: {payload}\n\n".encode())

        done_payload = json.dumps({"type": "done", "thread_id": thread_id})
        await resp.write(f"data: {done_payload}\n\n".encode())

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Stream error: {e}", exc_info=True)
        err_payload = json.dumps({"type": "error", "message": str(e)})
        try:
            await resp.write(f"data: {err_payload}\n\n".encode())
        except Exception:
            pass

    await resp.write_eof()
    return resp


@routes.options('/stream')
async def stream_preflight(request: web.Request):
    """CORS preflight for /stream."""
    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, x-api-key",
        },
    )


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


# ── PromptIntel Routes ─────────────────────────────────────

@routes.get('/api/intel/status')
async def intel_status(request: web.Request):
    """Return current PromptIntelTeam status."""
    if intel_team is None:
        return web.json_response({"running": False, "round": 0, "overall_score": 0,
                                  "total_improvements": 0})
    return web.json_response(await intel_team.get_status())


@routes.post('/api/intel/start')
async def intel_start(request: web.Request):
    """Start the PromptIntelTeam if not already running."""
    global intel_team, intel_task
    if intel_team is not None and intel_team.running:
        return web.json_response({"status": "already_running", "round": intel_team.round})

    intel_team = PromptIntelTeam(sio=sio)
    intel_task = asyncio.create_task(intel_team.run_forever())
    logger.info("PromptIntelTeam started via API")
    return web.json_response({"status": "started"})


@routes.post('/api/intel/stop')
async def intel_stop(request: web.Request):
    """Stop the running PromptIntelTeam."""
    global intel_team, intel_task
    if intel_task is not None and not intel_task.done():
        intel_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(intel_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if intel_team is not None:
        intel_team.running = False
    logger.info("PromptIntelTeam stopped via API")
    return web.json_response({"status": "stopped"})


# ── QualityIntel Routes ────────────────────────────────────

@routes.get('/api/quality/status')
async def quality_status(request: web.Request):
    """Return current QualityIntelTeam status."""
    if quality_team is None:
        return web.json_response({
            "running": False, "converged": False, "round": 0,
            "overall_score": 0, "total_improvements": 0, "scores": {},
        })
    return web.json_response(await quality_team.get_status())


@routes.post('/api/quality/start')
async def quality_start(request: web.Request):
    """Start the QualityIntelTeam if not already running."""
    global quality_team, quality_task
    if quality_team is not None and quality_team.running:
        return web.json_response({"status": "already_running", "round": quality_team.round})

    quality_team = QualityIntelTeam(sio=sio)
    quality_task = asyncio.create_task(quality_team.run_until_converged())
    logger.info("QualityIntelTeam started via API")
    return web.json_response({"status": "started"})


@routes.post('/api/quality/stop')
async def quality_stop(request: web.Request):
    """Stop the running QualityIntelTeam."""
    global quality_team, quality_task
    if quality_task is not None and not quality_task.done():
        quality_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(quality_task), timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    if quality_team is not None:
        quality_team.running = False
    logger.info("QualityIntelTeam stopped via API")
    return web.json_response({"status": "stopped"})


# ── Plugin Routes ──────────────────────────────────────────

def _meta_to_dict(m) -> dict:
    return {
        "name": m.name, "version": m.version, "url": m.url, "sha256": m.sha256,
        "author": m.author, "description": m.description, "category": m.category,
        "tags": list(m.tags), "permissions": list(m.permissions),
        "tool_count": m.tool_count, "size_bytes": m.size_bytes,
        "signature": m.signature,
    }


def _installed_to_dict(r) -> dict:
    return {
        "name": r.name, "version": r.version, "status": r.status,
        "score": r.score, "permissions": list(r.permissions),
        "install_path": r.install_path,
        "installed_at": r.installed_at, "last_audited_at": r.last_audited_at,
        "last_error": r.last_error,
    }


def _report_to_dict(rep) -> dict:
    return {
        "score": rep.score,
        "blocked": rep.blocked,
        "issues": [
            {"rule": i.rule, "message": i.message, "severity": i.severity,
             "file": i.file, "line": i.line}
            for i in rep.issues
        ],
        "blockers": [
            {"rule": i.rule, "message": i.message, "severity": i.severity,
             "file": i.file, "line": i.line}
            for i in rep.blockers
        ],
    }


@routes.get('/api/plugins')
async def plugins_list(request: web.Request):
    """List installed plugins."""
    if plugin_manager is None:
        return web.json_response({"plugins": []})
    rows = plugin_manager.list_installed()
    return web.json_response({"plugins": [_installed_to_dict(r) for r in rows]})


@routes.get('/api/plugins/report')
async def plugins_report(request: web.Request):
    """Return the persisted audit report for an installed plugin (404 if none)."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    name = request.query.get("name", "").strip()
    if not name:
        return web.json_response({"error": "'name' query param required"}, status=400)
    report = plugin_manager.registry.get_raw_report(name)
    if report is None:
        return web.json_response({"error": f"no report stored for {name}"}, status=404)
    return web.json_response(report)


@routes.get('/api/plugins/search')
async def plugins_search(request: web.Request):
    """Search the plugin hub by query (and optional category)."""
    if plugin_manager is None:
        return web.json_response({"results": []})
    q = request.query.get("q", "").strip()
    category = request.query.get("category") or None
    try:
        metas = await plugin_manager.search(q, category=category)
        return web.json_response({"results": [_meta_to_dict(m) for m in metas]})
    except Exception as e:
        logger.error(f"Plugin search error: {e}", exc_info=True)
        return web.json_response({"error": str(e)}, status=500)


@routes.get('/api/plugins/inspect')
async def plugins_inspect(request: web.Request):
    """Fetch a single hub entry by name."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    name = request.query.get("name", "").strip()
    if not name:
        return web.json_response({"error": "'name' query param required"}, status=400)
    meta = await plugin_manager.inspect(name)
    if meta is None:
        return web.json_response({"error": f"plugin not found: {name}"}, status=404)
    return web.json_response(_meta_to_dict(meta))


@routes.post('/api/plugins/audit')
async def plugins_audit(request: web.Request):
    """Run a dry-run audit without installing."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "'name' is required"}, status=400)
    await sio.emit("plugins:audit_start", {"name": name})
    try:
        report = await plugin_manager.audit(name, version=payload.get("version"))
        data = _report_to_dict(report)
        await sio.emit("plugins:audit_done", {"name": name, "report": data})
        return web.json_response(data)
    except PluginError as e:
        await sio.emit("plugins:error", {"op": "audit", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Plugin audit error: {e}", exc_info=True)
        await sio.emit("plugins:error", {"op": "audit", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=500)


@routes.post('/api/plugins/install')
async def plugins_install(request: web.Request):
    """Audit + install a plugin."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "'name' is required"}, status=400)
    version = payload.get("version")
    permissions = payload.get("permissions") or []
    force = bool(payload.get("force", False))
    if not isinstance(permissions, list):
        return web.json_response({"error": "'permissions' must be a list"}, status=400)
    try:
        installed = await plugin_manager.install(
            name, version=version, permissions=permissions, force=force,
        )
        data = _installed_to_dict(installed)
        await sio.emit("plugins:installed", data)
        return web.json_response(data)
    except PluginError as e:
        await sio.emit("plugins:error", {"op": "install", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Plugin install error: {e}", exc_info=True)
        await sio.emit("plugins:error", {"op": "install", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=500)


@routes.post('/api/plugins/uninstall')
async def plugins_uninstall(request: web.Request):
    """Uninstall a plugin by name."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "'name' is required"}, status=400)
    try:
        await plugin_manager.uninstall(name)
        await sio.emit("plugins:uninstalled", {"name": name})
        return web.json_response({"status": "ok", "name": name})
    except Exception as e:
        logger.error(f"Plugin uninstall error: {e}", exc_info=True)
        await sio.emit("plugins:error", {"op": "uninstall", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=500)


@routes.post('/api/plugins/reload')
async def plugins_reload(request: web.Request):
    """Reload the runtime sandbox for an installed plugin."""
    if plugin_manager is None:
        return web.json_response({"error": "plugin manager unavailable"}, status=503)
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "'name' is required"}, status=400)
    try:
        tools = await plugin_manager.reload(name)
        return web.json_response({"status": "ok", "name": name,
                                  "tools": [t.name for t in tools]})
    except PluginError as e:
        await sio.emit("plugins:error", {"op": "reload", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=400)
    except Exception as e:
        logger.error(f"Plugin reload error: {e}", exc_info=True)
        await sio.emit("plugins:error", {"op": "reload", "name": name, "error": str(e)})
        return web.json_response({"error": str(e)}, status=500)


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

    # Initialize Plugin Manager
    global plugin_manager
    _plugins_data = config.DATA_DIR / "plugins"
    _plugins_data.mkdir(parents=True, exist_ok=True)
    hub_index_url = getattr(config, "PLUGIN_HUB_INDEX_URL",
                            os.environ.get("SHADOWDEV_PLUGIN_HUB_INDEX",
                                           "https://plugins.shadowdev.dev/index.json"))
    _hub_pubkey_env = os.environ.get("SHADOWDEV_PLUGIN_HUB_PUBKEY", "").strip()
    _hub_pubkey: bytes | None = None
    if _hub_pubkey_env:
        try:
            import binascii as _bx
            try:
                _hub_pubkey = _bx.unhexlify(_hub_pubkey_env)
            except (ValueError, _bx.Error):
                import base64 as _b64
                _hub_pubkey = _b64.b64decode(_hub_pubkey_env, validate=True)
            if len(_hub_pubkey) != 32:
                raise ValueError(f"expected 32-byte ed25519 pubkey, got {len(_hub_pubkey)} bytes")
        except Exception as e:
            logger.warning(f"SHADOWDEV_PLUGIN_HUB_PUBKEY invalid: {e} — signature verification disabled")
            _hub_pubkey = None
    try:
        plugin_manager = PluginManager(
            hub_index_url=hub_index_url,
            install_root=_plugins_data / "installed",
            temp_root=_plugins_data / "tmp",
            db_path=_plugins_data / "registry.db",
            cache_dir=_plugins_data / "cache",
            hub_public_key=_hub_pubkey,
        )
        # Best-effort startup sweep
        sweep = getattr(plugin_manager, "startup_sweep", None)
        if sweep is not None:
            try:
                sweep()
            except Exception as e:
                logger.warning(f"Plugin startup sweep failed: {e}")
        # Bind as singleton for the agent graph adapter
        try:
            from agent.plugins.manager import set_singleton
            set_singleton(plugin_manager)
        except Exception:
            pass
        print(f"🔌 PluginManager ready (hub={hub_index_url})")
    except Exception as e:
        logger.error(f"Failed to init PluginManager: {e}", exc_info=True)
        plugin_manager = None

    # Initialize agent
    db_path = config.DATA_DIR / "graph.db"
    print(f"🚀 Building agent graph with SQLite persistence: {db_path}")

    checkpointer_cm = AsyncSqliteSaver.from_conn_string(str(db_path))
    saver = await checkpointer_cm.__aenter__()
    graph = build_graph(saver)

    async def cleanup(app):
        global intel_task, intel_team, quality_task, quality_team
        # Stop intel team on shutdown
        if intel_task is not None and not intel_task.done():
            intel_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(intel_task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if intel_team is not None:
            intel_team.running = False
        # Stop quality team on shutdown
        if quality_task is not None and not quality_task.done():
            quality_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(quality_task), timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if quality_team is not None:
            quality_team.running = False
        # Stop all plugin sandboxes on shutdown
        if plugin_manager is not None:
            for pname in list(plugin_manager._sandboxes.keys()):
                try:
                    await plugin_manager.unload(pname)
                except Exception as e:
                    logger.warning(f"Failed to unload plugin {pname}: {e}")
        await checkpointer_cm.__aexit__(None, None, None)
        print("👋 Shutting down...")

    app.on_cleanup.append(cleanup)

    print(f"✅ Agent ready! Workspace: {config.WORKSPACE_DIR}")
    print(f"⚡ Socket.IO server on http://{config.HOST}:{config.PORT}")

    # Auto-start the PromptIntelTeam
    global intel_team, intel_task, quality_team, quality_task
    intel_team = PromptIntelTeam(sio=sio)
    intel_task = asyncio.create_task(intel_team.run_forever())
    print("🔍 PromptIntelTeam started — mining CC source for prompt improvements")

    # Auto-start the QualityIntelTeam
    quality_team = QualityIntelTeam(sio=sio)
    quality_task = asyncio.create_task(quality_team.run_until_converged())
    print("🧪 QualityIntelTeam started — scanning project quality")

    return app


# ── Entry Point ─────────────────────────────────────────────

if __name__ == "__main__":
    # On Windows the default stdout codec (cp1252) can't encode emoji — force UTF-8
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    web.run_app(create_app(), host=config.HOST, port=config.PORT)
