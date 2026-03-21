# Multi-Instance ARCH — Analysis & Recommendations

Running multiple ARCH instances concurrently is a prerequisite for AEOS, where the COO Agent spawns parallel Archie sessions to close different ideal-state gaps simultaneously. This document analyzes what works today, what conflicts, and what to build.

---

## Current State

ARCH is designed as a singleton — one instance per project. Most isolation is already configurable via `arch.yaml`, but there are gaps.

### What's Already Isolated

| Resource | Config Key | Notes |
|----------|-----------|-------|
| MCP port | `settings.mcp_port` | Default 3999; each instance needs a unique port |
| State directory | `settings.state_dir` | Default `./state`; each instance needs its own dir |
| PID file | (lives in `state_dir`) | Isolated automatically if `state_dir` is unique |
| Config file | `--config` CLI flag | Each instance can point to its own `arch.yaml` |
| Dashboard | (served on MCP port) | Isolated by port — each instance has its own dashboard |
| Thread locking | (per-process `RLock`) | Safe within a single process; cross-process safety is enforced by PID file singleton per `state_dir` |

### What Conflicts

#### Git Worktrees (Critical)

`.worktrees/` is hardcoded in `worktree.py` relative to the repo root. Agent worktrees are created at `.worktrees/{agent_id}/` with branches named `agent/{agent_id}`. Two ARCH instances targeting the same repo will collide:

- Both try to create `.worktrees/archie/` → second fails
- Both try to create branch `agent/archie` → second fails
- Worktree cleanup on teardown can delete the other instance's work

This is the only **critical unsolved conflict** for multi-instance support.

#### No Upward Reporting

ARCH reports to the human user via the dashboard. There is no mechanism for an Archie session to report completion, results, or status back to a parent orchestrator (the COO). The COO has no way to know when an initiative is done or what it produced.

#### No Instance Identity

ARCH has no concept of an instance ID. There's no way to name, tag, or trace an instance back to the AEOS gap it was spawned to close.

#### No Instance Registry

Nothing tracks which ARCH instances are running, on what ports, for what initiatives. Each instance is fully independent and unaware of siblings.

---

## AEOS Integration Requirements

Per the AEOS spec, the COO Agent needs to:

1. **Spawn** — Write a BRIEF.md, launch an ARCH instance with isolated config
2. **Monitor** — Observe progress across all active instances
3. **Receive completion** — Know when an Archie session finishes and what it produced
4. **Manage lifecycle** — Tear down instances, collect results, spawn replacements
5. **Trace** — Map each instance back to a specific ideal-state gap

### Same Repo vs. Different Repos

If each ARCH instance targets a **different git repository** (frontend repo, backend repo, docs repo), most conflicts disappear naturally — worktrees, branches, and state are already separated by filesystem.

If multiple instances target the **same repo** (e.g., two parallel initiatives on one codebase), worktree namespacing becomes the hard problem. This is a real scenario — the COO might spawn one initiative to add a feature and another to fix a security issue, both on the same repo.

### Merge Strategy

**Decision:** Multi-instance merges go through **PRs**, not direct merge to `main`. This avoids merge conflicts when two instances try to merge concurrently, gives human review checkpoints, and plays well with CI/CD. Direct merge (`request_merge` without `pr_title`) should be disallowed when `instance_id` is set.

### BRIEF.md Scoping

**Decision:** Support **multiple BRIEFs** scoped by project, sprint, or instance. Each instance gets its own BRIEF (e.g., `BRIEF-{instance_id}.md` or `briefs/sprint-3.md`). The BRIEF path is configured in `arch.yaml`:

```yaml
project:
  brief: "briefs/sprint-3-security-fixes.md"  # default: BRIEF.md
```

This avoids contention when multiple instances target the same repo and makes it natural to scope work by sprint, initiative, or team.

### Service Agents

AEOS defines "Service Agents" (persistent, independent of Archie sessions) as a future layer. These have a fundamentally different lifecycle than ARCH's ephemeral agents — they don't start and stop with a project. This likely requires a separate runtime or a major extension to ARCH's lifecycle model. Out of scope for the multi-instance work but worth noting as an adjacent problem.

---

## Recommendations

### Phase 1 — Instance Isolation (ARCH changes)

Make ARCH safe to run N instances concurrently, including against the same repo.

**1a. Instance ID**
- Add `instance_id` to `arch.yaml` (auto-generated short UUID if not provided)
- Thread it through orchestrator, state store, and worktree manager
- Use it in log prefixes and state filenames for traceability

