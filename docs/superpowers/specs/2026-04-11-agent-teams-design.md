# Agent Teams — Design Spec
**Date:** 2026-04-11  
**Status:** Approved  
**Feature:** Coordinator/Worker multi-agent system (closes Claude Code competitive gap)

---

## 1. Goal

Add a full **Agent Teams / Coordinator Mode** to ShadowDev. Currently, subagents are one-shot (fire-and-return). This feature adds:

- A **Coordinator** lead agent that manages a team of long-lived async workers
- Workers that **report back** via task notifications and can be **continued by ID**
- A **review loop** that auto-verifies every implementation and retries on failure
- A **shared scratchpad** for cross-worker knowledge
- **Never-stop** behavior: errors are caught, retried with adaptive strategy, never crash the coordinator

Directly closes the #1 competitive gap vs Claude Code (Agent Teams / multi-session coordination).

---

## 2. Architecture Overview

```
User
  │
  ▼
Coordinator Agent  ←── activated by --team flag or /team command
  │  (coordinator system prompt, manages all workers)
  │
  ├─ worker_spawn("Explore auth bug", ...)   → worker-abc  (asyncio Task)
  ├─ worker_spawn("Explore test suite", ...) → worker-def  (asyncio Task)
  │
  │  Workers run fully async. Each has its own LLM loop + tool set.
  │  When done, inject <task-notification> into coordinator's message stream.
  │
  ├─── <task-notification id="worker-abc" status="completed"> → coordinator reads result
  ├─── worker_message("worker-abc", "Fix validate.ts:42 ...") → continues same worker
  │
  ├─ [review_loop auto-spawns reviewer after every implementation worker]
  │    reviewer → PASSED ✅  → coordinator continues
  │    reviewer → FAILED ❌  → coordinator retries fix (max 3 attempts)
  │
  └─ team_status() → live table of all workers (id, role, status, duration)
```

---

## 3. New Files

### `agent/team/__init__.py`
Empty package init.

### `agent/team/worker_pool.py`
The async worker pool — the heart of the system.

**Responsibilities:**
- Maintains a `dict[str, WorkerEntry]` registry keyed by worker UUID
- Each `WorkerEntry` holds: `task: asyncio.Task`, `messages: list`, `status`, `role`, `start_time`
- `spawn(prompt, role, tools, system_prompt, max_steps)` → creates asyncio Task running `_worker_loop`, returns UUID
- `send_message(worker_id, message)` → appends a new HumanMessage to the worker's message queue; worker picks it up on next iteration
- `stop(worker_id)` → cancels the asyncio Task, sets status=stopped
- `get_status()` → returns list of all worker summaries
- `notification_queue: asyncio.Queue` — workers push `TaskNotification` objects here when done/failed; coordinator polls this queue and injects notifications as HumanMessages

**Worker loop** (`_worker_loop`):
```
while steps < max_steps:
    response = await llm.invoke(messages)
    if no tool_calls and no pending_messages:
        push notification(status=completed, result=response.content)
        return
    execute tool calls (parallel)
    check for pending messages from coordinator → append as HumanMessage
    steps += 1
push notification(status=max_steps_reached)
```

**Error handling:** Any exception inside `_worker_loop` is caught, pushes `notification(status=failed, error=str(e))`, never propagates.

### `agent/team/coordinator.py`
Coordinator mode detection and system prompt.

**`is_coordinator_mode() → bool`**  
Reads `config.COORDINATOR_MODE` (bool, default False). Set by `--team` flag or `/team` command.

**`get_coordinator_system_prompt() → str`**  
Full coordinator system prompt modeled on the Claude Code coordinator prompt (from `claude-code-leak_ref/src/coordinator/coordinatorMode.ts`). Key sections:
- Role: orchestrate workers, synthesize results, communicate with user
- Tools: `worker_spawn`, `worker_message`, `worker_stop`, `team_status`, `team_create`
- Workflow: Research (parallel) → Synthesis (coordinator) → Implementation (workers) → Verification (auto)
- Never fabricate worker results — wait for notifications
- Retry rules: on FAILED review, continue same worker with corrected spec (up to 3 times)

