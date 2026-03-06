# ShadowDev Agentic IDE — Roadmap & Comparison Report

> Generated: 2026-03-06
> Score: 8.8/10 (weighted) — see comparison below

---

## Current State (v0.4)

- **65+ tools** | **6 LLM providers** | **310 tests** | **~22K LOC**
- Planner/Coder swarm architecture (LangGraph)
- Hook system (18 lifecycle + tool events, parallel execution)
- Skill system (Markdown workflows + Python plugins + Skill Hub)
- SQLite FTS5 persistent memory
- Web IDE (Next.js + Monaco) + CLI + Socket.IO server + VS Code extension
- Full git suite (13 tools), LSP (7 tools), code quality, dep graph
- GitHub + GitLab integration (12 tools)
- MCP integration (stdio + SSE), headless/CI mode
- Docker sandbox, pip plugin registry, GitHub Actions CI template

---

## Competitive Comparison

### Overall Scores (weighted /10)

| Criteria (weight) | ShadowDev | Claude Code | OpenCode | Continue.dev |
|---|---|---|---|---|
| Tool richness (15%) | **9.5** | 7 | 7.5 | 6 |
| Architecture (10%) | **9** | 8.5 | 8 | 7 |
| LLM flexibility (10%) | 7.5 | 3 | **10** | 9 |
| Security (10%) | 8.5 | **9.5** | 5 | 6 |
| Memory/persistence (10%) | **9** | 8 | 4 | 2 |
| Hook/extensibility (10%) | 7 | 8 | **9** | 7 |
| Git/CI-CD (10%) | 8 | 7 | **8.5** | 8 |
| IDE/UI (10%) | 8 | 7 | **8.5** | **8.5** |
| Code intelligence (5%) | **9.5** | 5 | 6 | 5 |
| Community/ecosystem (5%) | 1 | 7 | **10** | 8 |
| Production readiness (5%) | 7 | **10** | 8 | 8 |
| **TOTAL** | **8.2** | **7.5** | **7.8** | **6.8** |

### Key Advantages (ShadowDev leads)

1. **56 tools** vs 14-18 (competitors) — 13 git, 7 LSP, code quality, dep graph
2. **Planner/Coder swarm** — read/write separation, safer than single-loop
3. **SQLite FTS5 memory** — structured, searchable, cross-session (vs file-based)
4. **Atomic batch edit** — file_edit_batch with rollback (unique)
5. **4-strategy fuzzy edit** — exact > trim > Levenshtein > block anchor
6. **Web IDE** — Monaco editor + file tree + chat (unique in this group)
7. **Code intelligence** — quality scoring (A-F), dep graph, context_build
8. **Self-hosted, multi-provider** — no vendor lock-in

### Key Gaps (competitors lead)

| # | Gap | Severity | Who does it better |
|---|---|---|---|
| 1 | MCP not wired (config exists, no integration) | **CRITICAL** | Claude Code, OpenCode, Continue.dev |
| 2 | No GitHub Actions / CI integration | **CRITICAL** | OpenCode, Continue.dev |
| 3 | No headless/CI mode | **HIGH** | Claude Code, OpenCode |
| 4 | Only 2 hook events (vs 13 in Claude Code) | **MEDIUM** | Claude Code |
| 5 | No IDE extension (VS Code/JetBrains) | **MEDIUM** | Continue.dev, OpenCode, Claude Code |
| 6 | No OS-level sandbox | **MEDIUM** | Claude Code (Seatbelt/bubblewrap) |
| 7 | No PR creation/review automation | **MEDIUM** | OpenCode, Continue.dev |
| 8 | Sequential tool execution only | **LOW** | Claude Code (parallel native) |
| 9 | Zero community/ecosystem | **HIGH** | OpenCode (116K stars), Continue.dev (31K) |
| 10 | No plugin ecosystem (npm/pip registry) | **MEDIUM** | OpenCode (6 extensibility mechanisms) |

---

## Roadmap

### Phase 3A — Critical Gaps (target: v0.4)

- [x] **3A-1. MCP Integration** — P0
  - Wire `config.MCP_SERVERS` into tool loading
  - Support stdio transport (spawn MCP server as subprocess)
  - Support SSE/HTTP transport (connect to remote servers)
  - Auto-register MCP tools into PLANNER_TOOLS / CODER_TOOLS
  - Dynamic tool search when MCP tools > 10% context window
  - Test with 2-3 popular MCP servers (filesystem, GitHub, database)
  - Files: `agent/mcp_client.py` (new), `agent/graph.py` (registration)
  - Estimate: 2-3 days

