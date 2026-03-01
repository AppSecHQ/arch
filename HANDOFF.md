# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-13 of 13** ✅
**Tests: 423 passing**
**Last Commit:** 15cabc5

## Completed Components

| Step | File | Description | Tests |
|------|------|-------------|-------|
| 1 | `arch/state.py` | Thread-safe state store, JSON persistence, enum validation | 55 |
| 2 | `arch/worktree.py` | Git worktree create/remove/merge, CLAUDE.md injection | 28 |
| 3 | `arch/token_tracker.py` | Stream-json parsing, cost calculation, pricing.yaml | 32 |
| 4 | `arch/mcp_server.py` | SSE/HTTP MCP server, access controls, all tools, stop() | 40 |
| 5 | `arch/session.py` | Local claude subprocess, output parsing, resume | 34 |
| 6 | `arch/container.py` | Docker spawn/stop, volume mounts, Dockerfile | 40 |
| 7 | `arch/session.py` | Unified Session/Container interface, ContainerizedSession | 16 |
| 8 | `arch/orchestrator.py` | Config parsing, gates, startup/shutdown, lifecycle wiring | 53 |
| 9 | `arch/dashboard.py` | Textual TUI with agents/activity/costs panels, escalations | 43 |
| 10 | `personas/*.md` | archie, frontend, backend, qa, security, copywriter personas | - |
| 11 | `tests/test_mcp_server.py` | GitHub tools integration tests (mocked gh CLI) | 22 |
| 11.5 | `arch/*.py` | Agent state persistence: context field, save_progress tool, CLAUDE.md injection | 16 |
| 12 | `arch.py` | CLI entrypoint: up/down/status/init/send commands, PID file, GitHub label setup | 31 |
| 13 | `tests/test_integration.py` | End-to-end integration tests with real git operations | 16 |

## All Steps Complete

ARCH v1 implementation is complete per SPEC-AGENT-HARNESS.md.

## P0 Bug Fix Required

### Issue #4: Agent permissions block all execution

**UAT revealed that no agent can execute.** Agents spawned with `--print` block indefinitely waiting for tool permission approval because there's no TTY. See [#4](https://github.com/AppSecHQ/arch/issues/4) for full design.

**Three-layer permission system:**

1. **`--permission-mode acceptEdits`** — auto-approves Read, Edit, Write, Glob, Grep for all agents
2. **`--allowedTools` whitelist** — pre-approves MCP tools + common bash patterns per role
3. **`--permission-prompt-tool`** — delegates unapproved tool requests to dashboard via new `handle_permission_request` MCP tool

**Files to modify:**
- `arch/orchestrator.py` — `PermissionsConfig` gets `allowed_tools: list[str]`, default tool lists, pass to `AgentConfig`
- `arch/session.py` — `AgentConfig` gets `allowed_tools` + `permission_prompt_tool`, `Session.spawn()` builds CLI flags
- `arch/mcp_server.py` — new `handle_permission_request` tool (blocks like `escalate_to_user`)
- `arch/dashboard.py` — permission requests appear via existing pending decisions UI
- `arch.yaml` parsing — read `permissions.allowed_tools` per role

See Issue #4 for full implementation spec including default tool lists, config format, and test requirements.

---

## Key Architecture

### Dashboard Features (Step 9)

**Layout:**
```
┌─────────────────────────────────────────────────────────────────────┐
│  ARCH  ·  ProjectName  ·  Runtime: 00:14:32      [q]uit  [?]help   │
├───────────────┬──────────────────────────────────┬──────────────────┤
│ AGENTS        │ ACTIVITY LOG                     │ COSTS            │
│               │                                  │                  │
│ ● archie      │ 14:01 archie   Spawning fe-dev-1 │ archie   $0.12   │
│   Coordinating│ 14:02 fe-dev   Starting NavBar   │ fe-dev   $0.04   │
│               │ 14:03 qa-1     Running tests     │ qa-1     $0.02   │
│ ●[c] fe-dev-1 │ 14:04 fe-dev   BLOCKED: needs API│ ──────────────   │
│   Building    │ 14:05 archie   Checking in       │ Total    $0.19   │
│   NavBar      │                                  │ Budget   $5.00   │
│               │                                  │ ████░░   3.8%    │
│ ●[c][!] sec-1 │                                  │                  │
├───────────────┴──────────────────────────────────┴──────────────────┤
│ ⚠ ARCHIE ASKS: Merge frontend-dev-1 worktree to main? [y/N]: _     │
└─────────────────────────────────────────────────────────────────────┘
```

**Status indicators:**
- `●` green — working
- `●` yellow — blocked/waiting_review
- `○` bright_black — idle
- `✓` green — done
- `✗` red — error
- `[c]` — containerized
- `[!]` — skip_permissions

**Keyboard shortcuts:**
- `q` — graceful shutdown
- `?` — help overlay
- `l` — Archie's log
- `1-9` — agent logs
- `m` — message bus

**Integration:**
- `StateStore.list_agents()` → agents panel
- `StateStore.get_all_messages()` → activity log
- `StateStore.get_pending_decisions()` → escalation panel
- `TokenTracker.get_all_usage()` → costs panel
- `MCPServer.answer_escalation()` → handles user input
- 2-second refresh interval

### Agent Lifecycle Flow
```
Archie calls spawn_agent via MCP
  → MCPServer._handle_spawn_agent
  → Orchestrator._handle_spawn_agent (callback)
    → WorktreeManager.create()
    → WorktreeManager.write_claude_md()
    → SessionManager.spawn()
    → StateStore.register_agent()
  ← Returns {agent_id, worktree_path, sandboxed, status}
```

### Session Types
- `Session` — local subprocess
- `ContainerizedSession` — Docker container with stream parsing
- `AnySession = Session | ContainerizedSession`
- `SessionManager.spawn()` auto-delegates based on `config.sandboxed`

### Config file
- `arch.yaml` (renamed from archie.yaml — system config, not persona)

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Quick Verification

```bash
source .venv/bin/activate
python -c "
from arch.orchestrator import Orchestrator
from arch.mcp_server import MCPServer
from arch.session import SessionManager, ContainerizedSession
from arch.state import StateStore
from arch.dashboard import Dashboard, run_dashboard
print('All modules ready')
"
```
