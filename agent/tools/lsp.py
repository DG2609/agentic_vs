"""
Tool: LSP (Language Server Protocol) Client.
Provides precise code intelligence — definition, references, hover,
document symbols, diagnostics. Currently supports Python (pyright).

Enhanced with 6 operations (inspired by OpenCode's lsp.ts with 9 ops):
- lsp_definition: Go to definition
- lsp_references: Find all references
- lsp_hover: Get type/docs on hover
- lsp_symbols: Document symbol outline
- lsp_diagnostics: Get file errors/warnings
- lsp_rename_preview: Preview rename changes
"""
import logging
import os
import shutil
import sys
import json
import subprocess
import threading
import time
from typing import Optional, Dict, Any, List
from langchain_core.tools import tool
import config
from agent.tools.truncation import truncate_output
from models.tool_schemas import LSPPositionArgs, LSPFileArgs

logger = logging.getLogger(__name__)

# Check pyright availability at module load — avoids silent failure at runtime
_PYRIGHT_PATH = shutil.which("pyright")
if not _PYRIGHT_PATH:
    logger.warning(
        "[lsp] pyright not found on PATH — lsp_diagnostics will be unavailable. "
        "Install with: npm install -g pyright  OR  pip install pyright"
    )


class LSPClient:
    def __init__(self, command: list):
        self.command = command
        self.process: Optional[subprocess.Popen] = None
        self.recv_thread: Optional[threading.Thread] = None
        self.responses: Dict[int, Any] = {}
        self.requests: Dict[int, threading.Event] = {}
        self.request_id = 0
        self.lock = threading.Lock()
        self.capabilities = {}
        self.initialized = False

    def start(self):
        if self.process and self.process.poll() is None:
            return

        print(f"Starting LSP server: {' '.join(self.command)}")
        # Build a clean environment: keep PATH and essential vars, strip secrets.
        _KEEP_VARS = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
                       "SYSTEMROOT", "TEMP", "TMP", "COMSPEC",  # Windows
                       "PYTHONPATH", "NODE_PATH", "VIRTUAL_ENV"}
        _SECRET_SUBSTRINGS = {"KEY", "SECRET", "TOKEN", "PASSWORD", "CREDENTIAL"}
        clean_env = {}
        for k, v in os.environ.items():
            k_upper = k.upper()
            if k_upper in _KEEP_VARS:
                clean_env[k] = v
            elif not any(s in k_upper for s in _SECRET_SUBSTRINGS):
                clean_env[k] = v
        # shell=True on Windows only: required for PATH resolution of node/pyright executables.
        # Command is a trusted server binary, not user input.
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=clean_env,
            shell=True if os.name == 'nt' else False
        )

        self.recv_thread = threading.Thread(target=self._receive_loop, daemon=True)
        self.recv_thread.start()

        self.stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self.stderr_thread.start()

        self.initialize()

    def _stderr_loop(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stderr.readline()
                if not line:
                    break
            except Exception:
                break

    def _receive_loop(self):
        while self.process and self.process.poll() is None:
            try:
                line = self.process.stdout.readline()
                if not line:
                    break

                line = line.decode('utf-8').strip()
                if not line.startswith("Content-Length:"):
                    continue

                length = int(line.split(":")[1].strip())
                self.process.stdout.readline()  # skip empty line
                body = self.process.stdout.read(length)
                msg = json.loads(body.decode('utf-8'))

                if "id" in msg:
                    req_id = msg["id"]
                    with self.lock:
                        if req_id in self.requests:
                            self.responses[req_id] = msg
                            self.requests[req_id].set()
            except Exception as e:
                print(f"LSP Read Error: {e}")
                break

        # Process died or read loop ended — wake all pending requests with an error
        # so callers don't hang for the full timeout.
        with self.lock:
            for req_id, event in list(self.requests.items()):
                if req_id not in self.responses:
                    self.responses[req_id] = {"error": "LSP process terminated unexpectedly"}
                    event.set()

    def send_request(self, method: str, params: dict) -> Any:
        self.start()

        # CRITICAL: check process is alive BEFORE registering request.
        # If we register first and the process is dead, _receive_loop may have
        # already exited — our event will never be set and we hang for 5 seconds.
        if self.process is None or self.process.poll() is not None:
            return {"error": "LSP server is not running"}

        with self.lock:
            self.request_id += 1
            req_id = self.request_id
            self.requests[req_id] = threading.Event()

        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params
        }

        body = json.dumps(msg)
        body_bytes = body.encode('utf-8')
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode('utf-8')

        try:
            self.process.stdin.write(header + body_bytes)
            self.process.stdin.flush()
        except Exception as e:
            with self.lock:
                self.requests.pop(req_id, None)
            return {"error": str(e)}

        # 5-second timeout (reduced from 10s) — industry standard for LSP
        if not self.requests[req_id].wait(timeout=5.0):
            # Clean up to prevent memory leak when response eventually arrives
            with self.lock:
                self.requests.pop(req_id, None)
                self.responses.pop(req_id, None)
            return {"error": "Timeout waiting for LSP response"}

        with self.lock:
            response = self.responses.pop(req_id, None)
            self.requests.pop(req_id, None)

        if not response:
            return {"error": "No response received from LSP server"}

        if "error" in response:
            return {"error": response["error"]}

        return response.get("result")

    def send_notification(self, method: str, params: dict):
        self.start()
        msg = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params
        }
        body = json.dumps(msg)
        body_bytes = body.encode('utf-8')
        header = f"Content-Length: {len(body_bytes)}\r\n\r\n".encode('utf-8')
        try:
            self.process.stdin.write(header + body_bytes)
            self.process.stdin.flush()
        except Exception:
            pass

    def initialize(self):
        root_uri = f"file:///{config.WORKSPACE_DIR.replace(os.sep, '/')}"
        result = self.send_request("initialize", {
            "processId": os.getpid(),
            "rootUri": root_uri,
            "capabilities": {
                "textDocument": {
                    "hover": {"contentFormat": ["plaintext", "markdown"]},
                    "definition": {"dynamicRegistration": False},
                    "references": {"dynamicRegistration": False},
                    "implementation": {"dynamicRegistration": False},
                    "documentSymbol": {
                        "hierarchicalDocumentSymbolSupport": True,
                    },
                    "publishDiagnostics": {"relatedInformation": True},
                    "rename": {"prepareSupport": True},
                    "callHierarchy": {"dynamicRegistration": False},
                },
                "workspace": {
                    "symbol": {"dynamicRegistration": False},
                },
            }
        })
        if result:
            self.capabilities = result.get("capabilities", {})
        self.send_notification("initialized", {})
        self.initialized = True
        return result

    def did_open(self, file_path: str, content: str, language_id: str = "python"):
        uri = _file_uri(file_path)
        self.send_notification("textDocument/didOpen", {
            "textDocument": {
                "uri": uri,
                "languageId": language_id,
                "version": 1,
                "text": content
            }
        })

    def definition(self, file_path: str, line: int, character: int):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/definition", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}
        })

    def references(self, file_path: str, line: int, character: int, include_declaration: bool = True):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/references", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character},
            "context": {"includeDeclaration": include_declaration}
        })

    def hover(self, file_path: str, line: int, character: int):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/hover", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}
        })

    def document_symbol(self, file_path: str):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/documentSymbol", {
            "textDocument": {"uri": uri}
        })

    def workspace_symbol(self, query: str):
        return self.send_request("workspace/symbol", {"query": query})

    def implementation(self, file_path: str, line: int, character: int):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/implementation", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}
        })

    def call_hierarchy_prepare(self, file_path: str, line: int, character: int):
        uri = _file_uri(file_path)
        return self.send_request("textDocument/prepareCallHierarchy", {
            "textDocument": {"uri": uri},
            "position": {"line": line, "character": character}
        })

    def call_hierarchy_incoming(self, item: dict):
        return self.send_request("callHierarchy/incomingCalls", {"item": item})

    def call_hierarchy_outgoing(self, item: dict):
        return self.send_request("callHierarchy/outgoingCalls", {"item": item})

    def shutdown(self):
        if self.process and self.process.poll() is None:
            self.send_request("shutdown", {})
            self.send_notification("exit", {})
            self.process.terminate()


