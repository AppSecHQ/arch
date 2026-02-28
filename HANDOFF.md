# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-9 of 13**
**Tests: 326 passing**
**Last Commit:** (pending)

## Completed Components

| Step | File | Description | Tests |
|------|------|-------------|-------|
| 1 | `arch/state.py` | Thread-safe state store, JSON persistence, enum validation | 50 |
| 2 | `arch/worktree.py` | Git worktree create/remove/merge, CLAUDE.md injection | 28 |
| 3 | `arch/token_tracker.py` | Stream-json parsing, cost calculation, pricing.yaml | 32 |
| 4 | `arch/mcp_server.py` | SSE/HTTP MCP server, access controls, all tools, stop() | 40 |
| 5 | `arch/session.py` | Local claude subprocess, output parsing, resume | 34 |
| 6 | `arch/container.py` | Docker spawn/stop, volume mounts, Dockerfile | 40 |
| 7 | `arch/session.py` | Unified Session/Container interface, ContainerizedSession | 16 |
| 8 | `arch/orchestrator.py` | Config parsing, gates, startup/shutdown, lifecycle wiring | 43 |
| 9 | `arch/dashboard.py` | Textual TUI with agents/activity/costs panels, escalations | 43 |

## Next Step: Step 10 — Persona Files

Create default persona files in `personas/`:
- `archie.md` - Lead Agent persona (includes instructions for get_project_context, spawn_agent, GitHub Scrum Master flow)
- `frontend.md` - Frontend developer
- `backend.md` - Backend developer
- `qa.md` - QA/testing agent
- `security.md` - Security auditor
- `copywriter.md` - Documentation/copy agent

Each persona should follow the CLAUDE.md style and include:
- Role description
- Expertise areas
- Communication style
- Workflow instructions

## Remaining Steps (10-13)

10. **Persona files** — archie.md, frontend.md, backend.md, qa.md, security.md, copywriter.md
11. **GitHub tools** — Integration tests (tools already implemented in MCP server)
12. **CLI entrypoint** — `arch up/down/status/init` commands
13. **Integration test** — End-to-end with real git repo

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
