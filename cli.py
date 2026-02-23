import sys
import os
import uuid
import asyncio
import re
import time

from rich.console import Console
from rich.markdown import Markdown
from rich.live import Live
from rich.status import Status
from rich.syntax import Syntax
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from langchain_core.messages import HumanMessage

# Ensure project root is in PYTHONPATH
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
from agent.graph import build_graph
from langgraph.checkpoint.memory import MemorySaver

# Compile graph with in-memory persistence
graph = build_graph(checkpointer=MemorySaver())

console = Console()
style = Style.from_dict({'prompt': 'bold cyan'})

# Global state
current_agent_mode = "planner"

# ── Tool display helpers ────────────────────────────────────
TOOL_ICONS = {
    "file_read": ("→", "Read"),
    "batch_read": ("→", "Batch Read"),
    "file_list": ("→", "List"),
    "file_edit": ("←", "Edit"),
    "file_write": ("←", "Write"),
    "terminal_exec": ("$", "Run"),
    "grep_search": ("✱", "Grep"),
    "glob_search": ("✱", "Glob"),
    "code_search": ("✱", "Search"),
    "code_analyze": ("◈", "Analyze"),
    "semantic_search": ("◈", "Semantic"),
    "index_codebase": ("◈", "Index"),
    "webfetch": ("⊕", "Fetch"),
    "handoff_to_coder": ("🔀", "Handoff → Coder"),
    "handoff_to_planner": ("🔀", "Handoff → Planner"),
    "task_explore": ("🔍", "Explore"),
    "task_general": ("⚡", "Subagent"),
    "reply_to_user": ("💬", "Reply"),
}
for lsp_name in ["lsp_definition", "lsp_references", "lsp_hover", "lsp_symbols",
                  "lsp_diagnostics", "lsp_go_to_definition", "lsp_find_references"]:
    TOOL_ICONS[lsp_name] = ("◇", f"LSP {lsp_name.replace('lsp_', '')}")

MODE_COLORS = {"planner": "yellow", "coder": "cyan", "doc": "green"}


def _tool_display(tool_name: str, args: dict) -> tuple:
    """Return (icon, title) for a tool call."""
    icon, base = TOOL_ICONS.get(tool_name, ("⚙", tool_name))
    if tool_name in ("file_read", "batch_read", "file_list"):
        target = args.get("file_path", args.get("absolute_path", args.get("directory", "")))
        return icon, f"{base} {_short_path(target)}"
    elif tool_name in ("file_edit", "file_write"):
        target = args.get("file_path", args.get("TargetFile", ""))
        return icon, f"{base} {_short_path(target)}"
    elif tool_name == "terminal_exec":
        return icon, args.get("command", "command")
    elif tool_name in ("grep_search", "glob_search", "code_search"):
        q = args.get("pattern", args.get("query", args.get("search_term", "")))
        return icon, f'{base} "{q}"'
    elif tool_name.startswith("lsp"):
        sym = args.get("symbol", args.get("file_path", ""))
        return icon, f'{base} "{sym}"'
    elif tool_name in ("task_explore", "task_general"):
        task_desc = str(args.get("task", ""))[:60]
        return icon, f'{base}: "{task_desc}"'
    else:
        return icon, f"{base}"


def _short_path(p: str) -> str:
    if not p: return ""
    parts = p.replace("\\", "/").split("/")
    return "…/" + "/".join(parts[-2:]) if len(parts) > 2 else p


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 1: return f"{seconds*1000:.0f}ms"
    elif seconds < 60: return f"{seconds:.1f}s"
    else: return f"{seconds/60:.1f}m"