# ── Helpers ─────────────────────────────────────────────────
_pyright_client = None
_lsp_unavailable_reason: str = ""


def _detect_python_lsp_cmd() -> list[str]:
    """Detect an available Python LSP server on PATH.

    Priority: pyright-langserver → pylsp → python -m pylsp
    Returns empty list if none found.
    """
    import shutil
    candidates = [
        (["pyright-langserver", "--stdio"], "pyright-langserver"),
        (["pylsp"], "pylsp"),
        ([sys.executable, "-m", "pylsp"], "pylsp (module)"),
    ]
    for cmd, label in candidates:
        binary = cmd[0]
        # For `sys.executable -m module` form, check if module importable
        if binary == sys.executable:
            try:
                import pylsp  # noqa: F401
                return cmd
            except ImportError:
                continue
        if shutil.which(binary):
            return cmd
    return []


def get_pyright():
    """Get (or lazily create) the Python LSP client.

    Auto-detects pyright-langserver, falls back to pylsp.
    Returns None if no LSP server is available.
    """
    global _pyright_client, _lsp_unavailable_reason

    if _pyright_client is not None:
        # Restart if process died
        if _pyright_client.process and _pyright_client.process.poll() is not None:
            _pyright_client = None
        else:
            return _pyright_client

    cmd = _detect_python_lsp_cmd()
    if not cmd:
        _lsp_unavailable_reason = (
            "No Python LSP server found. Install one:\n"
            "  pip install pyright    (recommended)\n"
            "  pip install python-lsp-server"
        )
        return None

    _lsp_unavailable_reason = ""
    _pyright_client = LSPClient(cmd)
    return _pyright_client


