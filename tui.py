"""
ShadowDev TUI v3.0 — Full-screen Terminal UI (inspired by OpenCode).

Major features:
- Command palette (Ctrl+P)
- Session list with welcome screen
- Multi-line input (Shift+Enter newline, Enter send)
- Token/cost meter in status bar
- Collapsible tool output in chat
- Diff review with syntax highlighting
- File tree for modified files
- Toast notifications
- Responsive layout
- Theme support (dark/light)
- Better streaming (progressive markdown)
"""

import sys
import os
import re
import uuid
import asyncio
import time
import json
from datetime import datetime
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Header, Footer, RichLog, Static, Label, Input,
    ListView, ListItem, Tree, Button, TextArea,
    LoadingIndicator, ContentSwitcher, TabbedContent, TabPane,
)
from textual.containers import Horizontal, Vertical, VerticalScroll, Center, Container
from textual.reactive import reactive, var
from textual.screen import ModalScreen
from textual import work, on
from textual.worker import Worker
from textual.message import Message
from textual.css.query import NoMatches
from textual.timer import Timer
from textual.notifications import SeverityLevel

from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from rich.panel import Panel
from rich.table import Table
from rich.console import Group

from langchain_core.messages import HumanMessage

import config
from agent.graph import build_graph
from agent.hooks import run_lifecycle_hook, LIFECYCLE_HOOKS
from langgraph.checkpoint.memory import MemorySaver


# ── Constants ──────────────────────────────────────────────

VERSION = "3.0.0"

TOOL_ICONS = {
    "file_read": ("", "Read"),
    "batch_read": ("", "Batch Read"),
    "file_list": ("", "List"),
    "file_edit": ("", "Edit"),
    "file_write": ("", "Write"),
    "file_edit_batch": ("", "Batch Edit"),
    "terminal_exec": ("", "Run"),
    "grep_search": ("", "Grep"),
    "glob_search": ("", "Glob"),
    "code_search": ("", "Search"),
    "code_analyze": ("", "Analyze"),
    "webfetch": ("", "Fetch"),
    "web_search": ("", "Web"),
    "run_tests": ("", "Test"),
    "git_status": ("", "git status"),
    "git_diff": ("", "git diff"),
    "git_log": ("", "git log"),
    "git_add": ("", "git add"),
    "git_commit": ("", "git commit"),
    "git_show": ("", "git show"),
    "git_blame": ("", "git blame"),
    "git_branch": ("", "git branch"),
    "git_stash": ("", "git stash"),
    "code_quality": ("", "Quality"),
    "dep_graph": ("", "DepGraph"),
    "memory_save": ("", "MemSave"),
    "memory_search": ("", "MemSearch"),
    "skill_invoke": ("", "Skill"),
    "task_explore": ("", "Explore"),
    "task_general": ("", "Subagent"),
    "github_list_issues": ("", "GH Issues"),
    "github_list_prs": ("", "GH PRs"),
    "github_create_pr": ("", "GH NewPR"),
    "gitlab_list_issues": ("", "GL Issues"),
    "gitlab_list_mrs": ("", "GL MRs"),
    "lsp_definition": ("", "LSP Def"),
    "lsp_references": ("", "LSP Refs"),
    "todo_read": ("", "Todo"),
    "todo_write": ("", "Todo"),
    "plan_enter": ("", "Plan"),
    "plan_exit": ("", "Plan"),
    "question": ("", "Question"),
    "reply_to_user": ("", "Reply"),
    "handoff_to_coder": ("", "Coder"),
    "handoff_to_planner": ("", "Planner"),
}

AGENT_COLORS = {
    "planner": "#f5c542",
    "coder": "#42b0f5",
    "doc": "#42f587",
}


# ── Helpers ────────────────────────────────────────────────

def _short_path(p: str) -> str:
    if not p:
        return ""
    parts = p.replace("\\", "/").split("/")
    return "/".join(parts[-2:]) if len(parts) > 2 else p