### `agent/team/tools.py`
Five new LangChain tools the coordinator uses:

**`worker_spawn(prompt, role, description, max_steps=30) → str`**
- Role options: `"explorer"`, `"coder"`, `"reviewer"`, `"general"`
- Selects tool set by role (mirrors existing `_get_explore_tools()`, `_get_reviewer_tools()`, `_get_general_tools()`)
- Returns worker UUID + launch confirmation
- Fire-and-forget (async Task); result arrives via notification

**`worker_message(worker_id, message) → str`**
- Appends message to the named worker's pending queue
- Worker picks it up in its next iteration (within ~1s)
- Returns confirmation or "worker not found / already stopped"

**`worker_stop(worker_id) → str`**
- Cancels the asyncio Task for the given worker
- Returns final status

**`team_status() → str`**
- Returns a formatted table of all workers: ID, description, role, status (running/completed/failed/stopped), duration, step count

**`team_create(name, description) → str`**
- Creates a named team group (stored in pool as tag on workers)
- Useful for labeling a batch of related workers (e.g. "auth-fix-team")

**`team_delete(name) → str`**
- Stops all workers in the named team, removes the group tag

### `agent/team/scratchpad.py`
Shared R/W directory for cross-worker knowledge.

- Root: `.shadowdev/team/scratchpad/` (relative to workspace)
- `write(filename, content)` — any worker can write findings
- `read(filename) → str` — any worker can read what others wrote
- `list_files() → list[str]` — discover what's been written
- Used automatically: coordinator prompt tells workers about scratchpad dir

### `agent/team/review_loop.py`
Auto review-and-retry orchestration.

**`ReviewLoop`** class:
- `max_retries: int = 3`
- `attempt: int = 0`
- `last_worker_id: str`
- `async trigger_review(worker_id, changed_files) → ReviewResult`
  - Spawns a reviewer worker with the list of changed files
  - Waits for notification (polls `notification_queue`)
  - Parses PASSED/FAILED from result text
  - If FAILED and `attempt < max_retries`: sends corrected spec back to `last_worker_id` via `worker_message`, increments `attempt`
  - If FAILED and exhausted: returns `ReviewResult(passed=False, exhausted=True, issues=...)`
  - If PASSED: returns `ReviewResult(passed=True)`

---

## 4. Modified Files

### `agent/graph.py`
- Import `agent/team/tools.py` tools: `worker_spawn`, `worker_message`, `worker_stop`, `team_status`, `team_create`, `team_delete`
- Add all team tools to `PLANNER_TOOLS` (coordinator is a planner-class agent)
- In `build_graph()`: if `is_coordinator_mode()`, use `get_coordinator_system_prompt()` as the system prompt override for the planner node

### `agent/nodes.py`
- In `agent_node()`: if coordinator mode, inject pending `TaskNotification` messages from the pool's `notification_queue` as synthetic HumanMessages before calling LLM
- Add `coordinator_mode` flag to `AgentState` (optional bool)

### `models/state.py`
- Add `coordinator_mode: bool = False` to `AgentState`
- Add `team_notifications: list[str]` to buffer incoming worker notifications

### `config.py`
- Add `COORDINATOR_MODE: bool = os.getenv("SHADOWDEV_COORDINATOR", "false").lower() == "true"`
- Add `TEAM_MAX_RETRIES: int = int(os.getenv("SHADOWDEV_TEAM_MAX_RETRIES", "3"))`
- Add `TEAM_WORKER_MAX_STEPS: int = int(os.getenv("SHADOWDEV_WORKER_MAX_STEPS", "30"))`
- Add `TEAM_SCRATCHPAD_DIR: str = os.getenv("SHADOWDEV_SCRATCHPAD", ".shadowdev/team/scratchpad")`

### `cli.py`
- Add `--team` / `-T` flag → sets `config.COORDINATOR_MODE = True`
- Add `/team` slash command alias

### `tui.py`
- When coordinator mode active: show "COORDINATOR" in StatusBar model slot
- ToolSidebar shows worker activity log (worker ID, role, status, step count)

---

## 5. Data Flow — Full Example