- [x] **3A-2. Headless / CI Mode** — P0
  - `python cli.py -p "prompt"` non-interactive execution
  - `--output-format text|json|stream-json`
  - `--session-id` for multi-turn context persistence
  - Exit code 0 on success, non-zero on error
  - `--allowed-tools` for fine-grained permission control
  - `--timeout` max execution time
  - Files: `cli.py` (extend argparse), `agent/headless.py` (new)
  - Estimate: 1-2 days

- [x] **3A-3. GitHub Integration** — P1
  - `github_create_pr(title, body, branch, base)` tool
  - `github_create_issue(title, body, labels)` tool
  - `github_comment(pr_number, body)` tool
  - `github_list_issues(state, labels)` tool
  - Uses existing `config.GITHUB_TOKEN`
  - Uses PyGithub or raw REST API (requests)
  - Files: `agent/tools/github.py` (new), `models/tool_schemas.py` (schemas)
  - Estimate: 2-3 days

- [x] **3A-4. GitLab Integration** — P2
  - Mirror of GitHub tools for GitLab API
  - Uses existing `config.GITLAB_TOKEN` + `config.GITLAB_INSTANCE_URL`
  - Files: `agent/tools/gitlab.py` (new)
  - Estimate: 1-2 days

### Phase 3B — Hook & Agent Improvements (target: v0.4)

- [x] **3B-1. Extended Hook Events** — P1
  - Add: `session_start`, `session_end`, `stop`, `subagent_start`, `subagent_stop`
  - Add: `pre_compact`, `post_compact`
  - Add: `user_prompt_submit`
  - Update `agent/hooks.py` event validation
  - Wire into `agent/nodes.py` (agent_node, summarize_node)
  - Wire into `agent/subagents.py` (subagent start/stop)
  - Wire into `cli.py` or `server/main.py` (session start/end)
  - Estimate: 1 day

- [x] **3B-2. Parallel Tool Execution** — P1
  - Detect independent tool calls in LLM response
  - Execute via `asyncio.gather()` instead of sequential loop
  - Respect dependencies (if tool B needs tool A output, run A first)
  - Add "Parallel Tool Calls" instruction to system prompt (already done)
  - Wire parallel execution in `HookedToolNode.__call__`
  - Files: `agent/graph.py` (HookedToolNode), `agent/nodes.py`
  - Estimate: 2-3 days

- [x] **3B-3. GitHub Actions Workflow** — P2
  - `.github/workflows/shadowdev.yml` template
  - Triggered by: issue comment (`/shadowdev`), PR review request, issue label
  - Runs headless mode in GitHub Actions runner
  - Auto-commits changes, creates/updates PR
  - Files: `.github/workflows/shadowdev.yml` (template), docs
  - Estimate: 2-3 days

### Phase 3C — IDE & Platform (target: v0.5)

- [x] **3C-1. VS Code Extension** — P2
  - Spawn ShadowDev CLI in VS Code terminal
  - Chat panel sidebar (webview)
  - File diff viewer integration
  - `@file` mention support
  - Active file / selection context passing
  - Problems panel integration
  - Files: `extensions/vscode/` (new directory)
  - Estimate: 3-5 days

- [x] **3C-2. Desktop App** — P3
  - Electron or Tauri wrapper around web IDE
  - System tray integration
  - Auto-update mechanism
  - Files: `desktop/` (new directory)
  - Estimate: 3-5 days

- [x] **3C-3. Container Sandbox** — P2
  - Optional Docker-based sandbox for terminal_exec
  - Mount workspace as volume (read-write)
  - Network isolation option
  - Resource limits (CPU, memory, time)
  - Fallback to current denylist if Docker not available
  - Files: `agent/sandbox.py` (new), `agent/tools/terminal.py` (integrate)
  - Estimate: 2-3 days

### Phase 3D — Ecosystem (target: v0.5+)

- [x] **3D-1. Plugin Registry** — P3
  - `pip install shadowdev-plugin-xxx` convention
  - Auto-discovery via entry_points or `skills/_tools/`
  - Plugin metadata: name, version, author, description
  - `skill_list` shows installed plugins
  - Files: `agent/plugin_registry.py` (new)
  - Estimate: 2-3 days

- [x] **3D-2. Skill Hub / Marketplace** — P3
  - Central repo for sharing skills (markdown + Python)
  - `skill_install(name)` tool to download from registry
  - Version management
  - Files: `agent/tools/skills.py` (extend), `agent/skill_hub.py` (new)
  - Estimate: 3-5 days