def _tool_label(name: str, args: dict) -> str:
    icon, base = TOOL_ICONS.get(name, ("*", name))
    if name in ("file_read", "batch_read", "file_list"):
        target = args.get("file_path") or args.get("absolute_path") or args.get("directory", "")
        return f"{icon} {base} {_short_path(target)}"
    if name in ("file_edit", "file_write", "file_edit_batch"):
        target = args.get("file_path") or args.get("TargetFile", "")
        return f"{icon} {base} {_short_path(target)}"
    if name == "terminal_exec":
        return f"{icon} $ {(args.get('command', ''))[:50]}"
    if name in ("grep_search", "glob_search", "code_search"):
        q = args.get("pattern") or args.get("query") or args.get("search_term", "")
        return f'{icon} {base} "{q[:30]}"'
    if name.startswith("git_"):
        return f"{icon} {base}"
    if name in ("task_explore", "task_general"):
        task = str(args.get("task", ""))[:35]
        return f'{icon} {base}: "{task}"'
    return f"{icon} {base}"


def _fmt_elapsed(s: float) -> str:
    if s < 1:
        return f"{s*1000:.0f}ms"
    if s < 60:
        return f"{s:.1f}s"
    return f"{s/60:.1f}m"


def _get_model_name() -> str:
    provider = config.LLM_PROVIDER
    if provider == "ollama":
        return config.OLLAMA_MODEL
    return getattr(config, f"{provider.upper()}_MODEL", "unknown")


def _get_workspace() -> str:
    return os.path.basename(os.path.abspath(config.WORKSPACE_DIR))


# ── Command Palette Screen ─────────────────────────────────

COMMANDS = [
    ("/plan", "Switch to Planner mode"),
    ("/code", "Switch to Coder mode"),
    ("/doc", "Switch to Doc mode"),
    ("/clear", "Clear chat history"),
    ("/new", "New session"),
    ("/sessions", "Show session list"),
    ("/help", "Show help"),
    ("/theme", "Toggle dark/light theme"),
    ("/compact", "Compact conversation context"),
    ("/exit", "Quit ShadowDev"),
]


