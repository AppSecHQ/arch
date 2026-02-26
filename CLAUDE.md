# Project Guidelines

Read SPEC-AGENT-HARNESS.md in full. You are building ARCH (Agent Runtime & Coordination Harness) exactly as specified.

Before writing any code:
1. Read the entire spec top to bottom
2. Confirm you understand the architecture — MCP server, session manager, worktree manager, token tracker, state store, dashboard, container manager, and how they connect
3. Ask me any clarifying questions about ambiguous requirements

Then begin implementation strictly following the Implementation Order section of the spec. Start with step 1 (state store) and do not advance to the next step until the current one has working unit tests.

Constraints:
- Python 3.11+
- Follow the file/directory structure in the spec exactly
- The GitHub repo is at https://github.com/AppSecHQ/arch — initialize it as the working directory
- Do not skip steps or build ahead — the layers depend on each other
- Write tests for each component before moving on

When you complete a step, tell me what you built, show me the tests passing, and ask before proceeding to the next step.

## User Preferences
- **No self-attribution**: Do NOT add "Co-Authored-By: Claude" or similar attribution lines to commits, PRs, documents, or any other content unless explicitly instructed by the user.

## Compacting and Resuming
- When compacting, update your memory and update HANDOFF.md. 
- When resuming, read HANDOFF.md for full context. Next step is Step 7: Session Manager (container integration).
