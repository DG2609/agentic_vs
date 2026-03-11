# API Reference

ShadowDev exposes both an HTTP API (aiohttp) and real-time Socket.IO events. The server runs on `HOST:PORT` (default: `0.0.0.0:8000`).

## Authentication

If `API_KEY` is set in configuration, clients must include it in requests:
- HTTP: `x-api-key` header or `api_key` query parameter
- Socket.IO: `api_key` field in the connection payload

## HTTP Endpoints

### GET /api/health

Server health check and configuration summary.

**Response:**

```json
{
  "status": "ok",
  "provider": "openai",
  "model": "gpt-4o",
  "workspace": "/home/user/project",
  "tools_loaded": 65
}
```

### GET /api/files?path=

List files in a workspace directory.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | query string | Relative path within workspace (default: root) |

**Response:**

```json
{
  "files": [
    {"name": "src", "type": "directory"},
    {"name": "README.md", "type": "file", "size": 2048}
  ]
}
```

### GET /api/file?path=

Read a file's contents.

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | query string | Relative path to file within workspace |

**Response:**

```json
{
  "path": "src/main.py",
  "content": "import sys\n..."
}
```

### POST /api/file

Write content to a file.

**Request body:**

```json
{
  "path": "src/main.py",
  "content": "import sys\n\ndef main():\n    pass\n"
}
```

**Response:**

```json
{"status": "ok", "path": "src/main.py"}
```

### POST /api/file/revert

Revert an AI edit by writing the original content back.

**Request body:**

```json
{
  "path": "src/main.py",
  "content": "original file content..."
}
```

**Response:**

```json
{"status": "ok", "path": "src/main.py"}
```

### GET /api/workspace

Get the current workspace path.

**Response:**

```json
{"workspace": "/home/user/project"}
```

### POST /api/workspace

Set the workspace directory.

**Request body:**

```json
{"workspace": "/home/user/other-project"}
```

**Response:**

```json
{"status": "ok", "workspace": "/home/user/other-project"}
```

### GET /api/model

Get current LLM provider and model info.

**Response:**

```json
{
  "provider": "openai",
  "model": "gpt-4o",
  "fast_model": "gpt-4o-mini"
}
```

### GET /api/search?q=

Search for files by name in the workspace.

| Parameter | Type | Description |
|-----------|------|-------------|
| `q` | query string | Search query |

**Response:**

```json
{
  "results": [
    {"path": "src/utils.py", "type": "file"},
    {"path": "src/utils_test.py", "type": "file"}
  ]
}
```

### GET /api/git/status

Get git status of the workspace.

**Response:**

```json
{
  "branch": "main",
  "clean": false,
  "modified": ["src/main.py"],
  "untracked": ["new_file.py"]
}
```

---

## Socket.IO Events

### Client to Server (3 events)

#### `chat:message`

Send a message to the agent.

**Payload:**

```json
{
  "message": "Refactor the auth module",
  "thread_id": "unique-thread-id",
  "mode": "code",
  "api_key": "optional-api-key"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `message` | Yes | User message text |
| `thread_id` | No | Thread ID for persistence (auto-generated if omitted) |
| `mode` | No | Agent mode: `"code"` (default), `"plan"` |
| `api_key` | No | API key if server authentication is enabled |

#### `chat:stop`

Cancel a running generation.

**Payload:**

```json
{"thread_id": "unique-thread-id"}
```

#### `agent:resume`

Resume execution after a human-in-the-loop pause (when the agent asks a question).

**Payload:**

```json
{
  "thread_id": "unique-thread-id",
  "answer": "Yes, proceed with the refactor"
}
```

---

### Server to Client (11 events)

#### `connected`

Emitted immediately after WebSocket connection.

```json
{"status": "ok", "sid": "socket-session-id"}
```

#### `text`

Streamed LLM text chunk (emitted multiple times during generation).

```json
{"content": "Let me analyze the "}
```

#### `tool:start`

Tool execution has started.

```json
{
  "tool_name": "file_read",
  "tool_id": "call_abc123",
  "arguments": {"file_path": "src/main.py"}
}
```

#### `tool:end`

Tool execution has completed.

```json
{
  "tool_name": "file_read",
  "tool_id": "call_abc123",
  "status": "success",
  "result": "import sys\n..."
}
```

#### `file:diff`

A file was edited (for rendering diffs in the UI).

```json
{
  "path": "src/main.py",
  "original": "old content...",
  "modified": "new content..."
}
```

#### `agent:question`

The agent is asking the user a question (human-in-the-loop).

```json
{
  "thread_id": "unique-thread-id",
  "text": "Should I also update the tests?",
  "options": ["Yes", "No", "Skip tests"],
  "multiple": false
}
```

Respond with `agent:resume` to continue.

#### `agent:interrupt`

Execution is paused waiting for user input.

```json
{
  "thread_id": "unique-thread-id",
  "text": "I need clarification on the API design",
  "options": []
}
```

#### `index:progress`

Codebase indexing progress update.

```json
{
  "indexed": 150,
  "skipped": 23,
  "errors": 0,
  "total": 500,
  "active": true
}
```

#### `title`

Suggested conversation title (generated from first message).

```json
{"content": "Refactor auth module"}
```

#### `done`

Generation has finished.

```json
{
  "stopped": false,
  "interrupted": false
}
```

| Field | Description |
|-------|-------------|
| `stopped` | `true` if cancelled via `chat:stop` |
| `interrupted` | `true` if paused for human-in-the-loop |

#### `error`

An error occurred during processing.

```json
{"content": "Agent not initialized"}
```

---

## Connection Example (JavaScript)

```javascript
import { io } from "socket.io-client";

const socket = io("http://localhost:8000");

socket.on("connected", (data) => {
  console.log("Connected:", data.sid);

  socket.emit("chat:message", {
    message: "Explain the architecture of this project",
    thread_id: "my-session",
  });
});

socket.on("text", (data) => {
  process.stdout.write(data.content);
});

socket.on("tool:start", (data) => {
  console.log(`\n[Tool] ${data.tool_name}...`);
});

socket.on("tool:end", (data) => {
  console.log(`[Tool] ${data.tool_name} done`);
});

socket.on("done", (data) => {
  console.log("\nDone!");
  if (!data.interrupted) socket.disconnect();
});

socket.on("agent:question", (data) => {
  // Handle human-in-the-loop
  socket.emit("agent:resume", {
    thread_id: data.thread_id,
    answer: "Yes",
  });
});
```

## Connection Example (Python)

```python
import socketio

sio = socketio.Client()

@sio.on("connected")
def on_connect(data):
    sio.emit("chat:message", {
        "message": "List all Python files in the project",
        "thread_id": "my-session",
    })

@sio.on("text")
def on_text(data):
    print(data["content"], end="", flush=True)

@sio.on("done")
def on_done(data):
    sio.disconnect()

sio.connect("http://localhost:8000")
sio.wait()
```
