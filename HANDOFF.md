# ARCH Implementation Handoff

## Current State

**Steps Completed: 1-13 of 13** ✅
**Tests: 440 passing**
**Last Commit:** 029cd17
**UAT Status:** In progress — Archie launches but hangs (0 tokens). Fix pending commit.

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

## Post-Build Fixes (Review Cycle)

### Issue #4: Agent permissions — FIXED (multiple rounds)

UAT #1 revealed agents blocked on permission prompts (no TTY). Builder implemented three-layer permission system (`e633bf9`).

**Builder's implementation:**
- `--permission-mode acceptEdits` + `--allowedTools` + `--permission-prompt-tool` flags in session.py/container.py
- `handle_permission_request` MCP tool with blocking + `_runtime_allowed` dict
- Dashboard `[y]once [a]lways [n]o` UI for permission requests
- Config parsing for `permissions.allowed_tools` per role

**Review fix #1 (`db2f061`):** 4 bugs found and fixed:
1. MCP tools missing from `DEFAULT_ALLOWED_TOOLS_ALL` / `DEFAULT_ALLOWED_TOOLS_ARCHIE` — without them agents blocked on every MCP call
2. Wrong Bash pattern syntax: `Bash(git:*)` → `Bash(git *)` per Claude CLI docs
3. `--permission-prompt-tool` was commented out with incorrect race condition concern
4. `handle_permission_request` in `WORKER_TOOLS` (agents could call directly) → moved to `SYSTEM_TOOLS`

**Review fix #2 (uncommitted, ready to commit):** UAT #2 showed Archie still hangs (0 tokens, debug log ends at init). Cause: `SYSTEM_TOOLS` were excluded from `_get_tools_for_agent()` tool catalog. Claude CLI's `--permission-prompt-tool` references `mcp__arch__handle_permission_request` but can't discover it via MCP protocol. Fix: include `SYSTEM_TOOLS` in tool catalog returned by `_get_tools_for_agent()`.

### CLI rename: `arch` → `archie` (`dc002fb`)

`/usr/bin/arch` is a macOS system binary. Renamed all CLI user-facing references to `archie`. Config file stays `arch.yaml`. No `pyproject.toml` yet — run as `python arch.py up`.

### Path resolution issue (known, not yet fixed)

`repo: "."` and `state_dir: "./state"` in arch.yaml resolve relative to **cwd**, not the config file location. Workaround: run from the project directory. Proper fix: resolve relative to `config_path.parent` in orchestrator.

### Test coverage gap (known)

All 440 tests pass but every test mocks `create_subprocess_exec`, Docker, and MCP server start/stop. No test runs a real `claude` process connecting to a real MCP server. All UAT bugs (permission blocking, tool discovery, path resolution) were invisible to the test suite. Need a real smoke test that starts the MCP server and has a client connect and discover tools.

## Open GitHub Issues

| # | Title | Status |
|---|-------|--------|
| [#3](https://github.com/AppSecHQ/arch/issues/3) | Skills integration (v2) | Open — deferred |
| [#4](https://github.com/AppSecHQ/arch/issues/4) | Agent permissions | Implemented, UAT in progress |

Closed: [#1](https://github.com/AppSecHQ/arch/issues/1) (feedback), [#2](https://github.com/AppSecHQ/arch/issues/2) (auto-resume + arch send)

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

### Permission System (Three Layers)
1. `--permission-mode acceptEdits` — auto-approves Read, Edit, Write, Glob, Grep
2. `--allowedTools` — per-role whitelist from `DEFAULT_ALLOWED_TOOLS_ALL` / `_ARCHIE` + user config
3. `--permission-prompt-tool mcp__arch__handle_permission_request` — runtime delegation to dashboard

### Session Types
- `Session` — local subprocess
- `ContainerizedSession` — Docker container with stream parsing
- `SessionManager.spawn()` auto-delegates based on `config.sandboxed`

### Config file
- `arch.yaml` — system config
- CLI command: `python arch.py` (alias `archie` once pyproject.toml is added)

### UAT test project
- Location: `~/claude-projects/arch-test-1` (Mortgage Calculator)
- Run from project dir: `cd ~/claude-projects/arch-test-1 && source ~/claude-projects/arch/.venv/bin/activate && python ~/claude-projects/arch/arch.py up`

## Running Tests

```bash
source .venv/bin/activate
GIT_CONFIG_GLOBAL=/dev/null python -m pytest tests/ -v
```

## Files Modified This Session

- `arch/orchestrator.py` — DEFAULT_ALLOWED_TOOLS constants with MCP tools, fixed Bash syntax, enabled permission_prompt_tool
- `arch/mcp_server.py` — SYSTEM_TOOLS list, _get_tools_for_agent includes system tools, _check_tool_access allows system tools
- `arch/session.py` — (builder) allowed_tools + permission_prompt_tool in AgentConfig and spawn()
- `arch/container.py` — (builder) same permission flags for containerized sessions
- `arch/dashboard.py` — (builder) permission request UI with y/a/n
- `arch.py` — CLI rename arch → archie
- `tests/test_mcp_server.py` — SYSTEM_TOOLS tests, tool catalog tests
- `tests/test_session.py` — fixed Bash pattern syntax in tests
- `tests/test_cli.py` — updated "archie up" reference
- `KNOWN-ISSUES.md` — documented all fixes
- `HANDOFF.md` — this file