**1b. Worktree Namespacing**
- Change worktree paths from `.worktrees/{agent_id}/` to `.worktrees/{instance_id}/{agent_id}/`
- Change branch names from `agent/{agent_id}` to `{instance_id}/agent/{agent_id}`
- On teardown, clean up only worktrees under the instance's namespace
- This is the minimum viable fix — it unblocks same-repo concurrency

**1c. Completion Signal**
- On teardown (or when Archie marks the brief COMPLETE), write `state/result.json`:
  ```json
  {
    "instance_id": "abc123",
    "status": "complete|failed|cancelled",
    "brief": "path/to/BRIEF.md",
    "completed_at": "2026-03-18T14:30:00Z",
    "artifacts": ["path/to/output1", "path/to/output2"],
    "summary": "Archie's completion summary"
  }
  ```
- This gives the COO a simple polling target without requiring a persistent connection

### Phase 2 — Shared MCP Server

Instead of each instance running its own MCP server on a separate port, a **single shared MCP server** handles all instances. This eliminates port allocation entirely and provides a natural registry and aggregation point.

**Architecture:**

```
Shared MCP Server (port 3999)
├── /sse/{instance_id}/{agent_id}     → per-agent SSE (existing pattern, extended)
├── /api/instances                     → list all running instances
├── /api/instances/{id}/state          → state for one instance
├── /api/dashboard                     → meta-dashboard (all instances)
├── /api/dashboard/{id}                → single-instance dashboard
└── /api/instances/{id}/escalation     → escalation handling per instance
```

**Benefits:**
- No port allocation problem — one port serves everything
- Natural instance registry — the server knows what's running
- Meta-dashboard comes for free — aggregate all instances in one view
- Cross-instance communication possible (e.g., dependency signaling)
- COO connects to one SSE stream for all events

**Trade-offs:**
- Single point of failure (mitigated: restart recovers state from disk)
- Slightly more complex routing (instance_id in all paths)
- Must handle instance lifecycle (register on spawn, deregister on teardown)

**Decision:** Use a shared MCP server rather than per-instance servers.

### Phase 2b — COO Completion Callback

On completion (or failure), ARCH writes `state/result.json` **and** POSTs it to a `callback_url` if configured in `arch.yaml`. This gives the COO both a polling target and real-time notification.

```yaml
# In arch.yaml
settings:
  callback_url: "http://localhost:3999/api/instances/abc123/complete"
```

### Phase 3 — Instance Registry (AEOS layer)

This lives in AEOS, not ARCH. The COO maintains a registry of active instances:

```yaml
# aeos/state/instances.yaml
instances:
  - id: "abc123"
    brief: "Close gap: landing page conversion < 3%"
    gap_ref: "ideal_state.marketing.conversion"
    config: "/projects/marketing-site/arch-abc123.yaml"
    port: 4001
    state_dir: "/projects/marketing-site/state-abc123"
    status: "running"
    spawned_at: "2026-03-18T12:00:00Z"

  - id: "def456"
    brief: "Close gap: API p99 latency > 500ms"
    gap_ref: "ideal_state.platform.latency"
    config: "/projects/api-server/arch-def456.yaml"
    port: 4002
    state_dir: "/projects/api-server/state-def456"
    status: "complete"
    spawned_at: "2026-03-18T10:00:00Z"
    completed_at: "2026-03-18T13:45:00Z"
```

The COO's launcher generates an isolated `arch.yaml` for each instance (unique `instance_id`, `mcp_port`, `state_dir`), writes the BRIEF.md, and calls `archie up --config <path>`.

### Phase 4 — Aggregated Observability

Once multiple instances run concurrently, the CEO/COO needs a unified view:

- **Meta-dashboard** — Aggregates status from all active instances (hit each instance's `/api/dashboard/state` endpoint)
- **Cost rollup** — Sum token usage across instances, broken down by initiative
- **Timeline view** — When each instance started, its progress, and completion status

---

## Implementation Priority

| Priority | Work | Why |
|----------|------|-----|
| **P0** | Instance ID (1a) | Required by worktree namespacing and all downstream work |
| **P0** | Worktree namespacing (1b) | Unblocks same-repo concurrency — the only hard blocker |
| **P0** | BRIEF path config | Support `project.brief` in arch.yaml, default to `BRIEF.md` |
| **P1** | PR-only merges for multi-instance | Disable direct merge when `instance_id` is set |
| **P1** | Completion signal (1c) | COO can't close the loop without it |
| **P1** | Shared MCP server (Phase 2) | Eliminates port allocation, provides natural registry |
| **P2** | Instance registry (Phase 3) | COO needs to track what's running |
| **P2** | Meta-dashboard | Aggregate view across all instances |
| **P3** | Completion webhook (Phase 2b) | Real-time notification to COO |
