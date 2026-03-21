# SPEC: Agent Skills System

## Problem

ARCH personas today are flat markdown files that define an agent's identity and working style. They tell the agent *who it is* but not *what it can do* in any structured way. The mind-sharp project independently developed a skills convention — structured files listing discrete operational capabilities per persona, each with process steps, inputs, outputs, and quality criteria.

Meanwhile, **Claude Code has a native Skills system** (`SKILL.md` files in `.claude/skills/`) with built-in discovery, slash-command invocation, auto-invocation, frontmatter configuration, subagent execution, and dynamic context injection. ARCH should leverage this native system rather than reinvent it.

This spec defines how ARCH integrates persona skills with Claude Code's native skill primitives, adding only what's needed for multi-agent orchestration on top.

---

## Design Principles

1. **Use native Claude Code skills where possible.** Each agent skill becomes a real `.claude/skills/<name>/SKILL.md` file in the agent's worktree, gaining all native capabilities for free.
2. **Personas define identity; skills define capabilities.** `CLAUDE.md` says who you are. Skills say what you can do. They are separate concerns.
3. **ARCH adds the multi-agent layer.** The things Claude Code doesn't handle — per-agent skill sets, skill-aware team planning, cross-agent skill visibility, worktree injection — are what ARCH builds.
4. **Backward compatible.** Existing flat `personas/*.md` files continue to work. Skills are additive.

---

## What Claude Code Gives Us (Native)

These capabilities exist out of the box when a `SKILL.md` file is present in `.claude/skills/`:

| Capability | How It Works |
|------------|-------------|
| **Discovery** | Skill descriptions loaded into context at session start (2% context budget) |
| **Slash-command invocation** | Agent types `/deploy` to invoke a skill |
| **Auto-invocation** | Claude reads the skill description and decides when to use it |
| **Frontmatter config** | `allowed-tools`, `model`, `effort`, `context: fork`, `agent` type, `disable-model-invocation`, `user-invocable` |
| **Arguments** | `$ARGUMENTS`, `$0`, `$1` substitution from invocation |
| **Dynamic context** | `` !`command` `` syntax injects shell output at invocation time |
| **Supporting files** | Templates, examples, scripts alongside `SKILL.md` |
| **Subagent execution** | `context: fork` runs the skill in an isolated subagent |

**ARCH does NOT need to build:** skill parsing, skill invocation, skill listing for the agent itself, or a skill execution engine. The agent's Claude Code session handles all of this natively.

---

## What ARCH Adds

| Capability | Why Native Isn't Enough |
|------------|------------------------|
| **Per-agent skill sets** | Claude Code discovers skills in `.claude/skills/` of the working directory. Each agent's worktree needs the right skills for its role — not every skill for every role. |
| **Skill-aware team planning** | Archie needs to see what skills each *persona* offers before spawning agents. This is a planning-time concern, not a runtime one. |
| **Worktree skill injection** | When ARCH creates a worktree, it must copy/symlink the persona's skills into `.claude/skills/` so the agent's Claude Code session discovers them natively. |
| **Cross-agent skill visibility** | Archie needs to know what skills the engineering agent has vs. the QA agent, for task routing and completion verification. |
| **Quality criteria for verification** | Archie checks completion against skill-defined quality criteria. This is an ARCH workflow, not a Claude Code feature. |

---

## Persona Directory Structure

### Current (flat files)

```
personas/
  archie.md
  frontend.md
  backend.md
  qa.md
```

### New (directory-based with native Claude Code skills)

```
personas/
  archie/
    CLAUDE.md                              # identity, role, personality, workflow
  engineering/
    CLAUDE.md                              # identity, role, expertise, working style
    skills/
      build-game-engine/
        SKILL.md                           # native Claude Code skill
      deploy/
        SKILL.md
        checklist.md                       # supporting file
      implement-feature/
        SKILL.md
      accessibility-audit/
        SKILL.md
      performance-optimize/
        SKILL.md
  product-design/
    CLAUDE.md
    skills/
      design-game/
        SKILL.md
      generate-puzzle-batch/
        SKILL.md
        templates/                         # supporting files
          puzzle-schema.json
      tune-difficulty/
        SKILL.md
      design-engagement-mechanics/
        SKILL.md
  ops/
    CLAUDE.md
    skills/
      handle-support/
        SKILL.md
      send-sms-notification/
        SKILL.md
      monitor-uptime/
        SKILL.md
      manage-ad-placement/
        SKILL.md
  frontend.md                              # legacy flat file — still works
```

