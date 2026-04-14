import sys
import os
import uuid
import asyncio
import re
import time
import json
import logging
import datetime
from pathlib import Path

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.live import Live
from rich.panel import Panel
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
from agent.graph import build_graph, ALL_TOOLS
from agent.hooks import run_lifecycle_hook, LIFECYCLE_HOOKS
from agent.nodes import get_cache_stats
from agent.advisor import set_advisor_model, get_advisor_model, run_advisor
from langgraph.checkpoint.memory import MemorySaver
try:
    from langgraph.checkpoint.sqlite import SqliteSaver as _SqliteSaver
    _HAS_SQLITE_SAVER = True
except ImportError:
    _HAS_SQLITE_SAVER = False

# ── Session-level tool usage counter ────────────────────────────
_tool_usage: dict[str, int] = {}


# ── Structured JSON log handler ─────────────────────────────
class JSONLineHandler(logging.Handler):
    def __init__(self, filepath):
        super().__init__()
        self.filepath = filepath

    def emit(self, record):
        try:
            log_entry = {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            if record.exc_info:
                log_entry["exc"] = self.formatException(record.exc_info)
            with open(self.filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            pass


def _preprocess_markdown(text: str) -> str:
    """Prevent ~N~ from rendering as strikethrough.

    Models write ``~100~`` as an approximation marker, not as a strikethrough
    intent.  Single tildes around a number are stripped; double-tilde
    ``~~text~~`` emphasis is left untouched.
    """
    return re.sub(r'(?<!~)~(\d+(?:\.\d+)?)~(?!~)', r'\1', text)


def _setup_json_logging(thread_id: str) -> None:
    """Wire up per-session JSON line log file and prune logs older than 7 days."""
    log_dir = Path.home() / ".shadowdev" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 7-day retention: delete stale .jsonl files
    cutoff = time.time() - 7 * 24 * 3600
    for old_log in log_dir.glob("session-*.jsonl"):
        try:
            if old_log.stat().st_mtime < cutoff:
                old_log.unlink()
        except Exception:
            pass

    json_handler = JSONLineHandler(log_dir / f"session-{thread_id}.jsonl")
    json_handler.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(json_handler)


# ── First-run onboarding ─────────────────────────────────────
_SHADOWDEV_DIR = Path.home() / ".shadowdev"
_INITIALIZED_FLAG = _SHADOWDEV_DIR / ".initialized"

_SHADOWDEV_VERSION = "0.4.0"  # keep in sync with pyproject.toml


def _get_active_model() -> tuple[str, str]:
    """Return (model, provider) strings based on current config."""
    provider = config.LLM_PROVIDER
    model_map = {
        "ollama": config.OLLAMA_MODEL,
        "openai": config.OPENAI_MODEL,
        "anthropic": config.ANTHROPIC_MODEL,
        "google": config.GOOGLE_MODEL,
        "groq": config.GROQ_MODEL,
        "azure": config.AZURE_OPENAI_MODEL,
    }
    return model_map.get(provider, "unknown"), provider


def _show_first_run_welcome(console: Console) -> None:
    """Print a rich welcome panel on first run, then create the initialized flag."""
    model, provider = _get_active_model()
    tool_count = len(ALL_TOOLS)
    env_path = Path(os.path.dirname(os.path.abspath(__file__))) / ".env"

    welcome_lines = [
        f"[bold yellow]ShadowDev v{_SHADOWDEV_VERSION}[/bold yellow] — AI-Powered Coding Assistant\n",
        f"  [bold cyan]Model[/bold cyan]       [dim]│[/dim] [bold green]{model}[/bold green]  [dim]({provider})[/dim]",
        f"  [bold cyan]Provider[/bold cyan]    [dim]│[/dim] [bold]{provider.upper()}[/bold]",
        f"  [bold cyan]Tools[/bold cyan]       [dim]│[/dim] [bold]{tool_count}[/bold] tools loaded\n",
        "[bold white]Key shortcuts[/bold white]",
        "  [bold]/plan[/bold]   — enter planner agent mode",
        "  [bold]/code[/bold]   — enter coder agent mode",
        "  [bold]/fork[/bold]   — fork session at current point",
        "  [bold]Ctrl+C[/bold]  — cancel current request",
        "  [bold]/help[/bold]   — show all commands\n",
        f"  [dim]Config:[/dim] [italic]{env_path}[/italic]",
    ]

    panel = Panel(
        "\n".join(welcome_lines),
        title="[bold magenta]Welcome to ShadowDev[/bold magenta]",
        border_style="magenta",
        padding=(1, 2),
    )
    console.print()
    console.print(panel)
    console.print()

    # Mark initialized
    _SHADOWDEV_DIR.mkdir(parents=True, exist_ok=True)
    _INITIALIZED_FLAG.touch()

# Compile graph — use SQLite persistence if available, fallback to in-memory
def _make_checkpointer():
    """Create a SQLite checkpointer for session persistence across restarts."""
    if _HAS_SQLITE_SAVER:
        import os
        db_dir = _SHADOWDEV_DIR / "data"
        db_dir.mkdir(parents=True, exist_ok=True)
        db_path = str(db_dir / "sessions.db")
        try:
            return _SqliteSaver.from_conn_string(db_path)
        except Exception:
            pass
    return MemorySaver()

graph = build_graph(checkpointer=_make_checkpointer())

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

# ── Verbose mode ────────────────────────────────────────────
_verbose = False  # bool — annotated name can't be used with 'global'

# ── Daltonized theme color helpers ──────────────────────────
# Palettes safe for protanopia / deuteranopia (ported from Claude Code).
_DALTONIZED_PALETTES = {
    "daltonized-light": {
        "ai":   "#0059ff",   # blue  — AI responses
        "tool": "#d97706",   # orange — tool output
        "text": "#1a1a1a",   # dark gray on white bg
    },
    "daltonized-dark": {
        "ai":   "#38bdf8",   # sky blue — AI responses
        "tool": "#fbbf24",   # amber    — tool output
        "text": "#e2e8f0",   # light gray on dark bg
    },
}


def _theme_ai_color() -> str:
    """Return the Rich markup color for AI response text given current theme."""
    palette = _DALTONIZED_PALETTES.get(config.THEME)
    return palette["ai"] if palette else "green"


def _theme_tool_color() -> str:
    """Return the Rich markup color for tool output given current theme."""
    palette = _DALTONIZED_PALETTES.get(config.THEME)
    return palette["tool"] if palette else "cyan"


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


# ── Streaming rate limiter ──────────────────────────────────
_LAST_FLUSH = 0.0
_MIN_FLUSH_INTERVAL = 0.05  # 50ms = max 20fps terminal updates


def _should_flush() -> bool:
    """Return True if enough time has passed since last flush (rate limiter)."""
    global _LAST_FLUSH
    now = time.time()
    if now - _LAST_FLUSH >= _MIN_FLUSH_INTERVAL:
        _LAST_FLUSH = now
        return True
    return False


# ── Markdown streaming split ─────────────────────────────────

def _split_stable_markdown(text: str) -> tuple:
    """Split text into (stable_part, unstable_tail).

    Stable = everything up to and including the last complete top-level block.
    A top-level block ends at a blank line or at certain tokens (```, #, ---).
    The unstable tail is the current incomplete block being streamed.

    Returns (stable, unstable) where stable can be rendered safely.
    """
    # Find the last double-newline (block separator)
    last_break = text.rfind('\n\n')
    if last_break == -1:
        return "", text  # No complete block yet

    stable = text[:last_break + 2]
    unstable = text[last_break + 2:]

    # Don't split inside a code fence
    if stable.count('```') % 2 != 0:
        # Odd number of backtick fences = we're inside a fence, don't split here
        prev_break = text[:last_break].rfind('\n\n')
        if prev_break == -1:
            return "", text
        stable = text[:prev_break + 2]
        unstable = text[prev_break + 2:]

    return stable, unstable


# ── Synchronized output (DEC 2026 BSU/ESU) ──────────────────

_BSU = "\033[?2026h"  # Begin Synchronized Update
_ESU = "\033[?2026l"  # End Synchronized Update


def _supports_synchronized_output() -> bool:
    """Check if terminal supports DEC 2026 synchronized output (BSU/ESU)."""
    term = os.environ.get("TERM", "")
    term_program = os.environ.get("TERM_PROGRAM", "")
    # Supported: iTerm2, Ghostty, WezTerm, kitty — NOT tmux (it chunks)
    if "tmux" in os.environ.get("TMUX", ""):
        return False
    return term_program in ("iTerm.app", "ghostty", "WezTerm", "kitty") or "kitty" in term


def _sync_print(content: str) -> None:
    """Print with synchronized output to prevent flicker."""
    if _supports_synchronized_output():
        sys.stdout.write(_BSU + content + _ESU)
        sys.stdout.flush()
    else:
        sys.stdout.write(content)
        sys.stdout.flush()


# ── Bottom Toolbar ──────────────────────────────────────────
def bottom_toolbar():
    mode = current_agent_mode.upper()
    color = MODE_COLORS.get(current_agent_mode, "white")
    model, _provider = _get_active_model()
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
async def chat_loop(resume_id: str = None):
    global current_agent_mode
    session = PromptSession(style=style, bottom_toolbar=bottom_toolbar, key_bindings=bindings)
    thread_id = resume_id if resume_id else str(uuid.uuid4())
    _last_interrupt: float = 0.0  # track double Ctrl+C

    # Session cost/usage tracking
    _session_stats = {"turns": 0, "tokens": 0, "cost": 0.0}
    if resume_id:
        # Try to restore stats from existing session state
        try:
            state = graph.get_state({"configurable": {"thread_id": resume_id}})
            if state and state.values:
                sv = state.values
                _session_stats["turns"] = sv.get("session_turns", 0)
                _session_stats["tokens"] = sv.get("total_tokens_used", 0)
                _session_stats["cost"] = sv.get("session_cost", 0.0)
        except Exception:
            pass

    # ── JSON structured logging ──────────────────────────────
    _setup_json_logging(thread_id)

    # ── Lifecycle: session_start ─────────────────────────────
    if LIFECYCLE_HOOKS:
        await run_lifecycle_hook("session_start", {"thread_id": thread_id})

    # ── First-run welcome panel ──────────────────────────────
    if not _INITIALIZED_FLAG.exists():
        _show_first_run_welcome(console)

    # ── Concise session banner (every run) ───────────────────
    model, provider = _get_active_model()
    tool_count = len(ALL_TOOLS)
    workspace = os.path.basename(os.path.abspath(config.WORKSPACE_DIR))

    console.print()
    console.print(
        f"  [bold magenta]ShadowDev[/bold magenta]"
        f"  [dim]│[/dim]  [bold green]{model}[/bold green]  [dim]({provider})[/dim]"
        f"  [dim]│[/dim]  [bold]{tool_count}[/bold] tools"
        f"  [dim]│[/dim]  [dim]/help for commands[/dim]"
    )
    console.print(f"  [dim]Workspace: {workspace}/  │  Session: {thread_id[:8]}…[/dim]")
    console.print(f"  [dim]{'─' * 60}[/dim]")
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
            
            if input_text.strip() == "/sessions":
                # List recent sessions from SQLite checkpointer
                try:
                    if hasattr(graph, "checkpointer") and hasattr(graph.checkpointer, "conn"):
                        import sqlite3 as _sqlite3
                        conn = graph.checkpointer.conn
                        rows = conn.execute(
                            "SELECT thread_id, MAX(checkpoint_ns) as latest "
                            "FROM checkpoints GROUP BY thread_id ORDER BY latest DESC LIMIT 10"
                        ).fetchall()
                        if rows:
                            console.print("  [bold]Recent sessions[/bold] (use --resume SESSION_ID):")
                            for row in rows:
                                tid = row[0]
                                console.print(f"    {tid}")
                        else:
                            console.print("  No sessions found.")
                    else:
                        console.print("  [dim]Session listing requires SQLite checkpointer.[/dim]")
                except Exception as e:
                    console.print(f"  [red]Error listing sessions: {e}[/red]")
                continue
            elif input_text.strip() in ("/usage", "/cost"):
                from agent.tools.cost_tracker import format_cost
                cost_str = format_cost(_session_stats["cost"])
                turns = _session_stats["turns"]
                tokens = _session_stats["tokens"]
                tokens_k = f"{tokens / 1000:.1f}K" if tokens >= 1000 else str(tokens)
                console.print(
                    f"  [bold]Session usage[/bold]  │  "
                    f"Cost: [bold green]{cost_str}[/bold green]  │  "
                    f"Turns: [bold]{turns}[/bold]  │  "
                    f"Tokens: [bold]{tokens_k}[/bold]"
                )
                continue
            elif input_text.strip() == "/verbose":
                global _verbose
                _verbose = not _verbose
                if _verbose:
                    console.print("[dim]Verbose mode ON[/dim]")
                else:
                    console.print("[dim]Verbose mode OFF[/dim]")
                continue
            elif input_text.startswith('/fork'):
                # /fork [N]  — Fork current session at message N (default: now)
                parts = input_text.split()
                fork_at = -1
                if len(parts) > 1:
                    try:
                        fork_at = int(parts[1])
                    except ValueError:
                        console.print(f"  [red]Usage: /fork [N]  (N = message index, default: end)[/red]")
                        continue
                from agent.tools.session_tools import fork_session as _fork_session
                result = _fork_session.invoke({"session_id": thread_id, "fork_at": fork_at})
                console.print(result)
                continue
            elif input_text.strip() == "/insights":
                from agent.tools.cost_tracker import format_cost
                turns = _session_stats["turns"]
                tokens = _session_stats["tokens"]
                cost = _session_stats["cost"]
                cost_str = format_cost(cost)
                avg_tokens = (tokens // turns) if turns > 0 else 0
                # Top tools by usage (sorted descending, up to 5)
                top_tools = sorted(_tool_usage.items(), key=lambda x: x[1], reverse=True)[:5]
                tools_str = "  ".join(f"{name} ({count}x)" for name, count in top_tools) or "none"
                cache_stats = get_cache_stats()
                cache_breaks = cache_stats.get("cache_breaks", 0)
                lines = [
                    f"  [bold]Turns:[/bold]              {turns}",
                    f"  [bold]Total tokens:[/bold]       {tokens:,}",
                    f"  [bold]Cost:[/bold]               {cost_str}",
                    f"  [bold]Tools used:[/bold]         {tools_str}",
                    f"  [bold]Avg tokens/turn:[/bold]    {avg_tokens:,}",
                    f"  [bold]Prompt cache breaks:[/bold] {cache_breaks}",
                    f"  [bold]Session ID:[/bold]         {thread_id}",
                ]
                console.print(Panel(
                    "\n".join(lines),
                    title="[bold cyan]Session Insights[/bold cyan]",
                    border_style="cyan",
                    padding=(0, 1),
                ))
                continue
            elif input_text.strip().startswith("/advisor"):
                parts = input_text.strip().split(maxsplit=1)
                if len(parts) == 1 or parts[1].strip() in ("off", "none", ""):
                    set_advisor_model("")
                    console.print("[dim]Advisor disabled[/dim]")
                else:
                    model_name = parts[1].strip()
                    set_advisor_model(model_name)
                    console.print(f"[dim]Advisor model set to: {model_name}[/dim]")
                continue
            elif input_text.strip() in ("/help",):
                help_lines = [
                    "[bold white]ShadowDev Commands[/bold white]\n",
                    "  [bold]/plan[/bold]          — enter planner agent mode",
                    "  [bold]/code[/bold]          — enter coder agent mode",
                    "  [bold]/doc[/bold]           — enter documentation mode",
                    "  [bold]/fork [N][/bold]      — fork session at message N (default: now)",
                    "  [bold]/sessions[/bold]      — list recent sessions",
                    "  [bold]/usage[/bold]         — show session cost & token usage",
                    "  [bold]/insights[/bold]      — detailed session analytics",
                    "  [bold]/verbose[/bold]       — toggle verbose token display",
                    "  [bold]/advisor <model>[/bold] — enable advisor model (e.g. claude-opus-4-6)",
                    "  [bold]/advisor off[/bold]   — disable advisor model",
                    "  [bold]/memory [file][/bold] — open memory dir (or specific file) in $EDITOR",
                    "  [bold]/help[/bold]          — show this help\n",
                    "  [bold]Alt+1[/bold]  planner  [bold]Alt+2[/bold]  coder  [bold]Alt+3[/bold]  doc",
                    "  [bold]Ctrl+C[/bold]  cancel  [bold]Ctrl+C×2[/bold]  exit",
                ]
                console.print(Panel(
                    "\n".join(help_lines),
                    title="[bold magenta]Help[/bold magenta]",
                    border_style="magenta",
                    padding=(0, 2),
                ))
                continue
            elif input_text.strip().startswith("/memory"):
                import subprocess as _subprocess
                from pathlib import Path as _Path

                mem_dir = _Path.home() / ".shadowdev" / "memory"
                if not mem_dir.exists():
                    mem_dir = _Path(config.WORKSPACE_DIR) / ".shadowdev" / "memory"
                mem_dir.mkdir(parents=True, exist_ok=True)

                _mem_parts = input_text.strip().split(maxsplit=1)
                if len(_mem_parts) > 1:
                    _target = mem_dir / _mem_parts[1].strip()
                    if not _target.suffix:
                        _target = _target.with_suffix(".md")
                else:
                    _target = mem_dir / "MEMORY.md"
                    if not _target.exists():
                        _files = sorted(mem_dir.glob("*.md"))
                        if _files:
                            console.print("[bold]Memory files:[/bold]")
                            for _f in _files:
                                console.print(f"  {_f.name}")
                            console.print("\n[dim]Use /memory <filename> to edit a specific file[/dim]")
                        else:
                            console.print("[dim]No memory files found. Use memory_save tool to create entries.[/dim]")
                        continue

                _editor = os.environ.get("EDITOR", os.environ.get("VISUAL", "notepad" if os.name == "nt" else "nano"))
                try:
                    _subprocess.run([_editor, str(_target)])
                    console.print(f"[dim]Edited: {_target.name}[/dim]")
                except FileNotFoundError:
                    console.print(f"[yellow]Editor '{_editor}' not found. Set $EDITOR env var.[/yellow]")
                except Exception as _e:
                    console.print(f"[red]Failed to open editor: {_e}[/red]")
                continue
            elif input_text.startswith('/plan'):
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

            # ── Lifecycle: user_prompt_submit ────────────────
            if LIFECYCLE_HOOKS:
                modified = await run_lifecycle_hook(
                    "user_prompt_submit",
                    {"prompt": input_text, "agent": active_agent_override},
                )
                if modified:
                    input_text = modified

            _run_start = time.monotonic()
            _last_ai_response = await run_agent(input_text, thread_id, active_agent_override, _session_stats)
            _run_elapsed = time.monotonic() - _run_start

            # ── Model Advisor ────────────────────────────────
            _advisor = get_advisor_model() or config.ADVISOR_MODEL
            if _advisor and input_text and _last_ai_response:
                try:
                    _critique = await run_advisor(input_text, _last_ai_response, _advisor)
                    if _critique:
                        console.print(f"\n[dim]💡 Advisor ({_advisor}): {_critique}[/dim]")
                except Exception:
                    pass

            # ── Desktop notification for long runs ───────────
            if getattr(config, "NOTIFY_ON_COMPLETE", True) and _run_elapsed > 10:
                try:
                    from agent.notifications import notify
                    _turns = _session_stats.get("turns", 0) if _session_stats else 0
                    _tokens = _session_stats.get("tokens", 0) if _session_stats else 0
                    notify("ShadowDev", f"Task complete — {_turns} turns, {_tokens} tokens")
                except Exception:
                    pass

            # ── Verbose token usage ──────────────────────────
            if _verbose and _session_stats is not None:
                tokens = _session_stats.get("tokens", 0)
                cost = _session_stats.get("cost", 0.0)
                turns = _session_stats.get("turns", 0)
                tokens_k = f"{tokens / 1000:.1f}K" if tokens >= 1000 else str(tokens)
                console.print(
                    f"  [dim]tokens: {tokens_k}  cost: ${cost:.4f}  turns: {turns}[/dim]"
                )

            # ── Lifecycle: stop ──────────────────────────────
            if LIFECYCLE_HOOKS:
                await run_lifecycle_hook("stop", {"thread_id": thread_id})
            
        except EOFError:
            console.print("\n[yellow]Goodbye! ShadowDev powering down...[/yellow]")
            break
        except KeyboardInterrupt:
            now = time.time()
            if now - _last_interrupt < 1.5:
                # Second Ctrl+C within 1.5s → exit
                console.print("\n[yellow]Goodbye! ShadowDev powering down...[/yellow]")
                break
            _last_interrupt = now
            console.print("[dim]Interrupted. Press Ctrl+C again to exit.[/dim]")
            continue

    # ── Lifecycle: session_end ───────────────────────────────
    if LIFECYCLE_HOOKS:
        await run_lifecycle_hook("session_end", {"thread_id": thread_id})


# ── Agent runner ────────────────────────────────────────────
async def run_agent(message: str, thread_id: str, active_agent: str = None, _session_stats: dict = None):
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
                                
                    if _should_flush():
                        clean = buffer
                        clean = re.sub(r'```json\s*\{\s*"name"[\s\S]*?(?:```|$)', '', clean, flags=re.IGNORECASE)
                        clean = re.sub(r'\{\s*"name"\s*:\s*"[^"]+\"[\s\S]*?"arguments"\s*:[\s\S]*?(?:\}|$)', '', clean, flags=re.IGNORECASE)
                        stripped = clean.strip()
                        if stripped in ["{", "}", "{}", "{\n}", "{\n  \n}"]:
                            clean = ""
                        # Split at block boundary: render stable part as Markdown,
                        # unstable tail as plain text to avoid incomplete syntax flicker.
                        stable, unstable = _split_stable_markdown(clean.strip())
                        if stable:
                            display = Group(Markdown(_preprocess_markdown(stable)), Text(unstable))
                        else:
                            display = Markdown(_preprocess_markdown(clean.strip()))
                        if _supports_synchronized_output():
                            sys.stdout.write(_BSU)
                            sys.stdout.flush()
                        live_display.update(display)
                        if _supports_synchronized_output():
                            sys.stdout.write(_ESU)
                            sys.stdout.flush()

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
                        console.print(Markdown(_preprocess_markdown(str(msg))))
                        printed_replies.add(run_id)
                    continue

                icon, title = _tool_display(tool_name, args if isinstance(args, dict) else {})
                _tc = _theme_tool_color()
                status = Status(
                    f"  [{_tc}]{icon}[/{_tc}]  [dim]{title}[/dim]",
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
                _tool_usage[tool_name] = _tool_usage.get(tool_name, 0) + 1

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
                        console.print(Markdown(_preprocess_markdown(str(msg))))
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
                _tc = _theme_tool_color()
                if tool_name == "terminal_exec":
                    if hasattr(output, "content"): output = output.content
                    cmd = (args if isinstance(args, dict) else {}).get("command", "command")
                    console.print(f"  [green]✓[/green] [{_tc}]{icon}[/{_tc}]  [bold]{cmd}[/bold]{elapsed_str}")
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
                    console.print(f"  [green]✓[/green] [{_tc}]{icon}[/{_tc}]  [bold]{_short_path(target)}[/bold]{elapsed_str}")

                    diff_str = ""
                    if isinstance(output, str) and "Diff:" in output:
                        diff_str = output.split("Diff:")[1]
                    elif hasattr(output, "content") and isinstance(output.content, str) and "Diff:" in output.content:
                        diff_str = output.content.split("Diff:")[1]
                    if diff_str:
                        console.print(Syntax(diff_str.strip(), "diff", theme="monokai", padding=(0, 4)))
                    console.print()
                else:
                    console.print(f"  [green]✓[/green] [{_tc}]{icon}[/{_tc}]  {title}{elapsed_str}")

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

    # Update session usage stats from graph state (best-effort)
    if _session_stats is not None:
        try:
            state = graph.get_state({"configurable": {"thread_id": thread_id}})
            if state and state.values:
                sv = state.values
                _session_stats["turns"] = sv.get("session_turns", _session_stats["turns"])
                _session_stats["tokens"] = sv.get("total_tokens_used", _session_stats["tokens"])
                _session_stats["cost"] = sv.get("session_cost", _session_stats["cost"])
        except Exception:
            pass

    return buffer


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        prog="shadowdev",
        description="ShadowDev — AI-powered coding assistant",
    )
    parser.add_argument(
        "-p", "--prompt",
        metavar="PROMPT",
        help="Run in headless/CI mode with this prompt then exit.",
    )
    parser.add_argument(
        "--session-id",
        metavar="ID",
        help="Session ID for multi-turn context persistence (default: new UUID).",
    )
    parser.add_argument(
        "--agent",
        choices=["planner", "coder"],
        default="planner",
        help="Starting agent mode (default: planner).",
    )
    parser.add_argument(
        "--output-format",
        choices=["text", "json", "stream-json"],
        default="text",
        help="Output format for headless mode (default: text).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=0,
        metavar="SECONDS",
        help="Max execution time in seconds (0 = no limit, default: 0).",
    )
    parser.add_argument(
        "--allowed-tools",
        metavar="TOOLS",
        help="Comma-separated whitelist of tool names (default: all tools).",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Launch full-screen TUI mode (requires textual).",
    )
    parser.add_argument(
        "--resume",
        metavar="SESSION_ID",
        help="Resume a previous session by ID.",
    )
    parser.add_argument(
        "--team",
        action="store_true",
        help="Activate coordinator mode — lead agent manages async worker team.",
    )
    parser.add_argument(
        "--improve",
        metavar="GOAL",
        help=(
            "Run the autonomous improvement loop against the current workspace. "
            "Provide a goal string, e.g. \"improve code quality and test coverage\"."
        ),
    )
    parser.add_argument(
        "--improve-rounds",
        metavar="N",
        type=int,
        default=5,
        help="Max improvement rounds (default: 5, used with --improve).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose/debug mode: show token usage after each AI response.",
    )

    args = parser.parse_args()

    if args.verbose:
        _verbose = True

    if args.team:
        import config as _cfg
        _cfg.COORDINATOR_MODE = True
        from agent.graph import build_graph as _bg
        from langgraph.checkpoint.memory import MemorySaver as _MS
        graph = _bg(checkpointer=_MS())
        console.print("[bold magenta]🤝 Coordinator mode active — leading agent team[/bold magenta]")

    if args.improve:
        # ── Autonomous improvement loop ──────────────────────
        import config as _cfg
        workspace = _cfg.WORKSPACE_DIR or os.getcwd()
        goal = args.improve
        rounds = args.improve_rounds

        console.print(
            f"[bold cyan]🔄 Improvement loop starting[/bold cyan]\n"
            f"  Goal   : {goal}\n"
            f"  Rounds : {rounds}\n"
            f"  Workspace: {workspace}"
        )

        from agent.team.improvement_loop import run_improvement_loop
        summary = asyncio.run(run_improvement_loop(
            goal=goal,
            workspace=workspace,
            max_rounds=rounds,
        ))
        console.print(f"\n[bold green]✓ Improvement loop complete[/bold green]\n{summary}")

    elif args.tui:
        # ── Full-screen TUI mode ─────────────────────────────
        from tui import main as tui_main
        tui_main()
    elif args.prompt:
        # ── Headless / CI mode ───────────────────────────────
        from agent.headless import run_headless, print_result

        allowed = [t.strip() for t in args.allowed_tools.split(",")] if args.allowed_tools else None

        result = asyncio.run(run_headless(
            prompt=args.prompt,
            session_id=args.session_id,
            agent=args.agent,
            output_format=args.output_format,
            timeout=args.timeout or None,
            allowed_tools=allowed,
        ))
        print_result(result, args.output_format)
        sys.exit(result.exit_code)
    else:
        # ── Interactive mode ─────────────────────────────────
        asyncio.run(chat_loop(resume_id=args.resume))
