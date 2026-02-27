# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-8 of 13**
**Tests: 283 passing**
**Last Commit:** `ee74c2c` (HANDOFF.md and KNOWN-ISSUES.md updates)

Step 8 follow-up (agent lifecycle wiring) is complete but not yet committed.

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
| 8 | `arch/orchestrator.py` | Config parsing, startup/shutdown, gates, signal handlers, lifecycle wiring | 43 |

## Step 8 Follow-up Implementation

### What was wired:

**`_handle_spawn_agent(role, assignment, context, skip_permissions)`:**
- Looks up role in `agent_pool` config
- Validates `max_instances` and `max_concurrent_agents` limits
- Creates worktree via `WorktreeManager.create(agent_id)`
- Reads persona file for the role
- Writes CLAUDE.md with persona + assignment + tools
- Builds `AgentConfig` from pool entry (model, sandbox, permissions)
- Calls `SessionManager.spawn(config, prompt)`
- Registers agent in `StateStore`
- Returns `{agent_id, worktree_path, sandboxed, skip_permissions, status}`

**`_handle_teardown_agent(agent_id)`:**
- Rejects teardown of Archie
- Calls `SessionManager.stop(agent_id)`
- Removes worktree via `WorktreeManager.remove()` (unless `keep_worktrees`)
- Updates state to remove agent

**`_handle_request_merge(agent_id, target_branch, pr_title, pr_body)`:**
- If `pr_title` provided: creates PR via `WorktreeManager.create_pr()`
- Otherwise: direct merge via `WorktreeManager.merge()`
- Returns `{status: "approved"|"rejected", pr_url?}`

**`_handle_close_project(summary)`:**
- Sets `_shutdown_requested = True`
- Initiates graceful shutdown

### Also added:
- Agent instance tracking: `_agent_instance_counts` dict
- Instance count decrement on agent exit
- `_get_pool_entry(role)` helper
- `_generate_agent_id(role)` helper

---

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
│   ├── orchestrator.py     # ✅ Step 8 + follow-up
│   └── dashboard.py        # Step 9
│
├── personas/               # Step 10
└── tests/                  # All test files
```

## Architecture Notes

### Agent Lifecycle Flow
```
Archie calls spawn_agent via MCP
  → MCPServer._handle_spawn_agent
  → Orchestrator._handle_spawn_agent (via callback)
    → WorktreeManager.create(agent_id)
    → WorktreeManager.write_claude_md()
    → SessionManager.spawn(AgentConfig, prompt)
    → StateStore.register_agent()
  ← Returns {agent_id, worktree_path, ...}
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

Step 8 follow-up files need to be committed:
```bash
git add arch/orchestrator.py tests/test_orchestrator.py HANDOFF.md
git commit -m "Wire agent lifecycle tools to orchestrator (Step 8 follow-up)"
git push origin main
```

## Quick Verification

```bash
source .venv/bin/activate
python -c "
from arch.orchestrator import Orchestrator, parse_config
from arch.mcp_server import MCPServer
print('Orchestrator ready with lifecycle wiring')
"
```