**Rules:**
- A persona can be a **directory** (`personas/<role>/CLAUDE.md`) or a **flat file** (`personas/<role>.md`).
- Skills live in `personas/<role>/skills/<skill-name>/SKILL.md` — standard Claude Code skill structure.
- Directory-based personas take priority over flat files with the same name.
- Project personas override system personas by name.

---

## SKILL.md Format

Each skill follows the native Claude Code `SKILL.md` format with ARCH-specific conventions in the markdown body.

### Frontmatter (native Claude Code)

```yaml
---
name: build-game-engine
description: >
  Implement a game engine from a Product Design spec. Use when assigned
  a game implementation task.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---
```

Standard Claude Code frontmatter fields, all optional:
- `name` — Slash-command name (defaults to directory name)
- `description` — When Claude should use this skill (used for auto-invocation and ARCH discovery)
- `allowed-tools` — Tools the skill can use
- `model` — Model override
- `effort` — Effort level override
- `disable-model-invocation` — Only manual `/invoke` (for destructive skills like `/deploy`)
- `user-invocable` — Whether it appears in `/` menu (false = auto-invoke only)
- `context: fork` — Run in a subagent
- `agent` — Subagent type (Explore, Plan, general-purpose)

### Body (ARCH conventions)

The markdown body follows a structured format that both the agent and Archie can parse:

```markdown
## Process
1. Step one
2. Step two
3. ...

## Inputs
- What the agent needs to start

## Outputs
- What the agent produces when complete

## Quality Criteria
- Verifiable conditions that define "done well"

## Related Skills
- Cross-references to other skills or agents (optional)
```

### Full Example

```yaml
---
name: build-game-engine
description: >
  Implement a game engine from a Product Design spec. Use when assigned
  a game implementation task with a design spec and puzzle JSON files.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash
---
```

```markdown
Implement a game engine from a Product Design spec.

## Process
1. Read the game design spec thoroughly
2. Identify core modules: game logic, UI rendering, input handling, scoring, state
3. Implement game logic first — pure functions, no UI, testable
4. Build UI layer — responsive, accessible, mobile-first
5. Implement input handling (keyboard + touch)
6. Connect to storage (LocalStorage for anonymous, Supabase for logged-in)
7. Implement the daily puzzle loader (read from `puzzles/` JSON)
8. Build the share-results feature
9. Add the post-puzzle micro-feedback survey
10. Test on mobile devices (or mobile emulation)

## Inputs
- Game design spec from Product Design
- Puzzle JSON files from Product Design
- UI/accessibility requirements from design-plan.md

## Outputs
- Working game engine (HTML/CSS/JS)
- Game playable on test site
- Deploy report

## Quality Criteria
- Game logic matches spec exactly — no creative liberties
- Works on mobile Safari, Chrome, Firefox
- All interactive elements have 48px minimum tap targets
- Font sizes respect user preference settings
- Loads in < 2 seconds on 3G
- No framework dependencies — vanilla JS only
- Screen reader announces game state changes

## Related Skills
- For game design decisions → coordinate with `product-design`
- For deploying → `/deploy`
```

---

## ARCH Changes

### 1. Worktree Skill Injection (`worktree.py`)

This is the core integration point. When ARCH creates an agent's worktree, it copies the persona's skills into the worktree's `.claude/skills/` directory so Claude Code discovers them natively.

```python
def setup_agent_skills(self, agent_id: str, persona_path: str) -> int:
    """Copy persona skills into agent's worktree .claude/skills/ directory.

    Args:
        agent_id: Agent identifier.
        persona_path: Path to persona dir (e.g., 'personas/engineering').

    Returns:
        Number of skills installed.
    """
    worktree_path = self._worktree_path(agent_id)
    persona_dir = self.repo_path / persona_path
    skills_src = persona_dir / "skills"

    if not skills_src.is_dir():
        return 0

    skills_dest = worktree_path / ".claude" / "skills"
    skills_dest.mkdir(parents=True, exist_ok=True)

    count = 0
    for skill_dir in skills_src.iterdir():
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").exists():
            # Copy entire skill directory (SKILL.md + supporting files)
            dest = skills_dest / skill_dir.name
            shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
            count += 1

    return count
```

**After this runs**, the agent's worktree looks like:

```
.worktrees/engineering-1/
  .claude/
    skills/
      build-game-engine/
        SKILL.md              ← Claude Code discovers this natively
      deploy/
        SKILL.md
        checklist.md
      implement-feature/
        SKILL.md
      ...
  CLAUDE.md                   ← persona identity (injected by ARCH as before)
  src/
  ...
```

The agent's Claude Code session starts, discovers `.claude/skills/`, loads descriptions into context, and the agent can invoke or auto-invoke skills. No custom parsing needed at runtime.

