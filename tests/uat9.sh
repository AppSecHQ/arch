#!/bin/bash
#
# UAT #9 Setup & Launch Script
# Creates arch-test-9 project and starts ARCH
#
# WHAT THIS TESTS (skills system):
#   1. Directory-based personas (personas/engineering/CLAUDE.md + skills/)
#   2. Skill injection — skills copied into agent worktrees at .claude/skills/
#   3. list_personas returns skills[] array for directory personas
#   4. Archie uses skills to plan team (skill-aware team planning)
#   5. Agent has skills available via Claude Code native discovery
#   6. Archie uses get_skill to verify completion against quality criteria
#   7. Mixed personas — directory (engineering) + flat file (qa)
#   8. Full lifecycle: plan → spawn → build → verify → merge → close
#
set -e

UAT_DIR="$HOME/claude-projects/arch-test-9"
ARCH_DIR="$HOME/claude-projects/arch"

echo "=== ARCH UAT #9 Setup (Skills System) ==="
echo ""

# 1. Clean up any previous run
if [ -d "$UAT_DIR" ]; then
    echo "Removing previous arch-test-9..."
    rm -rf "$UAT_DIR"
fi

# 2. Create project directory
echo "Creating $UAT_DIR..."
mkdir -p "$UAT_DIR"
cd "$UAT_DIR"

# 3. Initialize git repo
echo "Initializing git repo..."
git init
git config user.email "uat@arch-test.com"
git config user.name "UAT Tester"

# 4. Create Archie persona (flat file — Archie doesn't need skills)
mkdir -p personas

cat > personas/archie.md << 'PERSONA'
# Archie — Lead Agent

You are **Archie**, the Lead Agent for ARCH.

## Session Startup

1. Call `get_project_context` as your **first action**.
2. Read the BRIEF.md goals and "Done When" criteria carefully.
3. Plan the team:
   a. Call `list_personas` to see what agent personas are available.
   b. **Pay attention to the `skills` array** for each persona — match skills to the project needs.
   c. Call `plan_team` with your proposed team and rationale referencing specific skills.
   d. Wait for user approval before spawning anyone.
4. After approval, use `spawn_agent` for each approved role.

## Spawning Agents

Be specific about assignments. Include:
- What to build
- Which **skill** to use (reference the skill name from list_personas)
- Acceptance criteria from BRIEF.md
- File paths and constraints

## Monitoring

- Call `list_agents` to check agent status
- Call `get_messages` to read agent messages

## Completing Work

When an agent calls `report_completion`:
1. Review their summary and artifacts
2. Call `get_skill(persona, skill)` to read the **Quality Criteria**
3. Verify the agent's output against those quality criteria
4. If incomplete, message them with specific feedback referencing the criteria
5. If complete, merge and tear down

## Session Shutdown

When all "Done When" criteria are met:
1. Verify all Done When items are checked off in BRIEF.md
2. Call `close_project(summary: "...")` with a complete summary

## IMPORTANT
- You are the coordinator, NOT the implementer
- ALWAYS call list_personas and plan_team before spawning any agents
- Reference skills by name when assigning work
- Use get_skill quality criteria to verify completions
- Always merge completed work and tear down agents
PERSONA

# 5. Create engineering persona (DIRECTORY with skills)
mkdir -p personas/engineering/skills/build-calculator
mkdir -p personas/engineering/skills/write-tests

cat > personas/engineering/CLAUDE.md << 'PERSONA'
# Engineering Agent

You are an **Engineering** agent who builds web applications.

## Your Expertise

- HTML, CSS, JavaScript
- Python scripting
- Unit testing and TDD
- Responsive design, accessibility

## When You Start
1. Read your assignment carefully — note which **skill** Archie wants you to use
2. Call `update_status(status: "working", task: "your current task")`

## While Working
- Keep Archie informed via `send_message(to: "archie", content: "status update")`
- Commit your work to git frequently
- If blocked, set status to "blocked" and message Archie

## When Complete
1. Make sure all files are committed to git
2. Call `report_completion(summary: "what you built", artifacts: ["file1.py"])`
PERSONA

cat > personas/engineering/skills/build-calculator/SKILL.md << 'SKILL'
---
name: build-calculator
description: >
  Build a web-based calculator application. Use when assigned a calculator
  implementation task.
allowed-tools: Read, Write, Edit, Bash
---

Build a calculator web application from a spec.

## Process
1. Read the requirements carefully
2. Create a single HTML file with embedded CSS and JS
3. Implement the calculator logic (basic arithmetic)
4. Style it with a clean, modern dark theme
5. Make it responsive (works on mobile and desktop)
6. Test all operations manually
7. Commit the file to git

## Inputs
- Requirements from BRIEF.md or Archie's assignment
- Any design constraints (colors, layout)

## Outputs
- `calculator.html` — Complete calculator application
- All code committed to git

## Quality Criteria
- All four basic operations work correctly (+, -, *, /)
- Division by zero shows "Error" (not crash or Infinity)
- Decimal point works (only one per number)
- Clear button resets to 0
- Display shows current input and result
- Responsive layout — usable on 320px wide screen
- Dark theme with high contrast text
- Buttons have visible hover/active states
SKILL

cat > personas/engineering/skills/write-tests/SKILL.md << 'SKILL'
---
name: write-tests
description: >
  Write Python unit tests for a web application. Use when assigned
  a testing task for HTML/JS applications.
allowed-tools: Read, Write, Edit, Bash
---

Write comprehensive Python tests for a web application.

## Process
1. Read the application source code
2. Identify all testable behaviors
3. Write tests using Python unittest and html.parser
4. Verify tests pass against the actual application
5. Commit test files to git

## Inputs
- The application HTML file to test
- Quality criteria or acceptance criteria from the BRIEF

