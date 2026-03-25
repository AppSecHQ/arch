#!/bin/bash
#
# UAT #10 — Multi-Instance ARCH
# Tests two scenarios:
#   A) Two ARCH instances on the SAME repo (worktree namespacing)
#   B) Two ARCH instances on DIFFERENT repos (independent)
#
# Run scenarios separately:
#   bash tests/uat10.sh same-repo
#   bash tests/uat10.sh diff-repo
#   bash tests/uat10.sh          (runs both sequentially)
#
set -e

ARCH_DIR="$HOME/claude-projects/arch"
BASE_DIR="$HOME/claude-projects"

setup_git_repo() {
    local dir="$1"
    echo "Initializing git repo at $dir..."
    mkdir -p "$dir"
    cd "$dir"
    git init
    git config user.email "uat@arch-test.com"
    git config user.name "UAT Tester"
}

create_personas() {
    local dir="$1"
    mkdir -p "$dir/personas"

    cat > "$dir/personas/archie.md" << 'PERSONA'
# Archie — Lead Agent

You are **Archie**, the Lead Agent for ARCH.

## Session Startup
1. Call `get_project_context` as your **first action**.
2. Read the BRIEF.md goals and "Done When" criteria.
3. Spawn a frontend agent to do the work.

## Completing Work
When an agent calls `report_completion`:
1. Review their summary
2. Merge their work
3. Tear down the agent
4. Call `close_project(summary: "...")` when done

## IMPORTANT
- You are the coordinator, NOT the implementer
- Always spawn agents to do the actual coding
PERSONA

    cat > "$dir/personas/frontend.md" << 'PERSONA'
# Frontend Developer

You build user interfaces with HTML, CSS, and JavaScript.

## When You Start
1. Read your assignment carefully
2. Call `update_status(status: "working", task: "your current task")`

## When Complete
1. Commit all files to git
2. Call `report_completion(summary: "what you built", artifacts: ["file.html"])`
PERSONA
}