### 2. Persona Scanning (`mcp_server.py:_scan_persona_dirs`)

Update the scanner to support directories and extract skill summaries for Archie's planning.

```
Current behavior:
  Scan personas/*.md → extract name, title, description

New behavior:
  For each entry in personas/:
    If it's a directory with CLAUDE.md:
      → Read CLAUDE.md for name, title, description
      → Scan skills/ subdirectory for SKILL.md files
      → Extract skill name + description from each SKILL.md frontmatter
      → Return persona with skills[] array
    If it's a .md file (and no same-name directory exists):
      → Current behavior (flat file scan)
      → Return persona with skills: []
```

The `list_personas` response gains a `skills` field:

```json
{
  "personas": [
    {
      "name": "engineering",
      "title": "Engineering",
      "description": "Builds site, implements games, manages infrastructure.",
      "path": "personas/engineering",
      "skills": [
        {"name": "build-game-engine", "description": "Implement a game engine from a Product Design spec."},
        {"name": "deploy", "description": "Push code to production or test environments."},
        {"name": "implement-feature", "description": "Build a feature from a BRIEF.md task or spec."},
        {"name": "accessibility-audit", "description": "Run a comprehensive accessibility check."},
        {"name": "performance-optimize", "description": "Identify and fix performance bottlenecks."}
      ]
    },
    {
      "name": "qa",
      "title": "QA Engineer",
      "description": "Tests and validates software quality.",
      "path": "personas/qa.md",
      "skills": []
    }
  ]
}
```

### 3. `get_skill` MCP Tool (`mcp_server.py`)

Archie needs to read full skill definitions for task routing and completion verification — but the skills live in persona directories, not in Archie's own worktree.

```
Tool: get_skill
Description: Get the full SKILL.md content for a persona's skill, including
             process, inputs, outputs, and quality criteria.
Parameters:
  - persona: string (required) — persona name (e.g., "engineering")
  - skill: string (optional) — skill name (e.g., "build-game-engine").
            If omitted, returns all skills for the persona.
Returns:
  Full markdown content of the matching skill(s).
```

This reads from `personas/<persona>/skills/<skill>/SKILL.md` in the project repo — the source of truth. Archie uses this to:
- Understand a skill's process before assigning work
- Check quality criteria when verifying completion
- Reference specific skills in assignment messages

### 4. Orchestrator Integration (`orchestrator.py`)

Update `_launch_agent` to call `setup_agent_skills` after creating the worktree:

```python
# After creating worktree and writing CLAUDE.md (existing code)
worktree_path = self.worktree_mgr.create(agent_id, base_branch)
self.worktree_mgr.write_claude_md(agent_id, persona_content, ...)

# NEW: Install persona skills into worktree
persona_dir = Path(pool_entry.persona).parent  # e.g., 'personas/engineering'
if (self.repo_path / persona_dir / "skills").is_dir():
    skill_count = self.worktree_mgr.setup_agent_skills(agent_id, str(persona_dir))
    logger.info(f"Installed {skill_count} skills for {agent_id}")
```

### 5. `plan_team` Path Convention

The `persona` field in `plan_team` changes from a file path to a directory path for directory-based personas:

```
Old: "persona": "personas/frontend.md"
New: "persona": "personas/engineering"       (directory-based)
     "persona": "personas/frontend.md"       (flat file, still works)
```

Archie specifies the path as returned by `list_personas`.

---

## How Skills Flow Through the System

### Team Planning (Archie)

```
1. Archie reads BRIEF.md → understands what needs to be done
2. Archie calls list_personas → sees personas with skill summaries
3. Archie matches skills to brief requirements:
   "Brief needs game implementation → engineering has 'build-game-engine'"
   "Brief needs puzzle content → product-design has 'generate-puzzle-batch'"
4. Archie calls plan_team with selected personas and rationale
5. User approves
```

### Agent Startup

```
1. ARCH creates worktree for engineering-1
2. ARCH writes CLAUDE.md (persona identity + ARCH context)
3. ARCH copies personas/engineering/skills/* → .worktrees/engineering-1/.claude/skills/
4. Claude Code session starts in the worktree
5. Claude Code discovers .claude/skills/ → loads skill descriptions into context
6. Agent sees: /build-game-engine, /deploy, /implement-feature, etc.
```

### Task Execution

```
1. Archie sends assignment: "Build the Word Chain game engine.
   Game design spec is in docs/word-chain-spec.md."
2. Agent reads the message → Claude Code auto-invokes /build-game-engine
   (or agent manually invokes it)
3. Full SKILL.md content loads into context → agent follows the process steps
4. Agent produces the defined outputs
5. Agent calls report_completion via MCP
```