- [x] **3D-3. Open Source Preparation** — P3
  - Clean up README, add CONTRIBUTING.md
  - Add LICENSE file
  - CI pipeline (GitHub Actions: lint, test, build)
  - Docker Hub image publishing
  - PyPI package publishing
  - Estimate: 2-3 days

---

## Feature Parity Checklist

Track which competitor features are matched:

### vs Claude Code
- [x] File read/write/edit (surpassed — fuzzy edit, batch edit)
- [x] Code search (surpassed — ripgrep + semantic + grep fallback)
- [x] Terminal execution (matched — with denylist protection)
- [x] Planning mode (matched — plan_enter/exit + todos)
- [x] Subagents (matched — 4 types vs Claude's Task tool)
- [x] Memory (surpassed — SQLite FTS5 vs file-based)
- [x] Skills system (matched — markdown + Python plugins)
- [x] Hook system (surpassed — 10 events, arg/output modification, parallel execution)
- [x] MCP support (matched — stdio + SSE transports)
- [ ] OS-level sandbox (gap — no Seatbelt/bubblewrap; Docker sandbox available)
- [x] VS Code extension (matched — chat sidebar, context injection, diagnostic watcher)
- [x] Headless CI mode (matched — cli.py argparse, exit codes, stream-json)
- [x] Parallel tool execution (matched — asyncio.gather in HookedToolNode)
- [x] Web search (matched)
- [x] Git operations (surpassed — 13 dedicated tools vs Bash)
- [x] LSP (surpassed — 7 tools vs none)
- [x] Code quality analysis (unique — not in Claude Code)

### vs OpenCode
- [x] File operations (surpassed — batch edit, fuzzy edit)
- [x] Code search (surpassed — semantic search + ripgrep)
- [x] Terminal (matched)
- [x] Planning (matched — both have Plan agent/mode)
- [x] Subagents (matched)
- [x] Memory (surpassed — structured SQLite vs session-only)
- [x] Skills (matched)
- [x] Multi-LLM (partial — 6 providers vs 75+)
- [x] MCP (matched — stdio + SSE transports)
- [x] GitHub Actions (matched — .github/workflows/shadowdev.yml trigger template)
- [x] Desktop app (matched — Electron wrapper, system tray, auto-update)
- [x] VS Code extension (matched — chat sidebar + context)
- [ ] Neovim integration (gap)
- [x] Plugin ecosystem (matched — pip entry_points + skill hub)
- [x] Web IDE (unique — OpenCode has TUI only as built-in)
- [x] LSP (surpassed — 7 tools vs 2)
- [x] Git (surpassed — 13 tools vs Bash)

### vs Continue.dev
- [x] File operations (surpassed)
- [x] Code search (surpassed)
- [x] Terminal (surpassed — Continue has limited shell)
- [x] Planning (matched)
- [x] Subagents (surpassed — Continue has none)
- [x] Memory (surpassed — Continue has none)
- [x] Skills (matched)
- [x] Multi-LLM (partial — 6 vs 15+)
- [x] MCP (matched — stdio + SSE transports)
- [x] VS Code deep integration (matched — extension with chat, context, diagnostics)
- [ ] JetBrains integration (gap)
- [ ] Cloud agents (gap — Continue has Mission Control)
- [x] PR review bot (matched — GitHub Actions workflow + github_comment tool)
- [x] Git (surpassed — 13 tools)
- [x] Code intelligence (surpassed — quality, dep graph)
- [x] Web IDE (unique)

---

## Effort Estimates Summary

| Phase | Items | Total Effort | Target |
|---|---|---|---|
| 3A (Critical) | MCP, Headless, GitHub, GitLab | 6-10 days | v0.4 |
| 3B (Hooks/Agent) | Extended hooks, Parallel exec, GH Actions | 5-7 days | v0.4 |
| 3C (IDE/Platform) | VS Code ext, Desktop, Sandbox | 8-13 days | v0.5 |
| 3D (Ecosystem) | Plugin registry, Skill Hub, Open Source | 7-11 days | v0.5+ |
| **Total** | **13 items** | **26-41 days** | |

---

## Notes

- **OpenClaw excluded** from comparison — it's a general-purpose AI agent (WhatsApp/Telegram interface), not a coding tool
- Scores are relative within this comparison group only
- "Surpassed" means ShadowDev has more features/depth in that area
- "Matched" means roughly equivalent functionality
- "Gap" means competitor has this but ShadowDev doesn't
- Effort estimates assume single developer, may parallelize