def _resolve_path(p: str) -> str:
    from agent.tools.utils import resolve_tool_path
    return resolve_tool_path(p)


def _file_uri(file_path: str) -> str:
    resolved = _resolve_path(file_path).replace(os.sep, '/')
    return f"file:///{resolved}"


def _parse_uri(uri: str) -> str:
    """Convert file URI to local path."""
    path = uri.replace("file:///", "")
    if os.name == 'nt' and path.startswith("/") and ":" in path:
        path = path[1:]
    return path


def _ensure_open(client: LSPClient | None, file_path: str) -> str | None:
    """Open file in LSP if not already open. Returns error string or None."""
    if client is None:
        return _lsp_unavailable_reason or "LSP server not available."
    resolved = _resolve_path(file_path)
    if not os.path.isfile(resolved):
        return f"Error: File {file_path} not found"
    try:
        with open(resolved, "r", encoding="utf-8") as f:
            content = f.read()
        client.did_open(file_path, content)
        return None  # no error
    except Exception as e:
        return f"Error reading file: {e}"


def _format_locations(locs) -> str:
    """Format LSP Location/Location[] results."""
    if not locs:
        return "No results found."

    if isinstance(locs, dict):
        locs = [locs]

    output = []
    for loc in locs:
        uri = loc.get("uri", loc.get("targetUri", ""))
        path = _parse_uri(uri)
        r = loc.get("range", loc.get("targetRange", {}))
        l = r.get("start", {}).get("line", 0)
        c = r.get("start", {}).get("character", 0)
        output.append(f"  {path}:{l+1}:{c+1}")

    return "\n".join(output)


# ── Symbol kind mapping ─────────────────────────────────────
SYMBOL_KINDS = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor",
    10: "Enum", 11: "Interface", 12: "Function", 13: "Variable",
    14: "Constant", 15: "String", 16: "Number", 17: "Boolean",
    18: "Array", 19: "Object", 20: "Key", 21: "Null",
    22: "EnumMember", 23: "Struct", 24: "Event", 25: "Operator",
    26: "TypeParameter",
}

DIAG_SEVERITY = {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}


# ── Tool definitions ────────────────────────────────────────

@tool(args_schema=LSPPositionArgs)
def lsp_definition(file_path: str, line: int, col: int) -> str:
    """Go to the definition of a symbol at the specified position.

    Args:
        file_path: Path to the file containing the symbol.
        line: Line number (0-indexed).
        col: Column number (0-indexed).

    Returns:
        Location(s) of the definition.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.definition(file_path, line, col)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"
        return f"Definition:\n{_format_locations(result)}"
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPPositionArgs)
def lsp_references(file_path: str, line: int, col: int) -> str:
    """Find all references to a symbol at the specified position.

    Args:
        file_path: Path to the file containing the symbol.
        line: Line number (0-indexed).
        col: Column number (0-indexed).

    Returns:
        All locations where the symbol is referenced.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.references(file_path, line, col)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No references found."

        locs = result if isinstance(result, list) else [result]
        output = [f"Found {len(locs)} reference(s):"]
        output.append(_format_locations(locs))
        return truncate_output("\n".join(output))
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPPositionArgs)
def lsp_hover(file_path: str, line: int, col: int) -> str:
    """Get type information and documentation for a symbol at the specified position.

    Args:
        file_path: Path to the file.
        line: Line number (0-indexed).
        col: Column number (0-indexed).

    Returns:
        Type info and documentation for the symbol.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.hover(file_path, line, col)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No hover information available."

        contents = result.get("contents", {})
        if isinstance(contents, dict):
            value = contents.get("value", str(contents))
        elif isinstance(contents, str):
            value = contents
        elif isinstance(contents, list):
            value = "\n".join(
                c.get("value", str(c)) if isinstance(c, dict) else str(c)
                for c in contents
            )
        else:
            value = str(contents)

        return f"Hover info:\n{value}"
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPFileArgs)
def lsp_symbols(file_path: str) -> str:
    """Get the symbol outline of a file (classes, functions, variables).

    Args:
        file_path: Path to the file to analyze.

    Returns:
        Hierarchical symbol outline with types and line numbers.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.document_symbol(file_path)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No symbols found."

        output = [f"Symbols in {file_path}:\n"]
        _format_symbols(result, output, indent=0)
        return truncate_output("\n".join(output))
    except Exception as e:
        return f"LSP Error: {e}"