## Outputs
- `test_calculator.py` — Python test file
- All tests passing

## Quality Criteria
- Tests cover all functionality listed in Done When
- Tests use Python standard library only (no pip installs)
- Tests are deterministic (no flaky tests)
- Test names clearly describe what they verify
- At least one test per Done When criterion
SKILL

# 6. Create QA persona (FLAT FILE — no skills, tests mixed format)
cat > personas/qa.md << 'PERSONA'
# QA Engineer

You write tests and validate that applications meet their requirements.

## When You Start
1. Read your assignment carefully
2. Call `update_status(status: "working", task: "your current task")`

## While Working
- Keep Archie informed via `send_message(to: "archie", content: "status update")`
- Write clear, runnable test scripts using Python unittest
- Commit your work to git frequently
- **Run tests yourself** using `python3 test_calculator.py` to verify they pass

## When Complete
1. Run the full test suite
2. Make sure all files are committed to git
3. Call `report_completion(summary: "what you tested and results", artifacts: ["test_file.py"])`
PERSONA

# 7. Create BRIEF.md
cat > BRIEF.md << 'BRIEF'
# Calculator App

## Goals

Build a web-based calculator with basic arithmetic operations and a clean UI.

## Done When

- [ ] `calculator.html` — Single HTML file with embedded CSS and JS
  - Four basic operations: add, subtract, multiply, divide
  - Division by zero shows "Error"
  - Decimal point support (max one per number)
  - Clear button resets to 0
  - Clean dark theme, responsive layout
- [ ] `test_calculator.py` — Python tests validating:
  - HTML file exists and is valid
  - All four operation buttons present
  - Display element exists
  - Clear button present
  - File is under 50KB
- [ ] All tests pass: `python3 test_calculator.py`

## Constraints

- Single HTML file — no frameworks, no build tools
- Tests use Python standard library only
- Must work by opening the file directly in a browser

## Current Status

Not started.

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
BRIEF

# 8. Create arch.yaml — NO agent_pool (forces dynamic team planning with skills)
cat > arch.yaml << YAML
project:
  name: "Calculator App"
  description: "Web-based calculator with basic arithmetic"
  repo: "."

archie:
  persona: "personas/archie.md"
  model: "claude-opus-4-6"

# No agent_pool — Archie must call list_personas and plan_team
# Two personas available:
#   - engineering (DIRECTORY with skills: build-calculator, write-tests)
#   - qa (FLAT FILE, no skills)
# Archie should pick engineering (has the right skills) and optionally qa

settings:
  max_concurrent_agents: 3
  state_dir: "./state"
  mcp_port: 3999
  token_budget_usd: 15.00
  auto_approve_team: false
YAML

# 9. Add .gitignore
cat > .gitignore << 'GI'
state/
.worktrees/
__pycache__/
*.pyc
GI

# 10. Initial commit
git add .
git commit -m "Initial project setup for UAT #9 — Calculator App with Skills"

echo ""
echo "=== Project created at $UAT_DIR ==="
echo ""
echo "Contents:"
ls -la
echo ""
echo "Persona structure:"
find personas -type f | sort
echo ""
echo "Skills available:"
find personas -name "SKILL.md" | sort
echo ""
echo "arch.yaml (note: NO agent_pool, directory personas):"
cat arch.yaml
echo ""
echo "=== Launching ARCH ==="
echo ""
echo "Dashboard opens automatically at http://localhost:3999/dashboard"
echo ""
echo "CHECKLIST (watch for these in the dashboard):"
echo ""
echo "  PHASE 1 — Skill-Aware Team Planning:"
echo "  [ ] Archie calls list_personas"
echo "  [ ] list_personas returns engineering with skills: [build-calculator, write-tests]"
echo "  [ ] list_personas returns qa with skills: [] (flat file)"
echo "  [ ] Archie calls plan_team referencing skills in rationale"
echo "  [ ] ESCALATION: Team plan shows skill-based rationale"
echo "  [ ] Approve the team plan"
echo ""
echo "  PHASE 2 — Skill Injection:"
echo "  [ ] Engineering agent spawned"
echo "  [ ] Check: .worktrees/engineering-1/.claude/skills/build-calculator/SKILL.md exists"
echo "  [ ] Check: .worktrees/engineering-1/.claude/skills/write-tests/SKILL.md exists"
echo "  [ ] Agent references skill in status updates"
echo ""
echo "  PHASE 3 — Build & Test:"
echo "  [ ] Engineering agent builds calculator.html"
echo "  [ ] QA or engineering agent writes test_calculator.py"
echo "  [ ] Tests run and pass"
echo ""
echo "  PHASE 4 — Skill-Based Verification:"
echo "  [ ] Archie calls get_skill to read quality criteria"
echo "  [ ] Archie verifies completion against quality criteria"
echo "  [ ] Work merged to main"
echo ""
echo "  PHASE 5 — Closeout:"
echo "  [ ] close_project confirmation in dashboard"
echo "  [ ] Confirm shutdown"
echo ""
echo "AFTER SHUTDOWN, VERIFY:"
echo "  cd $UAT_DIR"
echo "  git log --oneline              # Should show merge commits"
echo "  cat BRIEF.md                   # Done When items should be [x] checked"
echo "  python3 test_calculator.py     # All tests should pass"
echo "  open calculator.html           # Should work in browser"
echo "  cat state/events.jsonl | grep list_personas  # Should show skills in response"
echo "  cat state/events.jsonl | grep get_skill      # Should show Archie reading quality criteria"
echo ""
echo "Press Enter to launch orchestrator..."
read

source "$ARCH_DIR/.venv/bin/activate"
python "$ARCH_DIR/arch.py" up