# ============================================================================
# Scenario A: Two instances on the SAME repo
# ============================================================================
scenario_same_repo() {
    echo ""
    echo "============================================================"
    echo "  SCENARIO A: Two ARCH instances on the SAME repo"
    echo "============================================================"
    echo ""

    local PROJECT_DIR="$BASE_DIR/arch-test-10-shared"

    # Clean up
    if [ -d "$PROJECT_DIR" ]; then
        echo "Removing previous arch-test-10-shared..."
        rm -rf "$PROJECT_DIR"
    fi

    setup_git_repo "$PROJECT_DIR"
    create_personas "$PROJECT_DIR"

    # BRIEF for instance A — build a header
    cat > "$PROJECT_DIR/BRIEF-header.md" << 'BRIEF'
# Header Component

## Goal
Build a reusable page header component.

## This Session
Build header.html with a nav bar, logo placeholder, and responsive menu.

## Done When (this session)
- [ ] `header.html` — Header component with nav links and responsive layout

## Constraints
- Single HTML file, no frameworks
- Must work by opening directly in browser

## Current Status
Not started.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
BRIEF

    # BRIEF for instance B — build a footer
    cat > "$PROJECT_DIR/BRIEF-footer.md" << 'BRIEF'
# Footer Component

## Goal
Build a reusable page footer component.

## This Session
Build footer.html with copyright, links, and social icons.

## Done When (this session)
- [ ] `footer.html` — Footer component with copyright and links

## Constraints
- Single HTML file, no frameworks
- Must work by opening directly in browser

## Current Status
Not started.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
BRIEF

    # arch.yaml for instance A (port 3999)
    cat > "$PROJECT_DIR/arch-a.yaml" << YAML
project:
  name: "Header Component"
  description: "Reusable page header"
  repo: "."
  brief: "BRIEF-header.md"

archie:
  persona: "personas/archie.md"
  model: "claude-opus-4-6"

agent_pool:
  - id: frontend
    persona: "personas/frontend.md"
    model: "claude-sonnet-4-6"
    max_instances: 1

settings:
  max_concurrent_agents: 2
  state_dir: "./state-a"
  mcp_port: 3999
  instance_id: "inst-a"
  token_budget_usd: 10.00
YAML

    # arch.yaml for instance B (port 4000)
    cat > "$PROJECT_DIR/arch-b.yaml" << YAML
project:
  name: "Footer Component"
  description: "Reusable page footer"
  repo: "."
  brief: "BRIEF-footer.md"

archie:
  persona: "personas/archie.md"
  model: "claude-opus-4-6"

agent_pool:
  - id: frontend
    persona: "personas/frontend.md"
    model: "claude-sonnet-4-6"
    max_instances: 1

settings:
  max_concurrent_agents: 2
  state_dir: "./state-b"
  mcp_port: 4000
  instance_id: "inst-b"
  token_budget_usd: 10.00
YAML

    cat > "$PROJECT_DIR/.gitignore" << 'GI'
state-a/
state-b/
.worktrees/
GI

    git add .
    git commit -m "Initial setup for multi-instance UAT #10 (same repo)"

    echo ""
    echo "=== Same-repo project created at $PROJECT_DIR ==="
    echo ""
    echo "Two configs:"
    echo "  arch-a.yaml — instance 'inst-a', port 3999, BRIEF-header.md"
    echo "  arch-b.yaml — instance 'inst-b', port 4000, BRIEF-footer.md"
    echo ""
    echo "CHECKLIST:"
    echo ""
    echo "  WORKTREE ISOLATION:"
    echo "  [ ] .worktrees/inst-a/archie/ exists (not .worktrees/archie/)"
    echo "  [ ] .worktrees/inst-b/archie/ exists"
    echo "  [ ] .worktrees/inst-a/frontend-1/ exists"
    echo "  [ ] .worktrees/inst-b/frontend-1/ exists"
    echo "  [ ] No worktree name collisions"
    echo ""
    echo "  BRANCH ISOLATION:"
    echo "  [ ] Branch: inst-a/agent/archie"
    echo "  [ ] Branch: inst-b/agent/archie"
    echo "  [ ] Branch: inst-a/agent/frontend-1"
    echo "  [ ] Branch: inst-b/agent/frontend-1"
    echo ""
    echo "  BRIEF ISOLATION:"
    echo "  [ ] Instance A reads BRIEF-header.md (builds header)"
    echo "  [ ] Instance B reads BRIEF-footer.md (builds footer)"
    echo "  [ ] Each updates its own BRIEF, not the other's"
    echo ""
    echo "  INDEPENDENT COMPLETION:"
    echo "  [ ] Each instance merges to main independently"
    echo "  [ ] header.html on main after inst-a finishes"
    echo "  [ ] footer.html on main after inst-b finishes"
    echo "  [ ] Both dashboards show independent activity"
    echo ""
    echo "LAUNCH (run in two terminals):"
    echo ""
    echo "  Terminal 1:"
    echo "    cd $PROJECT_DIR"
    echo "    source $ARCH_DIR/.venv/bin/activate"
    echo "    python $ARCH_DIR/arch.py up --config arch-a.yaml --clean"
    echo ""
    echo "  Terminal 2:"
    echo "    cd $PROJECT_DIR"
    echo "    source $ARCH_DIR/.venv/bin/activate"
    echo "    python $ARCH_DIR/arch.py up --config arch-b.yaml --clean"
    echo ""
    echo "  Dashboards:"
    echo "    http://localhost:3999/dashboard  (Instance A — Header)"
    echo "    http://localhost:4000/dashboard  (Instance B — Footer)"
    echo ""
    echo "AFTER BOTH FINISH:"
    echo "  cd $PROJECT_DIR"
    echo "  git log --oneline              # Merge commits from both instances"
    echo "  git branch -a                  # Namespaced branches"
    echo "  ls .worktrees/                 # inst-a/ and inst-b/ subdirs"
    echo "  ls header.html footer.html     # Both files on main"
    echo "  cat state-a/result.json        # Instance A completion signal"
    echo "  cat state-b/result.json        # Instance B completion signal"
}