def _format_symbols(symbols: list, output: list, indent: int):
    """Recursively format document symbols."""
    if not isinstance(symbols, list):
        return

    for sym in symbols:
        kind = SYMBOL_KINDS.get(sym.get("kind", 0), "?")
        name = sym.get("name", "?")
        r = sym.get("range", sym.get("selectionRange", {}))
        line = r.get("start", {}).get("line", 0) + 1
        prefix = "  " * indent
        output.append(f"{prefix}{kind}: {name} (L{line})")

        # Recurse into children
        children = sym.get("children", [])
        if children:
            _format_symbols(children, output, indent + 1)


@tool(args_schema=LSPFileArgs)
def lsp_diagnostics(file_path: str) -> str:
    """Get compiler/linter errors and warnings for a file.

    Useful after editing a file to check if the changes introduced errors.

    Args:
        file_path: Path to the file to check.

    Returns:
        List of errors, warnings, and hints from the language server.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    # Pyright publishes diagnostics via notifications, not request.
    # We need to trigger analysis and wait for textDocument/publishDiagnostics.
    # For now, use a simple CLI approach as fallback.
    if not _PYRIGHT_PATH:
        return "lsp_diagnostics unavailable: pyright not found. Install: npm install -g pyright"

    resolved = _resolve_path(file_path)

    try:
        # shell=True on Windows only: required to locate pyright on PATH.
        # Command is a trusted CLI tool; resolved path is workspace-sandboxed.
        result = subprocess.run(
            [_PYRIGHT_PATH, "--outputjson", resolved],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace",
            shell=True if os.name == 'nt' else False,
        )

        if result.stdout:
            data = json.loads(result.stdout)
            diagnostics = data.get("generalDiagnostics", [])
            if not diagnostics:
                return f"No diagnostics for {file_path}"

            output = [f"Diagnostics for {file_path}:\n"]
            for d in diagnostics[:50]:
                severity = d.get("severity", "unknown")
                msg = d.get("message", "")
                r = d.get("range", {})
                start = r.get("start", {})
                line = start.get("line", 0) + 1
                col = start.get("character", 0) + 1
                output.append(f"  [{severity}] L{line}:{col} — {msg}")

            return truncate_output("\n".join(output))
        else:
            return f"No diagnostics output for {file_path}"

    except FileNotFoundError:
        return "Error: pyright CLI not found. Install with: pip install pyright"
    except json.JSONDecodeError:
        return "Error: Could not parse pyright output"
    except subprocess.TimeoutExpired:
        return "Error: Pyright analysis timed out"
    except Exception as e:
        return f"Error getting diagnostics: {e}"


# ── New LSP tools (Fix 7) ──────────────────────────────────

from models.tool_schemas import LSPWorkspaceSymbolArgs, LSPCallHierarchyItemArgs  # noqa: E402


@tool(args_schema=LSPWorkspaceSymbolArgs)
def lsp_workspace_symbols(query: str) -> str:
    """Search for symbols across the entire workspace by name.

    Useful for finding all classes, functions, or variables matching a query
    without knowing which file they are in.

    Args:
        query: Search string to match against symbol names.

    Returns:
        List of matching symbols with file and line location.
    """
    client = get_pyright()
    if client is None:
        return _lsp_unavailable_reason or "LSP server not available."

    try:
        result = client.workspace_symbol(query)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return f"No workspace symbols found for '{query}'"

        symbols = result if isinstance(result, list) else [result]
        output = [f"Workspace symbols matching '{query}' ({len(symbols)}):\n"]
        for sym in symbols[:100]:
            name = sym.get("name", "?")
            kind = SYMBOL_KINDS.get(sym.get("kind", 0), "?")
            loc = sym.get("location", {})
            uri = loc.get("uri", "")
            file_path = _parse_uri(uri)
            r = loc.get("range", {})
            line = r.get("start", {}).get("line", 0) + 1
            output.append(f"  {kind}: {name}  →  {file_path}:{line}")

        return truncate_output("\n".join(output))
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPPositionArgs)
def lsp_go_to_implementation(file_path: str, line: int, col: int) -> str:
    """Find implementations of an interface or abstract method at the given position.

    Args:
        file_path: Path to the file containing the interface/abstract method.
        line: Line number (0-indexed).
        col: Column number (0-indexed).

    Returns:
        Locations where the interface or abstract method is implemented.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.implementation(file_path, line, col)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No implementations found."

        locs = result if isinstance(result, list) else [result]
        output = [f"Implementations ({len(locs)}):"]
        output.append(_format_locations(locs))
        return "\n".join(output)
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPPositionArgs)
def lsp_call_hierarchy_prepare(file_path: str, line: int, col: int) -> str:
    """Prepare call hierarchy at the cursor position.

    Returns a JSON CallHierarchyItem that can be passed to
    lsp_incoming_calls or lsp_outgoing_calls.

    Args:
        file_path: Path to the file.
        line: Line number (0-indexed).
        col: Column number (0-indexed).

    Returns:
        JSON string of the CallHierarchyItem, or an error message.
    """
    client = get_pyright()
    err = _ensure_open(client, file_path)
    if err:
        return err

    try:
        result = client.call_hierarchy_prepare(file_path, line, col)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No call hierarchy item found at this position."

        items = result if isinstance(result, list) else [result]
        if not items:
            return "No call hierarchy item found at this position."

        item = items[0]
        uri = item.get("uri", "")
        name = item.get("name", "?")
        kind = SYMBOL_KINDS.get(item.get("kind", 0), "?")
        r = item.get("range", {})
        line_no = r.get("start", {}).get("line", 0) + 1
        file_p = _parse_uri(uri)
        return (
            f"Call hierarchy item: {kind} '{name}' at {file_p}:{line_no}\n"
            f"JSON (pass to lsp_incoming_calls / lsp_outgoing_calls):\n"
            f"{json.dumps(item)}"
        )
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPCallHierarchyItemArgs)
def lsp_incoming_calls(item: str) -> str:
    """Get all callers of a function (who calls this function).

    Pass the JSON item string from lsp_call_hierarchy_prepare.

    Args:
        item: JSON-serialized CallHierarchyItem from lsp_call_hierarchy_prepare.

    Returns:
        List of functions that call the given function, with file:line locations.
    """
    client = get_pyright()
    if client is None:
        return _lsp_unavailable_reason or "LSP server not available."

    try:
        item_dict = json.loads(item)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON item — {e}"

    try:
        result = client.call_hierarchy_incoming(item_dict)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No incoming calls found."

        calls = result if isinstance(result, list) else [result]
        output = [f"Incoming calls ({len(calls)}):\n"]
        for call in calls[:50]:
            caller = call.get("from", {})
            name = caller.get("name", "?")
            kind = SYMBOL_KINDS.get(caller.get("kind", 0), "?")
            uri = caller.get("uri", "")
            file_p = _parse_uri(uri)
            r = caller.get("range", {})
            line_no = r.get("start", {}).get("line", 0) + 1
            output.append(f"  {kind}: {name}  →  {file_p}:{line_no}")

        return truncate_output("\n".join(output))
    except Exception as e:
        return f"LSP Error: {e}"