class CommandPalette(ModalScreen[str]):
    """Fuzzy command picker overlay."""

    BINDINGS = [
        Binding("escape", "dismiss('')", "Close"),
    ]

    CSS = """
    CommandPalette {
        align: center middle;
    }
    #palette-box {
        width: 60;
        max-height: 20;
        background: $surface;
        border: thick $primary;
        padding: 1 2;
    }
    #palette-input {
        width: 100%;
        margin-bottom: 1;
    }
    #palette-list {
        height: auto;
        max-height: 14;
    }
    .palette-item {
        padding: 0 1;
        height: 1;
    }
    .palette-item:hover {
        background: $accent;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-box"):
            yield Label("[b]Command Palette[/b]  (type to filter)", id="palette-title")
            yield Input(placeholder="Type a command...", id="palette-input")
            yield VerticalScroll(id="palette-list")

    def on_mount(self) -> None:
        self._refresh_list("")
        self.query_one("#palette-input", Input).focus()

    @on(Input.Changed, "#palette-input")
    def _filter(self, event: Input.Changed) -> None:
        self._refresh_list(event.value.strip().lower())

    def _refresh_list(self, query: str) -> None:
        container = self.query_one("#palette-list", VerticalScroll)
        container.remove_children()
        for cmd, desc in COMMANDS:
            if query and query not in cmd.lower() and query not in desc.lower():
                continue
            container.mount(
                Static(f"[b]{cmd}[/b]  [dim]{desc}[/dim]",
                       classes="palette-item", id=f"cmd_{cmd[1:]}")
            )

    @on(Input.Submitted, "#palette-input")
    def _submit(self, event: Input.Submitted) -> None:
        val = event.value.strip()
        if val and not val.startswith("/"):
            val = "/" + val
        self.dismiss(val)

    def on_click(self, event) -> None:
        """Handle click on palette items."""
        widget = event.widget if hasattr(event, 'widget') else None
        if widget is None:
            # Walk up from event coordinates
            return
        widget_id = getattr(widget, "id", "") or ""
        if widget_id.startswith("cmd_"):
            self.dismiss("/" + widget_id[4:])


# ── Welcome Screen Widget ──────────────────────────────────

class WelcomeView(Static):
    """Shown when chat is empty — tips + session info."""

    def render(self) -> Text:
        t = Text()
        t.append("\n")
        t.append("  ShadowDev", style="bold magenta")
        t.append(f"  v{VERSION}\n", style="dim")
        t.append("  AI-Powered Coding Assistant\n\n", style="dim italic")
        t.append(f"  Provider: ", style="dim")
        t.append(f"{config.LLM_PROVIDER.upper()}", style="bold")
        t.append(f"  Model: ", style="dim")
        t.append(f"{_get_model_name()}\n", style="bold green")
        t.append(f"  Workspace: ", style="dim")
        t.append(f"{_get_workspace()}/\n\n", style="bold")
        t.append("  Quick Start:\n", style="bold")
        t.append("  > Type a message and press Enter to chat\n", style="dim")
        t.append("  > Use /plan, /code, /doc to switch modes\n", style="dim")
        t.append("  > Press Ctrl+P for command palette\n", style="dim")
        t.append("  > Press Tab to switch agents\n", style="dim")
        t.append("\n  Tips:\n", style="bold")
        t.append('  > "Fix the bug in auth.py" — Coder fixes it\n', style="dim")
        t.append('  > "Explain how the API works" — Planner analyzes\n', style="dim")
        t.append('  > "Run tests and fix failures" — Full workflow\n', style="dim")
        t.append("\n")
        return t


# ── Status Bar Widget ──────────────────────────────────────

class StatusBar(Static):
    """Bottom-docked status line with model, agent, tokens, session."""

    agent = reactive("planner")
    tokens = reactive(0)
    turns = reactive(0)
    cost = reactive(0.0)
    busy = reactive(False)
    session_id = reactive("")

    def render(self) -> Text:
        t = Text()
        # Agent mode — show COORDINATOR badge when coordinator mode is active
        if config.COORDINATOR_MODE:
            t.append(" COORDINATOR ", style="reverse bold magenta")
        else:
            color = AGENT_COLORS.get(self.agent, "white")
            t.append(f" {self.agent.upper()} ", style=f"reverse bold {color}")
        t.append("  ", style="dim")
        # Provider + model
        t.append(f"{config.LLM_PROVIDER.upper()}", style="bold")
        t.append("/", style="dim")
        t.append(f"{_get_model_name()} ", style="bold green")
        # Status
        if self.busy:
            t.append(" working... ", style="bold yellow")
        # Tokens
        if self.tokens > 0:
            t.append(f" {self.tokens:,} tokens", style="dim")
        # Turns
        t.append(f"  Turn {self.turns}", style="dim")
        # Cost
        if self.cost > 0:
            t.append(f"  ${self.cost:.4f}", style="dim")
        # Session
        if self.session_id:
            t.append(f"  [{self.session_id[:8]}]", style="dim")
        return t


# ── Tool Activity Sidebar ──────────────────────────────────

class ToolSidebar(Vertical):
    """Right sidebar: tool activity + modified files."""

    CSS = """
    ToolSidebar {
        width: 35;
        min-width: 28;
        max-width: 45;
        border-left: tall $primary-background-darken-2;
        background: $surface;
    }
    #tool-header {
        height: 1;
        background: $primary-background-darken-1;
        padding: 0 1;
        text-style: bold;
    }
    #tool-log {
        height: 1fr;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #files-header {
        height: 1;
        background: $primary-background-darken-1;
        padding: 0 1;
        text-style: bold;
    }
    #files-tree {
        height: auto;
        max-height: 12;
        padding: 0 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Static("Activity", id="tool-header")
        yield RichLog(id="tool-log", highlight=True, markup=True, wrap=True, max_lines=500)
        yield Static("Modified Files", id="files-header")
        yield Tree("files", id="files-tree")

    def on_mount(self) -> None:
        tree = self.query_one("#files-tree", Tree)
        tree.show_root = False
        tree.guide_depth = 2

    def log_tool_start(self, label: str) -> None:
        log = self.query_one("#tool-log", RichLog)
        log.write(Text(f"  > {label}", style="yellow"))

    def log_tool_end(self, label: str, elapsed: str) -> None:
        log = self.query_one("#tool-log", RichLog)
        log.write(Text(f"  {label} ({elapsed})", style="green"))

    def log_tool_error(self, label: str, error: str) -> None:
        log = self.query_one("#tool-log", RichLog)
        log.write(Text(f"  x {label}: {error[:40]}", style="red"))

    def log_agent(self, agent: str) -> None:
        log = self.query_one("#tool-log", RichLog)
        color = AGENT_COLORS.get(agent, "white")
        log.write(Text(f"  [{agent.upper()}]", style=f"bold {color}"))

    def add_file(self, filepath: str) -> None:
        tree = self.query_one("#files-tree", Tree)
        short = _short_path(filepath)
        # Avoid duplicates
        for child in tree.root.children:
            if child.label.plain == short:
                return
        tree.root.add_leaf(short)

    def refresh_workers(self) -> None:
        """Show last 5 workers from the coordinator pool (coordinator mode only)."""
        if not config.COORDINATOR_MODE:
            return
        try:
            from agent.team.tools import _POOL
        except ImportError:
            return
        status_icons = {
            "running": "⚡",
            "completed": "✅",
            "failed": "❌",
            "stopped": "⏹",
        }
        workers = _POOL.list_workers()[-5:]
        if not workers:
            return
        log = self.query_one("#tool-log", RichLog)
        log.write(Text("  [Workers]", style="bold magenta"))
        for w in workers:
            icon = status_icons.get(w["status"], "?")
            desc = w["description"][:25]
            line = f"  {icon} [{w['role']}] {desc} ({w['status']})"
            style = "magenta" if w["status"] == "running" else "dim"
            log.write(Text(line, style=style))


