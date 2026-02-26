# ARCH — Agent Runtime & Coordination Harness

> Meet **Archie** — your AI development team lead.

ARCH is a multi-agent development system that orchestrates independent Claude AI sessions working concurrently on a software project. Each agent is a full Claude CLI process with its own role, memory, and isolated git worktree. A central harness connects them via a local MCP server, tracks token costs, and renders a live terminal dashboard.

---

## How It Works

```
arch up
```

Archie (the Lead Agent) reads your project, decomposes the work, and dynamically spawns specialist agents — frontend dev, QA engineer, security auditor, and more — each working in parallel in their own git branch. You watch from the dashboard, answer questions when Archie needs a decision, and approve merges when work is ready.

---

## Features

- **Dynamic agent spawning** — Archie decides which specialists to spin up based on the task
- **Isolated git worktrees** — agents work in parallel without filesystem conflicts
- **Agent-to-agent messaging** — agents coordinate via a local MCP message bus
- **Token & cost tracking** — per-agent usage logged in real time
- **Sandboxed agents** — run agents in Docker containers for safety and isolation
- **Permission control** — opt-in `--dangerously-skip-permissions` per agent role, with audit logging
- **Live TUI dashboard** — see agent status, activity, and costs at a glance
- **Configurable** — single `arch.yaml` defines your project, agent pool, and settings

---

## Quick Start

```bash
# Install
pip install -r requirements.txt

# Scaffold config in your project
arch init --name "My Project"

# Start
arch up
```

---

## Configuration

```yaml
# arch.yaml
project:
  name: My App
  description: A full-stack web application

agent_pool:
  - id: frontend-dev
    persona: personas/frontend.md
    model: claude-sonnet-4-6
  - id: qa-engineer
    persona: personas/qa.md
    model: claude-sonnet-4-6
    sandbox:
      enabled: true        # run in Docker container
  - id: security-auditor
    persona: personas/security.md
    model: claude-sonnet-4-6
    sandbox:
      enabled: true
    permissions:
      skip_permissions: true   # requires user confirmation at startup

settings:
  max_concurrent_agents: 5
  token_budget_usd: 10.00
```

---

## Status

Early development. See [SPEC-AGENT-HARNESS.md](./SPEC-AGENT-HARNESS.md) for the full technical specification.