# ============================================================================
# Scenario B: Two instances on DIFFERENT repos
# ============================================================================
scenario_diff_repo() {
    echo ""
    echo "============================================================"
    echo "  SCENARIO B: Two ARCH instances on DIFFERENT repos"
    echo "============================================================"
    echo ""

    local PROJECT_A="$BASE_DIR/arch-test-10-repo-a"
    local PROJECT_B="$BASE_DIR/arch-test-10-repo-b"

    # Clean up
    for d in "$PROJECT_A" "$PROJECT_B"; do
        if [ -d "$d" ]; then
            echo "Removing previous $(basename $d)..."
            rm -rf "$d"
        fi
    done

    # --- Repo A: Badge component ---
    setup_git_repo "$PROJECT_A"
    create_personas "$PROJECT_A"

    cat > "$PROJECT_A/BRIEF.md" << 'BRIEF'
# Badge Component

## Goal
Build a CSS badge/pill component.

## This Session
Build badge.html with multiple badge styles (success, warning, error, info).

## Done When (this session)
- [ ] `badge.html` — Badge component with 4 color variants

## Constraints
- Single HTML file, no frameworks

## Current Status
Not started.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
BRIEF

    cat > "$PROJECT_A/arch.yaml" << YAML
project:
  name: "Badge Component"
  description: "CSS badge/pill component"
  repo: "."

archie:
  persona: "personas/archie.md"
  model: "claude-opus-4-6"

agent_pool:
  - id: frontend
    persona: "personas/frontend.md"
    model: "claude-sonnet-4-6"
    max_instances: 1

settings:
  max_concurrent_agents: 2
  state_dir: "./state"
  mcp_port: 3999
  token_budget_usd: 10.00
YAML

    cat > "$PROJECT_A/.gitignore" << 'GI'
state/
.worktrees/
GI

    git add .
    git commit -m "Initial setup for UAT #10 repo A — Badge component"

    # --- Repo B: Card component ---
    setup_git_repo "$PROJECT_B"
    create_personas "$PROJECT_B"

    cat > "$PROJECT_B/BRIEF.md" << 'BRIEF'
# Card Component

## Goal
Build a CSS card component.

## This Session
Build card.html with image, title, body text, and action button.

## Done When (this session)
- [ ] `card.html` — Card component with image, title, body, and button

## Constraints
- Single HTML file, no frameworks

## Current Status
Not started.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
BRIEF

    cat > "$PROJECT_B/arch.yaml" << YAML
project:
  name: "Card Component"
  description: "CSS card component"
  repo: "."

archie:
  persona: "personas/archie.md"
  model: "claude-opus-4-6"

agent_pool:
  - id: frontend
    persona: "personas/frontend.md"
    model: "claude-sonnet-4-6"
    max_instances: 1

settings:
  max_concurrent_agents: 2
  state_dir: "./state"
  mcp_port: 4000
  token_budget_usd: 10.00
YAML

    cat > "$PROJECT_B/.gitignore" << 'GI'
state/
.worktrees/
GI

    git add .
    git commit -m "Initial setup for UAT #10 repo B — Card component"

    echo ""
    echo "=== Two independent repos created ==="
    echo "  Repo A: $PROJECT_A (Badge, port 3999)"
    echo "  Repo B: $PROJECT_B (Card, port 4000)"
    echo ""
    echo "CHECKLIST:"
    echo ""
    echo "  [ ] Both instances start without interference"
    echo "  [ ] Each reads its own BRIEF.md"
    echo "  [ ] Worktrees in each repo are independent"
    echo "  [ ] No port or state conflicts"
    echo "  [ ] Each produces its deliverable (badge.html, card.html)"
    echo ""
    echo "LAUNCH (run in two terminals):"
    echo ""
    echo "  Terminal 1:"
    echo "    cd $PROJECT_A"
    echo "    source $ARCH_DIR/.venv/bin/activate"
    echo "    python $ARCH_DIR/arch.py up --clean"
    echo ""
    echo "  Terminal 2:"
    echo "    cd $PROJECT_B"
    echo "    source $ARCH_DIR/.venv/bin/activate"
    echo "    python $ARCH_DIR/arch.py up --clean"
    echo ""
    echo "  Dashboards:"
    echo "    http://localhost:3999/dashboard  (Repo A — Badge)"
    echo "    http://localhost:4000/dashboard  (Repo B — Card)"
    echo ""
    echo "AFTER BOTH FINISH:"
    echo "  ls $PROJECT_A/badge.html"
    echo "  ls $PROJECT_B/card.html"
    echo "  cat $PROJECT_A/state/result.json"
    echo "  cat $PROJECT_B/state/result.json"
}

# ============================================================================
# Main
# ============================================================================

echo "=== ARCH UAT #10 — Multi-Instance ==="
echo ""

source "$ARCH_DIR/.venv/bin/activate"

case "${1:-both}" in
    same-repo|same|a)
        scenario_same_repo
        ;;
    diff-repo|diff|b)
        scenario_diff_repo
        ;;
    both|all)
        scenario_same_repo
        echo ""
        echo "============================================================"
        echo "  Scenario A setup complete. Set up Scenario B next."
        echo "============================================================"
        echo ""
        echo "Press Enter to set up Scenario B..."
        read
        scenario_diff_repo
        ;;
    *)
        echo "Usage: bash tests/uat10.sh [same-repo|diff-repo|both]"
        exit 1
        ;;
esac