# ── Chat Input Widget ──────────────────────────────────────

class ChatInput(Vertical):
    """Multi-line input area with submit button."""

    CSS = """
    ChatInput {
        height: auto;
        max-height: 8;
        dock: bottom;
        padding: 0 1;
        background: $surface;
        border-top: tall $primary-background-darken-2;
    }
    #chat-input-area {
        height: auto;
        min-height: 3;
        max-height: 6;
    }
    #input-hint {
        height: 1;
        color: $text-muted;
    }
    """

    class Submitted(Message):
        def __init__(self, value: str) -> None:
            super().__init__()
            self.value = value

    def compose(self) -> ComposeResult:
        yield TextArea(id="chat-input-area")
        yield Static(
            "[dim]Enter[/dim] send  [dim]Shift+Enter[/dim] newline  "
            "[dim]Tab[/dim] switch agent  [dim]Ctrl+P[/dim] commands  "
            "[dim]Ctrl+C[/dim] cancel",
            id="input-hint",
        )

    def on_mount(self) -> None:
        ta = self.query_one("#chat-input-area", TextArea)
        ta.show_line_numbers = False
        ta.soft_wrap = True
        ta.focus()

    def on_key(self, event) -> None:
        if event.key == "enter" and not event.shift:
            event.prevent_default()
            event.stop()
            ta = self.query_one("#chat-input-area", TextArea)
            val = ta.text.strip()
            if val:
                self.post_message(self.Submitted(val))
                ta.clear()

    def disable(self) -> None:
        self.query_one("#chat-input-area", TextArea).disabled = True

    def enable(self) -> None:
        ta = self.query_one("#chat-input-area", TextArea)
        ta.disabled = False
        ta.focus()


# ── Main TUI App ───────────────────────────────────────────

