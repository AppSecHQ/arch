# ARCH — Known Issues & Follow-up Tasks

Non-blocking issues found during code review. Address before v1 ship.
Each item notes which step introduced it and which step it must be fixed by.

---

## State Store (Step 1)

- [ ] **No enum validation** — agent status and task status values are not validated before persisting. Invalid strings can slip in silently. Fix before MCP server step (Step 4) when status values become load-bearing.
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

_To be filled after Step 3 review._

---

## MCP Server (Step 4)

_To be filled after Step 4 review._

---

## Session Manager (Step 5)

_To be filled after Step 5 review._

---

## Container Manager (Step 6)

_To be filled after Step 6 review._

---

## Orchestrator (Step 8)

_To be filled after Step 8 review._

---

## Dashboard (Step 9)

_To be filled after Step 9 review._