# ── Bottom Toolbar ──────────────────────────────────────────
def bottom_toolbar():
    mode = current_agent_mode.upper()
    color = MODE_COLORS.get(current_agent_mode, "white")
    model = config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.OPENAI_MODEL
    return HTML(
        f' <b>ShadowDev</b>'
        f' │ <style bg="ansiyellow" fg="black"> Alt+1 Plan </style>'
        f' <style bg="ansicyan" fg="black"> Alt+2 Code </style>'
        f' <style bg="ansigreen" fg="black"> Alt+3 Doc </style>'
        f' │ Mode: <style bg="ansi{color}" fg="black"> {mode} </style>'
        f' │ Model: <b>{model}</b>'
    )


# ── Keybindings ─────────────────────────────────────────────
bindings = KeyBindings()

@bindings.add('escape', '1')
def _(event):
    global current_agent_mode
    current_agent_mode = "planner"
    event.app.invalidate()

@bindings.add('escape', '2')
def _(event):
    global current_agent_mode
    current_agent_mode = "coder"
    event.app.invalidate()

@bindings.add('escape', '3')
def _(event):
    global current_agent_mode
    current_agent_mode = "doc"
    event.app.invalidate()


# ── Chat loop ───────────────────────────────────────────────
async def chat_loop():
    global current_agent_mode
    session = PromptSession(style=style, bottom_toolbar=bottom_toolbar, key_bindings=bindings)
    thread_id = str(uuid.uuid4())
    
    # ── Styled Banner ───────────────────────────────────────
    model = config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.OPENAI_MODEL
    provider = config.LLM_PROVIDER.upper()
    workspace = os.path.basename(os.path.abspath(config.WORKSPACE_DIR))
    
    console.print()
    console.print("[bold magenta]  ╭──────────────────────────────────────╮[/bold magenta]")
    console.print("[bold magenta]  │[/bold magenta]     [bold white]⚡ ShadowDev CLI v2.0 ⚡[/bold white]         [bold magenta]│[/bold magenta]")
    console.print("[bold magenta]  │[/bold magenta]     [dim]AI-Powered Coding Assistant[/dim]      [bold magenta]│[/bold magenta]")
    console.print("[bold magenta]  ╰──────────────────────────────────────╯[/bold magenta]")
    console.print()
    console.print(f"  [bold cyan]◆[/bold cyan] Provider   [dim]│[/dim] [bold]{provider}[/bold]")
    console.print(f"  [bold cyan]◆[/bold cyan] Model      [dim]│[/dim] [bold green]{model}[/bold green]")
    console.print(f"  [bold cyan]◆[/bold cyan] Workspace  [dim]│[/dim] [bold]{workspace}/[/bold]")
    console.print(f"  [bold cyan]◆[/bold cyan] Session    [dim]│[/dim] [dim]{thread_id[:8]}…[/dim]")
    console.print()
    console.print("  [dim]Shortcuts: [bold]Alt+1[/bold] Plan │ [bold]Alt+2[/bold] Code │ [bold]Alt+3[/bold] Doc │ [bold]/plan /code /doc /exit[/bold][/dim]")
    console.print(f"  [dim]{'─' * 50}[/dim]")
    console.print()

    while True:
        try:
            user_input = await session.prompt_async("User ❯ ")
            if not user_input.strip():
                continue
            if user_input.lower() in ['exit', 'quit', '/exit', '/quit']:
                console.print("[yellow]Goodbye! ShadowDev powering down...[/yellow]")
                break
                
            active_agent_override = current_agent_mode
            input_text = user_input.strip()
            
            if input_text.startswith('/plan'):
                active_agent_override = "planner"
                input_text = input_text[5:].strip()
            elif input_text.startswith('/code'):
                active_agent_override = "coder"
                input_text = input_text[5:].strip()
            elif input_text.startswith('/doc'):
                active_agent_override = "doc"
                input_text = input_text[4:].strip()
                
            if active_agent_override == "doc":
                active_agent_override = "planner"
                input_text += "\n\n[SYSTEM: Prioritize documentation. Docs > Code.]"
                
            if not input_text.strip():
                continue

            await run_agent(input_text, thread_id, active_agent_override)
            
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Goodbye! ShadowDev powering down...[/yellow]")
            break