### Completion Verification (Archie)

```
1. Archie receives completion report
2. Archie calls get_skill(persona="engineering", skill="build-game-engine")
3. Archie checks quality criteria against the agent's reported output:
   - Game logic matches spec? ✓
   - Works on mobile Safari? (needs UAT)
   - 48px tap targets? ✓
   - Loads in < 2s? ✓
4. Archie approves or sends feedback
```

### COO-Level Planning (AEOS)

```
1. COO identifies gap: "no games built" (ideal state: 3 games live)
2. COO calls list_personas → sees engineering has 'build-game-engine',
   product-design has 'design-game' and 'generate-puzzle-batch'
3. COO writes BRIEF.md scoped to building the first game
4. COO spawns ARCH instance → Archie executes
```

---

## Shared / Project-Wide Skills

Some skills aren't persona-specific — they're project-wide conventions any agent might use. These go in the project's `.claude/skills/` directory (the standard Claude Code location):

```
.claude/
  skills/
    commit-convention/
      SKILL.md          # "All commits must follow conventional commits format"
    report-completion/
      SKILL.md          # "When done, call report_completion with..."
```

These are inherited by all worktrees (Claude Code's native behavior for the repo root). Persona-specific skills are **added** on top by ARCH's worktree injection.

---

## Migration from mind-sharp Format

The mind-sharp project currently uses a single `SKILLS.md` per persona with multiple `## Skill:` sections. To migrate to native Claude Code skills:

```
Before (mind-sharp convention):
  personas/engineering/
    CLAUDE.md
    SKILLS.md           ← single file, multiple skills

After (native Claude Code skills):
  personas/engineering/
    CLAUDE.md
    skills/
      build-game-engine/
        SKILL.md        ← one file per skill, with frontmatter
      deploy/
        SKILL.md
      implement-feature/
        SKILL.md
```

Each `## Skill:` section becomes its own `SKILL.md` with:
- Claude Code frontmatter (`name`, `description`, `allowed-tools`, etc.)
- The same body content (Process, Inputs, Outputs, Quality Criteria)

A migration script can automate this conversion.

---

## What We Don't Build

These are handled by Claude Code natively and need no ARCH code:

| Feature | Native Mechanism |
|---------|-----------------|
| Skill discovery at session start | `.claude/skills/` scanning, 2% context budget |
| Slash-command invocation | `/skill-name` in prompt |
| Auto-invocation | Description matching |
| Argument passing | `$ARGUMENTS`, `$0`, `$1` |
| Dynamic context injection | `` !`command` `` syntax |
| Supporting files | Files alongside `SKILL.md` |
| Subagent execution | `context: fork` + `agent` frontmatter |
| Skill listing for the agent | `/skills` command |
| Tool restrictions per skill | `allowed-tools` frontmatter |
| Model/effort overrides | `model`, `effort` frontmatter |

---

## Implementation Priority

| Priority | Change | File(s) | Effort |
|----------|--------|---------|--------|
| P0 | Worktree skill injection | `worktree.py` | Small |
| P0 | Directory-based persona scanning | `mcp_server.py` | Small |
| P0 | Orchestrator calls skill injection on spawn | `orchestrator.py` | Small |
| P1 | Skills summary in `list_personas` response | `mcp_server.py` | Small |
| P1 | `get_skill` MCP tool for Archie | `mcp_server.py` | Medium |
| P2 | Skill-aware planning guidance in Archie persona | `personas/archie/CLAUDE.md` | Small |
| P2 | mind-sharp migration script | `scripts/` | Small |
| P2 | Tests | `tests/` | Medium |

## Open Questions

1. **Copy vs. symlink.** Should ARCH copy skill directories into worktrees or symlink them? Copy is simpler and avoids issues if the source changes mid-session. Symlink saves disk and keeps skills in sync. Recommendation: copy (simplicity, isolation).

2. **Skill inheritance.** Should agents inherit project-wide `.claude/skills/` in addition to their persona skills? Claude Code would handle this if the worktree is inside the repo (it walks up to find `.claude/`). Verify this works with ARCH's worktree layout.

3. **Skill versioning.** If a persona's skills change between sessions, agents mid-flight have the old version (copied at spawn time). This is probably fine — skills change infrequently and agents are ephemeral.

4. **COO skill visibility.** The COO agent (AEOS layer) needs to browse skills across all personas without spawning them. The `list_personas` + `get_skill` MCP tools serve this purpose, but the COO doesn't run inside ARCH. May need a CLI command: `archie skills [persona]`.
