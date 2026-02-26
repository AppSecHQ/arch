# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-8 of 13**
**Tests: 273 passing**
**Last Commit:** `587fa7d` (Rename archie.yaml to arch.yaml in docs)

Step 8 (Orchestrator) is complete but not yet committed.

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
| 8 | `arch/orchestrator.py` | Config parsing, startup/shutdown, gates, signal handlers | 33 |

## Step 8 Implementation Details

### What was built:
- **Config dataclasses**: `ProjectConfig`, `ArchieConfig`, `AgentPoolEntry`, `SandboxConfig`, `PermissionsConfig`, `GitHubConfig`, `SettingsConfig`, `ArchConfig`
- **`parse_config()`**: Parses arch.yaml into typed config objects with validation
- **Gate checks**:
  - `check_permission_gate()` - identifies agents with skip_permissions
  - `check_container_gate()` - verifies Docker and images
  - `check_github_gate()` - verifies gh CLI and repo access
- **`Orchestrator` class**:
  - `startup()` - 10-step startup sequence
  - `shutdown()` - graceful cleanup
  - `run()` - main loop
  - Signal handlers (SIGINT, SIGTERM, atexit)
  - Archie auto-restart with --resume

### Also updated:
- `arch/mcp_server.py` - Added `stop()` method for graceful shutdown, `start(background=True)` for non-blocking

### Startup sequence:
1. Parse and validate arch.yaml
2. Initialize state store
3. Verify git repo
4. Permission gate (user confirmation for skip_permissions)
5. Container gate (Docker check, image pull)
6. GitHub gate (warn only)
7. Start MCP server (background)
8. Create Archie's worktree
9. Spawn Archie session
10. (Dashboard - Step 9)

## Next Steps

### Step 9: Dashboard (`arch/dashboard.py`)
Textual TUI with:
- Agents panel with status indicators (`●`, `[c]`, `[!]`)
- Activity log
- Cost panel with budget progress bar
- User input for escalations (blocking Archie's `escalate_to_user`)
- Keyboard shortcuts (q, ?, l, 1-9, m)

### Steps 10-13 (Remaining)
10. **Persona files** - archie.md, frontend.md, backend.md, qa.md, security.md, copywriter.md
11. **GitHub tools** - Already implemented in MCP server, just need integration tests
12. **CLI entrypoint** - `arch up/down/status/init` commands
13. **Integration test** - End-to-end with real git repo

## Key Files

```
arch/
├── arch.py                 # CLI entrypoint (Step 12)
├── arch.yaml               # User config
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
│   ├── mcp_server.py       # ✅ Step 4 (+ stop() added in Step 8)
│   ├── session.py          # ✅ Steps 5 + 7
│   ├── container.py        # ✅ Step 6
│   ├── orchestrator.py     # ✅ Step 8
│   └── dashboard.py        # Step 9
│
├── personas/               # Step 10
└── tests/                  # All test files
```

## Architecture Notes

### Orchestrator Lifecycle
```
Orchestrator
  ├── startup()
  │   ├── parse_config() → ArchConfig
  │   ├── StateStore(state_dir)
  │   ├── _permission_gate() → user confirmation
  │   ├── _container_gate() → Docker check
  │   ├── _github_gate() → gh auth check
  │   ├── MCPServer.start(background=True)
  │   ├── WorktreeManager.create("archie")
  │   └── SessionManager.spawn(archie_config)
  ├── run() → main loop, monitors Archie
  └── shutdown()
      ├── SessionManager.stop_all()
      ├── MCPServer.stop()
      └── WorktreeManager.cleanup_all()
```

### Signal Handling
- SIGINT/SIGTERM: Set `_shutdown_requested = True`, graceful exit
- atexit: Emergency worktree cleanup

### Archie Auto-Restart
- On unexpected exit (non-zero), attempt `--resume` once
- Uses session_id from previous run
- After 2 failures, initiate shutdown

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
```

## Commit Pending

Step 8 files need to be committed:
```bash
git add arch/orchestrator.py arch/mcp_server.py tests/test_orchestrator.py HANDOFF.md
git commit -m "Add orchestrator implementation (Step 8)"
git push origin main
```

## Quick Verification

```bash
source .venv/bin/activate
python -c "
from arch.orchestrator import Orchestrator, parse_config
from arch.mcp_server import MCPServer
print('Orchestrator ready')
"
```