# ── Agent runner ────────────────────────────────────────────
async def run_agent(message: str, thread_id: str, active_agent: str = None):
    graph_config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 100,
    }
    input_state = {
        "messages": [HumanMessage(content=message)],
        "workspace": config.WORKSPACE_DIR,
    }
    if active_agent:
        input_state["active_agent"] = active_agent

    console.print()
    
    live_display = None
    buffer = ""
    current_agent = "planner"
    printed_replies = set()
    active_tools = {}   # run_id → {status, start, icon, title, tool_name, args}
    tool_counter = 0

    try:
        async for event in graph.astream_events(input_state, config=graph_config, version="v2"):
            kind = event.get("event", "")
            
            # ── Agent handoff ───────────────────────────────
            data = event.get("data", {})
            if "output" in data and isinstance(data["output"], dict):
                if "active_agent" in data["output"]:
                    new_agent = data["output"]["active_agent"]
                    if new_agent != current_agent:
                        current_agent = new_agent
                        if current_agent == "coder":
                            console.print("  [bold cyan]🔀 Planner → Coder[/bold cyan]")
                        else:
                            console.print("  [bold yellow]🔀 Coder → Planner[/bold yellow]")

            # ── Streaming text ──────────────────────────────
            if kind == "on_chat_model_stream":
                if live_display is None:
                    live_display = Live(Markdown(""), console=console, refresh_per_second=15, transient=False)
                    live_display.start()
                
                chunk = event.get("data", {}).get("chunk")
                if chunk and hasattr(chunk, "content") and chunk.content:
                    if isinstance(chunk.content, str):
                        buffer += chunk.content
                    elif isinstance(chunk.content, list):
                        for block in chunk.content:
                            if isinstance(block, str):
                                buffer += block
                            elif isinstance(block, dict) and block.get("type") == "text":
                                buffer += block["text"]
                                
                    clean = buffer
                    clean = re.sub(r'```json\s*\{\s*"name"[\s\S]*?(?:```|$)', '', clean, flags=re.IGNORECASE)
                    clean = re.sub(r'\{\s*"name"\s*:\s*"[^"]+\"[\s\S]*?"arguments"\s*:[\s\S]*?(?:\}|$)', '', clean, flags=re.IGNORECASE)
                    stripped = clean.strip()
                    if stripped in ["{", "}", "{}", "{\n}", "{\n  \n}"]:
                        clean = ""
                    live_display.update(Markdown(clean.strip()))

            # ── Tool START ──────────────────────────────────
            elif kind == "on_tool_start":
                if live_display:
                    live_display.stop()
                    live_display = None
                    buffer = ""
                
                tool_name = event.get("name", "unknown")
                run_id = event.get("run_id", str(uuid.uuid4()))
                args = event.get("data", {}).get("input", {})
                
                # reply_to_user → render as text
                if tool_name == "reply_to_user":
                    msg = ""
                    if isinstance(args, dict):
                        msg = args.get("message", "")
                    elif isinstance(args, str):
                        try:
                            import json
                            msg = json.loads(args).get("message", "")
                        except Exception:
                            pass
                    if msg and str(msg).strip() and run_id not in printed_replies:
                        console.print(Markdown(str(msg)))
                        printed_replies.add(run_id)
                    continue
                
                icon, title = _tool_display(tool_name, args if isinstance(args, dict) else {})
                
                status = Status(
                    f"  [cyan]{icon}[/cyan]  [dim]{title}[/dim]",
                    console=console,
                    spinner="dots",
                )
                status.start()
                
                active_tools[run_id] = {
                    "status": status,
                    "start": time.time(),
                    "icon": icon,
                    "title": title,
                    "tool_name": tool_name,
                    "args": args if isinstance(args, dict) else {},
                }
                tool_counter += 1

            # ── Tool END ────────────────────────────────────
            elif kind == "on_tool_end":
                tool_name = event.get("name", "unknown")
                run_id = event.get("run_id", "")
                output = event.get("data", {}).get("output", "")
                args = event.get("data", {}).get("input", {})
                
                # reply_to_user fallback
                if tool_name == "reply_to_user":
                    if run_id in printed_replies:
                        continue
                    msg = ""
                    if isinstance(args, dict):
                        msg = args.get("message", "")
                    elif isinstance(args, str):
                        try:
                            import json
                            msg = json.loads(args).get("message", "")
                        except Exception:
                            pass
                    if msg and str(msg).strip():
                        console.print(Markdown(str(msg)))
                        printed_replies.add(run_id)
                    continue
                
                # Stop spinner
                tool_info = active_tools.pop(run_id, None)
                if tool_info:
                    tool_info["status"].stop()
                    elapsed = time.time() - tool_info["start"]
                    icon = tool_info["icon"]
                    title = tool_info["title"]
                else:
                    icon, title = _tool_display(tool_name, args if isinstance(args, dict) else {})
                    elapsed = 0
                
                elapsed_str = f" [dim]({_fmt_elapsed(elapsed)})[/dim]" if elapsed > 0 else ""
                
                # ── Render completion ───────────────────────
                if tool_name == "terminal_exec":
                    if hasattr(output, "content"): output = output.content
                    cmd = (args if isinstance(args, dict) else {}).get("command", "command")
                    console.print(f"  [green]✓[/green] [cyan]{icon}[/cyan]  [bold]{cmd}[/bold]{elapsed_str}")
                    if output and str(output).strip():
                        lines = str(output).strip().split("\n")
                        preview = "\n".join(f"    {l}" for l in lines[:10])
                        if len(lines) > 10:
                            preview += f"\n    [dim]… ({len(lines) - 10} more lines)[/dim]"
                        console.print(f"[dim]{preview}[/dim]")
                    console.print()
                        
                elif tool_name in ("file_edit", "file_write"):
                    target = (args if isinstance(args, dict) else {}).get("file_path", 
                              (args if isinstance(args, dict) else {}).get("TargetFile", "File"))
                    console.print(f"  [green]✓[/green] [cyan]{icon}[/cyan]  [bold]{_short_path(target)}[/bold]{elapsed_str}")
                    
                    diff_str = ""
                    if isinstance(output, str) and "Diff:" in output:
                        diff_str = output.split("Diff:")[1]
                    elif hasattr(output, "content") and isinstance(output.content, str) and "Diff:" in output.content:
                        diff_str = output.content.split("Diff:")[1]
                    if diff_str:
                        console.print(Syntax(diff_str.strip(), "diff", theme="monokai", padding=(0, 4)))
                    console.print()
                else:
                    console.print(f"  [green]✓[/green] [cyan]{icon}[/cyan]  {title}{elapsed_str}")

    except Exception as e:
        err_msg = str(e)
        if "cudaMalloc" in err_msg or "out of memory" in err_msg.lower():
            console.print("\n  [red]⚠ Ollama ran out of GPU memory (OOM).[/red]")
            console.print("  [dim]Tip: Restart Ollama or use a smaller model (qwen2.5-coder:7b)[/dim]")
        elif "ConnectError" in err_msg or "connection" in err_msg.lower():
            console.print("\n  [red]⚠ Cannot connect to Ollama.[/red]")
            console.print("  [dim]Tip: Make sure Ollama is running.[/dim]")
        else:
            console.print(f"\n  [red]⚠ Error: {err_msg[:200]}[/red]")
    finally:
        for tid, info in active_tools.items():
            try: info["status"].stop()
            except Exception: pass
        active_tools.clear()
        if live_display:
            live_display.stop()
        print()


if __name__ == "__main__":
    asyncio.run(chat_loop())