class ShadowDevTUI(App):
    """ShadowDev TUI v3 — OpenCode-inspired interface."""

    TITLE = "ShadowDev"
    SUB_TITLE = f"v{VERSION}"

    CSS = """
    Screen {
        layout: horizontal;
    }
    #main-area {
        width: 1fr;
        layout: vertical;
    }
    #status-bar {
        height: 1;
        dock: top;
        background: $primary-background-darken-1;
        padding: 0 1;
    }
    #chat-area {
        height: 1fr;
    }
    #chat-log {
        height: 1fr;
        padding: 0 1;
        scrollbar-size: 1 1;
    }
    #welcome {
        height: 1fr;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("ctrl+p", "command_palette", "Commands", key_display="Ctrl+P"),
        Binding("tab", "switch_agent", "Switch Agent", key_display="Tab"),
        Binding("ctrl+q", "quit_app", "Quit", key_display="Ctrl+Q"),
        Binding("ctrl+l", "clear_chat", "Clear", key_display="Ctrl+L"),
        Binding("ctrl+n", "new_session", "New Session", key_display="Ctrl+N"),
        Binding("ctrl+c", "cancel_agent", "Cancel", key_display="Ctrl+C", show=False),
        Binding("escape", "cancel_agent", "Cancel", show=False),
    ]

    current_agent = reactive("planner")
    is_running = reactive(False)
    has_messages = reactive(False)

    def __init__(self):
        super().__init__()
        self.graph = build_graph(checkpointer=MemorySaver())
        self.thread_id = str(uuid.uuid4())
        self.modified_files: list[str] = []
        self.active_tools: dict[str, dict] = {}
        self.printed_replies: set = set()
        self.total_tokens = 0
        self.total_turns = 0
        self.total_cost = 0.0
        self._abort_event: asyncio.Event = asyncio.Event()
        self._state_lock: asyncio.Lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        with Horizontal():
            with Vertical(id="main-area"):
                yield StatusBar(id="status-bar")
                yield WelcomeView(id="welcome")
                yield RichLog(
                    id="chat-log", highlight=True, markup=True,
                    wrap=True, max_lines=10000,
                )
                yield ChatInput(id="chat-input")
            yield ToolSidebar(id="sidebar")
        yield Footer()

    async def on_mount(self) -> None:
        # Hide chat log initially, show welcome
        self.query_one("#chat-log", RichLog).display = False
        self.query_one("#welcome", WelcomeView).display = True

        # Init status bar
        sb = self.query_one("#status-bar", StatusBar)
        sb.session_id = self.thread_id
        sb.agent = self.current_agent

        # Lifecycle hook
        if LIFECYCLE_HOOKS:
            await run_lifecycle_hook("session_start", {"thread_id": self.thread_id})

        # Notify
        self.notify(f"Session started: {self.thread_id[:8]}...", severity="information")

    def watch_current_agent(self, agent: str) -> None:
        try:
            sb = self.query_one("#status-bar", StatusBar)
            sb.agent = agent
        except NoMatches:
            pass

    def watch_has_messages(self, has: bool) -> None:
        """Switch between welcome view and chat log."""
        try:
            self.query_one("#welcome", WelcomeView).display = not has
            self.query_one("#chat-log", RichLog).display = has
        except NoMatches:
            pass

    # ── Input Handling ─────────────────────────────────────

    @on(ChatInput.Submitted)
    async def _on_chat_submit(self, event: ChatInput.Submitted) -> None:
        value = event.value.strip()
        if not value:
            return

        # Handle commands
        if value.startswith("/"):
            handled = await self._handle_command(value)
            if handled:
                return

        # Determine agent mode from prefix
        active = self.current_agent
        text = value
        if text.startswith("/plan "):
            active = "planner"
            text = text[6:]
        elif text.startswith("/code "):
            active = "coder"
            text = text[6:]
        elif text.startswith("/doc "):
            active = "doc"
            text = text[5:]
            text += "\n\n[SYSTEM: Prioritize documentation.]"

        if active == "doc":
            active = "planner"

        if not text.strip():
            return

        # Lifecycle hook
        if LIFECYCLE_HOOKS:
            modified = await run_lifecycle_hook(
                "user_prompt_submit",
                {"prompt": text, "agent": active},
            )
            if modified:
                text = modified

        # Switch to chat view
        self.has_messages = True

        # Show user message
        chat = self.query_one("#chat-log", RichLog)
        chat.write(Text(f"\n You > {value}", style="bold cyan"))
        chat.write(Text(""))

        # Run agent
        self.is_running = True
        self._abort_event.clear()  # Reset abort flag for new message
        ci = self.query_one("#chat-input", ChatInput)
        ci.disable()
        sb = self.query_one("#status-bar", StatusBar)
        sb.busy = True

        self._run_agent(text, active)

    async def _handle_command(self, cmd: str) -> bool:
        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command in ("/exit", "/quit"):
            await self.action_quit_app()
            return True
        elif command == "/clear":
            self.action_clear_chat()
            return True
        elif command == "/help":
            self._show_help()
            return True
        elif command == "/new":
            self.action_new_session()
            return True
        elif command == "/theme":
            self.theme = "textual-light" if self.theme == "textual-dark" else "textual-dark"
            self.notify(f"Theme: {self.theme}")
            return True
        elif command in ("/plan", "/code", "/doc"):
            # If there's text after, treat as message
            if len(parts) > 1:
                return False
            self.current_agent = {"plan": "planner", "code": "coder", "doc": "planner"}.get(
                command[1:], "planner"
            )
            self.notify(f"Agent: {self.current_agent.upper()}")
            return True

        return False

    # ── Agent Execution ────────────────────────────────────

    @work(exclusive=True)
    async def _run_agent(self, message: str, active_mode: str) -> None:
        chat = self.query_one("#chat-log", RichLog)
        sidebar = self.query_one("#sidebar", ToolSidebar)
        sb = self.query_one("#status-bar", StatusBar)

        graph_config = {
            "configurable": {"thread_id": self.thread_id},
            "recursion_limit": 100,
        }
        input_state = {
            "messages": [HumanMessage(content=message)],
            "workspace": config.WORKSPACE_DIR,
            "active_agent": active_mode,
        }

        buffer = ""
        current_agent = active_mode
        self.total_turns += 1
        sb.turns = self.total_turns

        try:
            async for event in self.graph.astream_events(
                input_state, config=graph_config, version="v2"
            ):
                # Check for cancellation between each streaming chunk
                if self._abort_event.is_set():
                    chat.write(Text("  Cancelled.", style="bold yellow"))
                    break

                kind = event.get("event", "")
                data = event.get("data", {})

                # Agent handoff
                if "output" in data and isinstance(data["output"], dict):
                    new_agent = data["output"].get("active_agent")
                    if new_agent and new_agent != current_agent:
                        # Flush buffer
                        if buffer.strip():
                            chat.write(Markdown(self._clean_buffer(buffer)))
                            buffer = ""
                        current_agent = new_agent
                        self.current_agent = current_agent
                        direction = ">>" if current_agent == "coder" else "<<"
                        color = AGENT_COLORS.get(current_agent, "white")
                        chat.write(Text(
                            f"  {direction} {current_agent.upper()}",
                            style=f"bold {color}",
                        ))
                        sidebar.log_agent(current_agent)

                # Streaming text
                if kind == "on_chat_model_stream":
                    chunk = data.get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        if isinstance(content, str):
                            buffer += content
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, str):
                                    buffer += block
                                elif isinstance(block, dict) and block.get("type") == "text":
                                    buffer += block["text"]

                        # Progressive render: flush on double newline
                        if "\n\n" in buffer:
                            parts = buffer.rsplit("\n\n", 1)
                            to_render = parts[0]
                            buffer = parts[1] if len(parts) > 1 else ""
                            clean = self._clean_buffer(to_render)
                            if clean:
                                chat.write(Markdown(clean))

                # Tool START
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "unknown")
                    run_id = event.get("run_id", str(uuid.uuid4()))
                    args = data.get("input", {})

                    if tool_name == "reply_to_user":
                        msg = args.get("message", "") if isinstance(args, dict) else ""
                        async with self._state_lock:
                            already_printed = run_id in self.printed_replies
                        if msg and not already_printed:
                            if buffer.strip():
                                chat.write(Markdown(self._clean_buffer(buffer)))
                                buffer = ""
                            chat.write(Markdown(str(msg)))
                            async with self._state_lock:
                                self.printed_replies.add(run_id)
                        continue

                    label = _tool_label(tool_name, args if isinstance(args, dict) else {})
                    sidebar.log_tool_start(label)
                    async with self._state_lock:
                        self.active_tools[run_id] = {
                            "start": time.time(),
                            "tool_name": tool_name,
                            "args": args if isinstance(args, dict) else {},
                        }

                # Tool END
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "unknown")
                    run_id = event.get("run_id", "")
                    output = data.get("output", "")
                    args = data.get("input", {})

                    if tool_name == "reply_to_user":
                        async with self._state_lock:
                            already_printed = run_id in self.printed_replies
                        if not already_printed:
                            msg = args.get("message", "") if isinstance(args, dict) else ""
                            if msg:
                                if buffer.strip():
                                    chat.write(Markdown(self._clean_buffer(buffer)))
                                    buffer = ""
                                chat.write(Markdown(str(msg)))
                                async with self._state_lock:
                                    self.printed_replies.add(run_id)
                        continue

                    async with self._state_lock:
                        tool_info = self.active_tools.pop(run_id, None)
                    elapsed = time.time() - tool_info["start"] if tool_info else 0
                    elapsed_str = _fmt_elapsed(elapsed)
                    a = args if isinstance(args, dict) else {}
                    label = _tool_label(tool_name, a)

                    sidebar.log_tool_end(label, elapsed_str)

                    # Track modified files
                    if tool_name in ("file_edit", "file_write", "file_edit_batch"):
                        target = a.get("file_path", "")
                        if target:
                            async with self._state_lock:
                                if target not in self.modified_files:
                                    self.modified_files.append(target)
                                    sidebar.add_file(target)

                    # Show diffs inline
                    if tool_name in ("file_edit", "file_write"):
                        out_str = output if isinstance(output, str) else (
                            output.content if hasattr(output, "content") else str(output)
                        )
                        if "Diff:" in out_str:
                            diff_str = out_str.split("Diff:", 1)[1].strip()[:600]
                            target = a.get("file_path", "file")
                            chat.write(Text(
                                f"  Edit: {_short_path(target)} ({elapsed_str})",
                                style="green",
                            ))
                            chat.write(Syntax(diff_str, "diff", theme="monokai", line_numbers=False))

                    # Show terminal output
                    elif tool_name == "terminal_exec":
                        out_str = output if isinstance(output, str) else (
                            output.content if hasattr(output, "content") else str(output)
                        )
                        cmd = a.get("command", "")
                        chat.write(Text(f"  $ {cmd} ({elapsed_str})", style="green"))
                        if out_str.strip():
                            lines = out_str.strip().split("\n")[:6]
                            for line in lines:
                                chat.write(Text(f"    {line}", style="dim"))
                            total = len(out_str.strip().split("\n"))
                            if total > 6:
                                chat.write(Text(f"    ... ({total - 6} more lines)", style="dim"))

            # Flush remaining buffer
            if buffer.strip():
                clean = self._clean_buffer(buffer)
                if clean:
                    chat.write(Markdown(clean))

            self.notify("Task complete", severity="information")

        except Exception as e:
            err_msg = str(e)
            if "cudaMalloc" in err_msg or "out of memory" in err_msg.lower():
                chat.write(Text("  ERROR: GPU out of memory (OOM)", style="bold red"))
                chat.write(Text("  Tip: Restart Ollama or use smaller model", style="dim"))
            elif "ConnectError" in err_msg or "connection" in err_msg.lower():
                chat.write(Text("  ERROR: Cannot connect to LLM provider", style="bold red"))
            else:
                chat.write(Text(f"  ERROR: {err_msg[:200]}", style="bold red"))

            sidebar.log_tool_error("Agent", err_msg[:50])
            self.notify(f"Error: {err_msg[:60]}", severity="error")

        finally:
            async with self._state_lock:
                self.active_tools.clear()
            self.is_running = False
            sb.busy = False
            ci = self.query_one("#chat-input", ChatInput)
            ci.enable()
            chat.write(Text(""))

            if LIFECYCLE_HOOKS:
                await run_lifecycle_hook("stop", {"thread_id": self.thread_id})

    # ── Utilities ──────────────────────────────────────────

    def _clean_buffer(self, buffer: str) -> str:
        clean = buffer
        clean = re.sub(
            r'```json\s*\{\s*"name"[\s\S]*?(?:```|$)', '', clean, flags=re.IGNORECASE
        )
        clean = re.sub(
            r'\{\s*"name"\s*:\s*"[^"]+\"[\s\S]*?"arguments"\s*:[\s\S]*?(?:\}|$)',
            '', clean, flags=re.IGNORECASE,
        )
        stripped = clean.strip()
        if stripped in ("{", "}", "{}", "{\n}", "{\n  \n}"):
            return ""
        return stripped

    def _show_help(self) -> None:
        self.has_messages = True
        chat = self.query_one("#chat-log", RichLog)
        help_md = f"""## ShadowDev TUI v{VERSION}

