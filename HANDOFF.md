# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-8 of 13**
**Tests: 283 passing**
**Last Commit:** `2aa2d9e` Wire agent lifecycle tools to orchestrator (Step 8 follow-up)

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

## Next Step: Step 9 — Dashboard

Build `arch/dashboard.py` using Textual TUI library.

### Layout (from spec)
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

### Status indicators
- `●` green — working
- `●` yellow — blocked/waiting_review
- `○` grey — idle
- `✓` green — done
- `✗` red — error
- `[c]` — containerized (sandboxed)
- `[!]` — skip_permissions enabled

### Key features
1. **Agents panel**: Live status from `StateStore.list_agents()`
2. **Activity log**: Messages from state, scrollable
3. **Costs panel**: Per-agent costs from `TokenTracker`, budget progress bar
4. **User input**: Bottom panel for `escalate_to_user` responses
5. **Keyboard shortcuts**: q=quit, ?=help, l=Archie log, 1-9=agent logs, m=messages

### Integration points
- `MCPServer.answer_escalation(decision_id, answer)` — called when user answers
- `StateStore.get_pending_decisions()` — check for questions
- `TokenTracker.get_all_usage()` — cost data
- 2-second refresh interval

### Blocking pattern for escalations
```python
# In MCPServer._handle_escalate_to_user:
event = asyncio.Event()
self._pending_escalations[decision_id] = event
await event.wait()  # Blocks until dashboard calls answer_escalation
```

## Remaining Steps (10-13)

10. **Persona files** — archie.md, frontend.md, backend.md, qa.md, security.md, copywriter.md
11. **GitHub tools** — Integration tests (tools already implemented in MCP server)
12. **CLI entrypoint** — `arch up/down/status/init` commands
13. **Integration test** — End-to-end with real git repo

## Key Architecture

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
print('All modules ready')
"
```
