# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-6 of 13**
**Tests: 224 passing**
**Last Commit:** `c045051` (Step 5 - Session Manager)

Step 6 (Container Manager) is complete but not yet committed.

## Completed Components

| Step | File | Description | Tests |
|------|------|-------------|-------|
| 1 | `arch/state.py` | Thread-safe state store, JSON persistence, enum validation | 50 |
| 2 | `arch/worktree.py` | Git worktree create/remove/merge, CLAUDE.md injection | 28 |
| 3 | `arch/token_tracker.py` | Stream-json parsing, cost calculation, pricing.yaml | 32 |
| 4 | `arch/mcp_server.py` | SSE/HTTP MCP server, access controls, all tools | 40 |
| 5 | `arch/session.py` | Local claude subprocess, output parsing, resume | 34 |
| 6 | `arch/container.py` | Docker spawn/stop, volume mounts, Dockerfile | 40 |

## Next Steps

### Step 7: Session Manager (container integration)
Integrate `container.py` into `session.py` for unified interface. When `AgentConfig.sandboxed=True`, delegate to `ContainerSession` instead of local subprocess.

### Steps 8-13 (Remaining)
8. **Orchestrator** - Wire all components, startup/shutdown, signal handlers
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
│   ├── session.py          # ✅ Step 5
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
- `ContainerSession` class handles Docker containers
- Both parse stream-json output via `StreamParser`
- Session ID extracted from `result` event for resume

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

Step 6 files need to be committed:
```bash
git add arch/container.py tests/test_container.py
git commit -m "Add container manager implementation (Step 6)"
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
from arch.session import Session, SessionManager
from arch.container import ContainerManager
print('All modules import successfully')
"
```
