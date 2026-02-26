# ARCH — Known Issues & Follow-up Tasks

Non-blocking issues found during code review. Address before v1 ship.
Each item notes which step introduced it and which step it must be fixed by.

---

## State Store (Step 1)

- [x] **No enum validation** — FIXED in Step 4. Added `validate_agent_status()` and `validate_task_status()` with `InvalidStatusError`.
- [ ] **No cascade deletion** — `remove_agent()` leaves orphaned tasks and messages referencing the removed agent. Document behavior or implement cleanup.
- [ ] **No JSON corruption recovery** — corrupted state files return None silently on load. Add try/except with logged warning.

---

## Worktree Manager (Step 2)

- [ ] **Missing subprocess timeouts** — all subprocess calls lack `timeout=` parameter. Git operations can hang indefinitely and freeze the harness. Add `timeout=30` minimum to all calls. Fix before Step 8 (Orchestrator).
- [ ] **Fragile PR number parsing** — PR number extracted by splitting URL string. Switch to `gh pr create --json number,url` and parse JSON output instead.
- [ ] **No PR creation tests** — `create_pr()` is the highest-risk method (depends on `gh` CLI, remote, GitHub auth) and has zero test coverage.
- [ ] **Silent branch deletion failure** — failed `git branch -d` after worktree removal is silently ignored. Add logging.
- [ ] **Type hint typo** — `dict[str, any]` should be `dict[str, Any]` (capital A from `typing`).
- [ ] **No logging** — no `logging` module integration. Add before Step 8 (Orchestrator) for debuggability.

---

## Token Tracker (Step 3)

- [ ] **Callback exception propagation** — if `on_usage_update` callback raises, it propagates through the token tracker and could crash stream parsing. Wrap in try/except. Fix in Step 9 (Dashboard) when wiring up callbacks.

---

## MCP Server (Step 4)

- [x] **POST /messages stubbed out** — FIXED. POST handler now routes messages through active transport.
- [x] **MCP server instance duplication** — FIXED. Added `get_or_create_mcp_server()` with instance caching in `_mcp_servers` dict.
- [ ] **BRIEF.md regex fails on whitespace** — regex for updating Current Status section assumes exact formatting. Whitespace variations cause silent failures.
- [ ] **GitHub CLI FileNotFoundError opaque** — when `gh` not installed, error message is generic `str(e)`. Add explicit handling with install instructions.
- [ ] **Logging inconsistent** — `logger` imported but only used in a few places. Add logging for all error paths.

---

## Session Manager (Step 5)

- [ ] **Unread stderr can deadlock** — `stderr=asyncio.subprocess.PIPE` is set but stderr is never consumed. If the subprocess writes enough to stderr, the pipe buffer fills and the process deadlocks. Either read stderr concurrently or use `asyncio.subprocess.DEVNULL`.
- [ ] **Exit handling race** — `_process_output()` calls `_handle_exit()` after the read loop ends, but `stop()` can also cancel the output task and set `_running = False`. If both race, `_handle_exit` could fire twice. Add a guard at the top of `_handle_exit`.
- [ ] **Dead sessions accumulate** — `_wrap_exit_callback` in SessionManager doesn't remove finished sessions from `_sessions` dict. Stale entries grow over long runs. Add cleanup or periodic pruning.

---

## Container Manager (Step 6)

- [ ] **Unread stderr can deadlock (same as Step 5)** — `stderr=PIPE` is set but only exposed via optional `read_stderr()`. If the container writes heavily to stderr without anyone reading, pipe buffer fills and process deadlocks.
- [ ] **Timeout test uses wrong exception** — `test_check_docker_available_timeout` mocks `TimeoutError` but the code catches `subprocess.TimeoutExpired`. Test passes via the generic `except Exception` fallback, not the intended path.
- [ ] **No output parsing** — `ContainerSession` lacks the `_process_output` → `StreamParser` → `TokenTracker` pipeline that `Session` has. By design for Step 7 to integrate, but containerized agents won't track tokens until then.

---

## Orchestrator (Step 8)

_To be filled after Step 8 review._

---

## Dashboard (Step 9)

_To be filled after Step 9 review._