### Commands
| Command | Description |
|---------|-------------|
| `/plan <msg>` | Send as Planner |
| `/code <msg>` | Send as Coder |
| `/doc <msg>` | Send as Doc agent |
| `/clear` | Clear chat |
| `/new` | New session |
| `/theme` | Toggle theme |
| `/help` | This help |
| `/exit` | Quit |

### Keybindings
| Key | Action |
|-----|--------|
| **Enter** | Send message |
| **Shift+Enter** | New line |
| **Tab** | Switch agent |
| **Ctrl+P** | Command palette |
| **Ctrl+N** | New session |
| **Ctrl+L** | Clear chat |
| **Ctrl+Q** | Quit |

### Agent Modes
- **Planner** — Analyzes, plans, reads code
- **Coder** — Writes, edits, runs commands
- **Doc** — Documentation focus
"""
        chat.write(Markdown(help_md))

    # ── Actions ────────────────────────────────────────────

    async def action_command_palette(self) -> None:
        result = await self.push_screen(CommandPalette())
        if result:
            await self._handle_command(result)

    def action_switch_agent(self) -> None:
        agents = ["planner", "coder"]
        idx = agents.index(self.current_agent) if self.current_agent in agents else 0
        self.current_agent = agents[(idx + 1) % len(agents)]
        self.notify(f"Agent: {self.current_agent.upper()}")
        sidebar = self.query_one("#sidebar", ToolSidebar)
        sidebar.log_agent(self.current_agent)

    def action_clear_chat(self) -> None:
        chat = self.query_one("#chat-log", RichLog)
        chat.clear()
        self.has_messages = False
        self.notify("Chat cleared")

    def action_new_session(self) -> None:
        self.thread_id = str(uuid.uuid4())
        # Note: no lock needed here since new_session is only callable when not running
        self.modified_files.clear()
        self.active_tools.clear()
        self.printed_replies.clear()
        self._abort_event.clear()
        self.total_tokens = 0
        self.total_turns = 0
        self.total_cost = 0.0
        self.current_agent = "planner"

        # Reset UI
        chat = self.query_one("#chat-log", RichLog)
        chat.clear()
        self.has_messages = False

        sb = self.query_one("#status-bar", StatusBar)
        sb.session_id = self.thread_id
        sb.tokens = 0
        sb.turns = 0
        sb.cost = 0.0

        # Reset sidebar
        tree = self.query_one("#files-tree", Tree)
        tree.root.remove_children()
        tool_log = self.query_one("#tool-log", RichLog)
        tool_log.clear()

        self.notify(f"New session: {self.thread_id[:8]}...")

    def action_cancel_agent(self) -> None:
        """Cancel the running agent (Ctrl+C / Escape)."""
        if self.is_running:
            self._abort_event.set()
            self.notify("Cancelling...", severity="warning")

    async def action_quit_app(self) -> None:
        if LIFECYCLE_HOOKS:
            await run_lifecycle_hook("session_end", {"thread_id": self.thread_id})
        self.exit()


# ── Entry point ────────────────────────────────────────────

def main():
    app = ShadowDevTUI()
    app.run()


if __name__ == "__main__":
    main()