```
User: "fix the null pointer in auth and add tests"

Coordinator:
  worker_spawn("Explore auth module for null pointer", role="explorer") → worker-a1b
  worker_spawn("Explore test suite for auth", role="explorer")          → worker-d4e
  → "Investigating from two angles..."

<task-notification id="worker-a1b" status="completed">
  Found NPE in src/auth/validate.py:42 — user field undefined on expired sessions
</task-notification>

Coordinator:
  [synthesizes: validate.py:42, user field, Session expiry]
  worker_message("worker-a1b", "Fix NPE in validate.py:42. Add null check before user.id access. If null, raise AuthError('Session expired'). Run tests. Commit.")
  → "Fix in progress..."

<task-notification id="worker-a1b" status="completed">
  Fixed. Tests pass. Commit: abc123
</task-notification>

Coordinator:
  [review_loop.trigger_review("worker-a1b", ["src/auth/validate.py"])]
  worker_spawn("Review validate.py changes", role="reviewer") → worker-r7f

<task-notification id="worker-r7f" status="completed">
  FAILED ❌ — lsp_diagnostics found unhandled import in validate.py:3
</task-notification>

Coordinator:
  [attempt=1, max_retries=3]
  worker_message("worker-a1b", "Fix unhandled import on line 3 of validate.py. Re-run tests. Commit.")

<task-notification id="worker-a1b" status="completed">
  Fixed import. All tests pass. Commit: def456
</task-notification>

Coordinator:
  worker_spawn("Re-verify validate.py", role="reviewer") → worker-r8g

<task-notification id="worker-r8g" status="completed">
  PASSED ✅ — All diagnostics clean, 12/12 tests pass
</task-notification>

Coordinator:
  "Done. Fixed NPE in validate.py:42, added null check, all 12 tests pass. Commit: def456"
```

---

## 6. Error Handling & Never-Stop Rules

| Scenario | Behavior |
|----------|----------|
| Worker throws exception | Caught inside `_worker_loop`, pushes `failed` notification, pool continues |
| Worker hits max_steps | Pushes `max_steps_reached` notification; coordinator decides to continue or spawn fresh |
| Review FAILED 3× | Coordinator reports failure to user with full issue list; does NOT crash |
| Worker not found (bad ID) | `worker_message` returns error string; coordinator retries with `team_status()` to find correct ID |
| LLM API error in worker | Exponential backoff (3 attempts), then `failed` notification |
| Coordinator LLM error | Existing `_invoke_with_retry` in `nodes.py` handles it (already in codebase) |
| asyncio Task cancelled | WorkerEntry status set to `stopped`; coordinator sees notification |

---

## 7. Testing

- `tests/team/test_worker_pool.py` — spawn/stop/message/notification flow (mock LLM)
- `tests/team/test_coordinator.py` — coordinator prompt generation, mode detection
- `tests/team/test_review_loop.py` — PASSED path, FAILED→retry path, exhausted path
- `tests/team/test_tools.py` — all 6 team tools, happy path + error cases
- `tests/team/test_scratchpad.py` — read/write/list, concurrent writes
- Integration test: `tests/team/test_integration.py` — full coordinator→2 workers→reviewer flow with mock LLM

Target: 40+ new tests, maintaining 469+ pass rate.

---

## 8. Competitive Positioning After This Feature

| Feature | ShadowDev (after) | Claude Code |
|---------|-------------------|-------------|
| Multi-agent coordination | ✅ Coordinator + N workers | ✅ Coordinator mode |
| Worker continuation by ID | ✅ `worker_message` | ✅ `SendMessage` |
| Team creation/deletion | ✅ `team_create/delete` | ✅ `TeamCreate/Delete` |
| Auto review-and-retry loop | ✅ `review_loop` (built-in) | ❌ (manual) |
| Shared scratchpad | ✅ `.shadowdev/team/scratchpad/` | ✅ (gated feature) |
| Never-stop on error | ✅ All exceptions caught | ✅ |
| Parallel worker fan-out | ✅ asyncio.gather | ✅ |
| TUI worker activity log | ✅ | ❌ |

ShadowDev **exceeds** Claude Code on auto review-retry loop and TUI integration.