@tool(args_schema=LSPCallHierarchyItemArgs)
def lsp_outgoing_calls(item: str) -> str:
    """Get all functions called by this function (what does this function call).

    Pass the JSON item string from lsp_call_hierarchy_prepare.

    Args:
        item: JSON-serialized CallHierarchyItem from lsp_call_hierarchy_prepare.

    Returns:
        List of functions called by the given function, with file:line locations.
    """
    client = get_pyright()
    if client is None:
        return _lsp_unavailable_reason or "LSP server not available."

    try:
        item_dict = json.loads(item)
    except json.JSONDecodeError as e:
        return f"Error: invalid JSON item — {e}"

    try:
        result = client.call_hierarchy_outgoing(item_dict)  # type: ignore[union-attr]
        if isinstance(result, dict) and "error" in result:
            return f"LSP Error: {result['error']}"

        if not result:
            return "No outgoing calls found."

        calls = result if isinstance(result, list) else [result]
        output = [f"Outgoing calls ({len(calls)}):\n"]
        for call in calls[:50]:
            callee = call.get("to", {})
            name = callee.get("name", "?")
            kind = SYMBOL_KINDS.get(callee.get("kind", 0), "?")
            uri = callee.get("uri", "")
            file_p = _parse_uri(uri)
            r = callee.get("range", {})
            line_no = r.get("start", {}).get("line", 0) + 1
            output.append(f"  {kind}: {name}  →  {file_p}:{line_no}")

        return truncate_output("\n".join(output))
    except Exception as e:
        return f"LSP Error: {e}"
