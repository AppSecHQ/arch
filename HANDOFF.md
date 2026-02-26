# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-7 of 13**
**Tests: 240 passing**
**Last Commit:** `7862953` (Step 6 - Container Manager)

Step 7 (Session/Container integration) is complete but not yet committed.

## Completed Components

| Step | File | Description | Tests |
|------|------|-------------|-------|
| 1 | `arch/state.py` | Thread-safe state store, JSON persistence, enum validation | 50 |
| 2 | `arch/worktree.py` | Git worktree create/remove/merge, CLAUDE.md injection | 28 |
| 3 | `arch/token_tracker.py` | Stream-json parsing, cost calculation, pricing.yaml | 32 |
| 4 | `arch/mcp_server.py` | SSE/HTTP MCP server, access controls, all tools | 40 |
| 5 | `arch/session.py` | Local claude subprocess, output parsing, resume | 34 |
| 6 | `arch/container.py` | Docker spawn/stop, volume mounts, Dockerfile | 40 |
| 7 | `arch/session.py` | Unified Session/Container interface, stream parsing for containers | 16 |

## Step 7 Implementation Details

### What was built:
- `ContainerizedSession` class in `session.py` - wraps `ContainerSession` with stream parsing + token tracking
- `SessionManager.spawn()` now checks `AgentConfig.sandboxed`:
  - `sandboxed=False` → creates local `Session`
  - `sandboxed=True` → creates `ContainerizedSession`
- `AnySession` type alias for `Session | ContainerizedSession`
- New methods: `list_local_sessions()`, `list_containerized_sessions()`, `is_containerized()`

### Key patterns:
- Both `Session` and `ContainerizedSession` share same interface: `spawn()`, `stop()`, `is_running`, `session_id`, `agent_id`
- Stream parsing via `StreamParser` works identically for both session types
- Token tracking unified through `TokenTracker.register_agent()`
- State updates include `container_name` and `sandboxed` flags for containerized agents

## Next Steps

### Step 8: Orchestrator (`arch/orchestrator.py`)
Wire all components, startup/shutdown, signal handlers:
- Parse and validate `archie.yaml`
- Initialize state store
- Permission gate: confirm `skip_permissions` usage
- Container gate: verify Docker if any agent has `sandbox.enabled`
- GitHub gate: verify `gh` auth if `github.repo` is set
- Start MCP server
- Create Archie's worktree and spawn Archie session
- Start dashboard
- Signal handlers for graceful shutdown (`SIGINT`, `SIGTERM`, `atexit`)

### Steps 9-13 (Remaining)
9. **Dashboard** - Textual TUI, live state, user input for escalations
10. **Persona files** - archie.md, frontend.md, backend.md, qa.md, security.md, copywriter.md
11. **GitHub tools** - Already implemented in MCP server, just need integration tests
12. **CLI entrypoint** - `arch up/down/status/init` commands
13. **Integration test** - End-to-end with real git repo

## Key Files

```
arch/
├── arch.py                 # CLI entrypoint (Step 12)
├── archie.yaml             # User config (exists in spec)
├── BRIEF.md                # Project brief (scaffolded by init)
├── pricing.yaml            # Token pricing config
├── requirements.txt        # Dependencies
├── KNOWN-ISSUES.md         # Tracked technical debt
├── SPEC-AGENT-HARNESS.md   # Full specification
│
├── arch/
│   ├── state.py            # ✅ Step 1
│   ├── worktree.py         # ✅ Step 2
│   ├── token_tracker.py    # ✅ Step 3
│   ├── mcp_server.py       # ✅ Step 4
│   ├── session.py          # ✅ Steps 5 + 7
│   ├── container.py        # ✅ Step 6
│   ├── orchestrator.py     # Step 8
│   └── dashboard.py        # Step 9
│
├── personas/               # Step 10
└── tests/                  # All test files
```

## Architecture Notes

### MCP Server
- SSE transport on `localhost:{mcp_port}`
- Agent identity from URL path: `/sse/{agent_id}`
- Server instances cached in `_mcp_servers` dict
- Active transports tracked in `_active_transports` dict
- `escalate_to_user` blocks via `asyncio.Event`

### Session Management
- `Session` class handles local subprocess
- `ContainerizedSession` class wraps `ContainerSession` + adds parsing
- Both parse stream-json output via `StreamParser`
- Session ID extracted from `result` event for resume
- `SessionManager.spawn()` auto-delegates based on `sandboxed` flag

### State Store
- In-memory dict with automatic JSON flush
- Enum validation for status fields
- Cursor-based message pagination

## Known Issues (Pre-Step 8)

From `KNOWN-ISSUES.md`:
- [ ] Add subprocess timeouts to `worktree.py` (all calls)
- [ ] Fix PR JSON parsing (use `gh pr create --json`)
- [ ] Add PR creation tests

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Commit Pending

Step 7 files need to be committed:
```bash
git add arch/session.py tests/test_session.py HANDOFF.md
git commit -m "Add session/container integration (Step 7)"
git push origin main
```

## Quick Verification

```bash
source .venv/bin/activate
python -c "
from arch.state import StateStore
from arch.worktree import WorktreeManager
from arch.token_tracker import TokenTracker
from arch.mcp_server import MCPServer
from arch.session import Session, ContainerizedSession, SessionManager, AnySession
from arch.container import ContainerManager
print('All modules import successfully')
"
```
