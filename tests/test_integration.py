"""Integration tests for ARCH (Step 13).

End-to-end tests exercising:
- Orchestrator full lifecycle
- Archie + local agent + sandboxed agent
- GitHub integration (mocked)
- Agent state persistence (save_progress → teardown → verify)

These tests use REAL git operations but mock:
- Claude CLI subprocess (asyncio.create_subprocess_exec)
- Docker availability checks
- GitHub gate checks
- MCP server start/stop
"""

import asyncio
import json
import subprocess
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from contextlib import contextmanager

import pytest
import yaml

from arch.orchestrator import Orchestrator, AgentConfig
from arch.state import StateStore
from arch.mcp_server import MCPServer
from arch.session import Session, ContainerizedSession, SessionManager
from arch.token_tracker import TokenTracker


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def integration_repo(tmp_path):
    """Create a real git repository for integration testing."""
    repo_path = tmp_path / "test-project"
    repo_path.mkdir()

    # Initialize git repo
    subprocess.run(["git", "init"], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_path, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo_path, capture_output=True, check=True
    )

    # Create initial commit
    (repo_path / "README.md").write_text("# Test Project\n")
    subprocess.run(["git", "add", "."], cwd=repo_path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=repo_path, capture_output=True, check=True
    )

    return repo_path


@pytest.fixture
def integration_config(integration_repo, tmp_path):
    """Create arch.yaml with local + sandboxed agents + GitHub config."""
    state_dir = tmp_path / "state"
    personas_dir = integration_repo / "personas"
    personas_dir.mkdir()

    # Create persona files
    (personas_dir / "archie.md").write_text("""# Archie
You are the lead agent coordinating the team.
""")

    (personas_dir / "frontend.md").write_text("""# Frontend Developer
You build user interfaces.
""")

    (personas_dir / "security.md").write_text("""# Security Auditor
You audit code for security vulnerabilities.
""")

    # Create BRIEF.md
    (integration_repo / "BRIEF.md").write_text("""# Project Brief

## Goals
Build a test application.

## Done When
- All tests pass
- Code is reviewed

## Constraints
- Use Python 3.11+

## Current Status
Starting development.

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
""")

    # Create arch.yaml
    config_path = integration_repo / "arch.yaml"
    config = {
        "project": {
            "name": "Integration Test Project",
            "description": "A project for integration testing",
            "repo": str(integration_repo)
        },
        "archie": {
            "persona": "personas/archie.md",
            "model": "claude-opus-4-5"
        },
        "agent_pool": [
            {
                "id": "frontend-dev",
                "persona": "personas/frontend.md",
                "model": "claude-sonnet-4-6",
                "max_instances": 2
            },
            {
                "id": "security-auditor",
                "persona": "personas/security.md",
                "model": "claude-sonnet-4-6",
                "max_instances": 1,
                "sandbox": {
                    "enabled": True,
                    "image": "arch-agent:latest",
                    "memory_limit": "1g"
                }
            }
        ],
        "github": {
            "repo": "testorg/test-project",
            "default_branch": "main"
        },
        "settings": {
            "state_dir": str(state_dir),
            "mcp_port": 13999,  # Use non-standard port to avoid conflicts
            "max_concurrent_agents": 5,
            "token_budget_usd": 10.0
        }
    }
    config_path.write_text(yaml.dump(config))

    return config_path


def create_mock_claude_process(output_lines=None, session_id="test-session-123"):
    """Create a mock Claude subprocess with stream-json output."""
    mock = MagicMock()
    mock.pid = 12345
    mock.returncode = None

    if output_lines is None:
        output_lines = []

    # Add standard output events
    full_output = output_lines + [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Task completed."}]}}),
        json.dumps({"type": "usage", "input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 50}),
        json.dumps({"type": "result", "session_id": session_id}),
    ]

    output_iter = iter(full_output + [None])

    async def readline():
        try:
            line = next(output_iter)
            if line is None:
                return b""
            return (line + "\n").encode()
        except StopIteration:
            return b""

    mock.stdout = MagicMock()
    mock.stdout.readline = readline
    mock.stderr = MagicMock()
    mock.stderr.readline = AsyncMock(return_value=b"")
    mock.wait = AsyncMock(return_value=0)
    mock.terminate = MagicMock()
    mock.kill = MagicMock()

    return mock


def create_mock_docker_process():
    """Create a mock Docker subprocess."""
    mock = MagicMock()
    mock.pid = 54321
    mock.returncode = None

    output_lines = [
        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "Security audit complete."}]}}),
        json.dumps({"type": "usage", "input_tokens": 800, "output_tokens": 400, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 25}),
        json.dumps({"type": "result", "session_id": "container-session-456"}),
    ]

    output_iter = iter(output_lines + [None])

    async def readline():
        try:
            line = next(output_iter)
            if line is None:
                return b""
            return (line + "\n").encode()
        except StopIteration:
            return b""

    mock.stdout = MagicMock()
    mock.stdout.readline = readline
    mock.stderr = MagicMock()
    mock.stderr.readline = AsyncMock(return_value=b"")
    mock.wait = AsyncMock(return_value=0)
    mock.terminate = MagicMock()
    mock.kill = MagicMock()

    return mock


@contextmanager
def mock_external_tools(mock_exec_return=None):
    """
    Mock external tools (Claude CLI, Docker, gh) while allowing git to run.

    This enables real git worktree operations but mocks:
    - asyncio.create_subprocess_exec (Claude CLI)
    - Docker availability checks
    - GitHub gate checks
    - MCP server start/stop
    - Container image checks/pulls
    """
    if mock_exec_return is None:
        mock_exec_return = create_mock_claude_process()

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        mock_exec.return_value = mock_exec_return

        with patch("arch.orchestrator.check_docker_available", return_value=(True, "OK")):
            with patch("arch.orchestrator.check_image_exists", return_value=True):
                with patch("arch.orchestrator.check_github_gate", return_value=(True, "GitHub: testorg/test-project")):
                    # Also mock container module's image checks
                    with patch("arch.container.check_image_exists", return_value=True):
                        with patch("arch.container.pull_image", return_value=(True, "Image pulled")):
                            with patch.object(MCPServer, "start", new_callable=AsyncMock):
                                with patch.object(MCPServer, "stop", new_callable=AsyncMock):
                                    yield mock_exec


# ============================================================================
# Integration Tests
# ============================================================================


class TestOrchestratorLifecycle:
    """Tests for full orchestrator lifecycle."""

    @pytest.mark.asyncio
    async def test_startup_initializes_all_components(self, integration_config):
        """Orchestrator startup initializes state, worktrees, MCP, and Archie."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            result = await orch.startup()

            assert result is True
            assert orch.state is not None
            assert orch.token_tracker is not None
            assert orch.worktree_manager is not None
            assert orch.session_manager is not None
            assert orch.mcp_server is not None
            assert orch._archie_session is not None

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_resources(self, integration_config, integration_repo):
        """Orchestrator shutdown stops sessions and cleans worktrees."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Verify worktree was created
            archie_worktree = integration_repo / ".worktrees" / "archie"
            assert archie_worktree.exists()

            # Shutdown
            await orch.shutdown()

            assert orch._running is False
            # Worktrees should be cleaned up
            assert not archie_worktree.exists()


class TestAgentSpawnAndTeardown:
    """Tests for agent spawn and teardown lifecycle."""

    @pytest.mark.asyncio
    async def test_spawn_local_agent(self, integration_config, integration_repo):
        """Orchestrator can spawn a local agent with worktree."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn a local agent
            result = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build the login page"
            )

            assert "agent_id" in result
            assert result["agent_id"].startswith("frontend-dev-")
            assert result["sandboxed"] is False
            assert result["status"] == "spawning"

            # Verify worktree was created
            worktree_path = Path(result["worktree_path"])
            assert worktree_path.exists()
            assert (worktree_path / "CLAUDE.md").exists()

            # Verify agent registered in state
            agent = orch.state.get_agent(result["agent_id"])
            assert agent is not None
            assert agent["role"] == "frontend-dev"

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_spawn_sandboxed_agent(self, integration_config, integration_repo):
        """Orchestrator can spawn a sandboxed (containerized) agent."""
        orch = Orchestrator(integration_config)

        with mock_external_tools() as mock_exec:
            # Return different mocks for Archie vs container
            mock_exec.side_effect = [
                create_mock_claude_process(session_id="archie-session"),
                create_mock_docker_process(),
            ]

            await orch.startup()

            # Spawn a sandboxed agent
            result = await orch._handle_spawn_agent(
                role="security-auditor",
                assignment="Audit the authentication code"
            )

            assert "agent_id" in result
            assert result["agent_id"].startswith("security-auditor-")
            assert result["sandboxed"] is True

            # Verify agent registered in state
            agent = orch.state.get_agent(result["agent_id"])
            assert agent is not None
            assert agent["sandboxed"] is True

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_teardown_agent_cleans_up(self, integration_config, integration_repo):
        """Teardown removes agent session and worktree."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn an agent
            spawn_result = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build feature"
            )
            agent_id = spawn_result["agent_id"]
            worktree_path = Path(spawn_result["worktree_path"])

            # Verify worktree exists
            assert worktree_path.exists()

            # Teardown the agent
            teardown_result = await orch._handle_teardown_agent(agent_id)

            assert teardown_result is True

            # Agent should be removed from state
            agent = orch.state.get_agent(agent_id)
            assert agent is None

            # Worktree should be removed
            assert not worktree_path.exists()

            # Cleanup
            await orch.shutdown()


class TestAgentStatePersistence:
    """Tests for save_progress and state persistence across restarts."""

    @pytest.mark.asyncio
    async def test_save_progress_persists_context(self, integration_config):
        """save_progress MCP tool persists agent context."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn an agent
            spawn_result = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build the navbar"
            )
            agent_id = spawn_result["agent_id"]

            # Simulate save_progress via MCP
            context = {
                "files_modified": ["src/Navbar.tsx", "src/Navbar.css"],
                "progress": "Navbar component 80% complete",
                "next_steps": "Add responsive styles",
                "blockers": None,
                "decisions": ["Using CSS modules for styling"]
            }
            orch.state.update_agent(agent_id, context=context)

            # Verify context is persisted
            agent = orch.state.get_agent(agent_id)
            assert agent["context"] == context
            assert agent["context"]["progress"] == "Navbar component 80% complete"

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_context_injected_on_restart(self, integration_config, integration_repo):
        """Saved context is injected into CLAUDE.md on agent restart."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Simulate Archie having saved progress in a previous session
            context = {
                "files_modified": ["BRIEF.md"],
                "progress": "Sprint 1 planning complete",
                "next_steps": "Spawn frontend agent",
                "decisions": ["Using React for UI"]
            }
            orch.state.update_agent("archie", context=context)

            # Get Archie's worktree path
            worktree_path = orch.worktree_manager.get_worktree_path("archie")

            # Re-write CLAUDE.md (simulating restart)
            orch.worktree_manager.write_claude_md(
                agent_id="archie",
                persona_content="# Archie\nLead agent.",
                project_name="Test",
                project_description="Test project",
                assignment="Coordinate team",
                available_tools=["spawn_agent", "get_messages"],
                session_state=context
            )

            # Read CLAUDE.md and verify context is injected
            claude_md = (worktree_path / "CLAUDE.md").read_text()
            assert "## Session State" in claude_md
            assert "Sprint 1 planning complete" in claude_md
            assert "Spawn frontend agent" in claude_md
            assert "Using React for UI" in claude_md

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_full_persistence_cycle(self, integration_config, integration_repo):
        """Full cycle: spawn → save_progress → teardown → verify persisted."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Step 1: Spawn agent
            spawn_result = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build login form"
            )
            agent_id = spawn_result["agent_id"]

            # Step 2: Save progress
            context = {
                "files_modified": ["src/Login.tsx"],
                "progress": "Login form complete",
                "next_steps": "Add validation",
                "blockers": None,
                "decisions": []
            }
            orch.state.update_agent(agent_id, context=context)

            # Verify context saved
            agent = orch.state.get_agent(agent_id)
            assert agent["context"]["progress"] == "Login form complete"

            # Step 3: Teardown
            await orch._handle_teardown_agent(agent_id)

            # Step 4: Verify state was persisted to disk
            state_dir = Path(orch.config.settings.state_dir)
            assert (state_dir / "agents.json").exists()

            # The agent was removed, but we can verify the state file is valid
            agents_data = json.loads((state_dir / "agents.json").read_text())
            assert "archie" in agents_data  # Archie should still be there

            # Cleanup
            await orch.shutdown()


class TestMessageBus:
    """Tests for message bus integration."""

    @pytest.mark.asyncio
    async def test_send_and_receive_messages(self, integration_config):
        """Agents can send and receive messages via state store."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn an agent
            spawn_result = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build feature"
            )
            agent_id = spawn_result["agent_id"]

            # Send message from Archie to agent
            orch.state.add_message("archie", agent_id, "Please update the tests")

            # Agent receives message
            messages, _ = orch.state.get_messages(agent_id)
            assert len(messages) == 1
            assert messages[0]["content"] == "Please update the tests"

            # Agent replies
            orch.state.add_message(agent_id, "archie", "Tests updated!")

            # Archie receives reply
            archie_messages, _ = orch.state.get_messages("archie")
            assert len(archie_messages) == 1
            assert archie_messages[0]["content"] == "Tests updated!"

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_broadcast_messages(self, integration_config):
        """Broadcast messages reach all agents."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn two agents
            agent1 = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Task 1"
            )
            agent2 = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Task 2"
            )

            # Broadcast message
            orch.state.add_message("archie", "broadcast", "Everyone stop for standup!")

            # Both agents receive the broadcast
            msgs1, _ = orch.state.get_messages(agent1["agent_id"])
            msgs2, _ = orch.state.get_messages(agent2["agent_id"])

            assert len(msgs1) == 1
            assert len(msgs2) == 1
            assert msgs1[0]["content"] == "Everyone stop for standup!"
            assert msgs2[0]["content"] == "Everyone stop for standup!"

            # Cleanup
            await orch.shutdown()


class TestGitHubIntegration:
    """Tests for GitHub tools integration (mocked)."""

    @pytest.mark.asyncio
    async def test_github_gate_check(self, integration_config):
        """GitHub gate verifies gh CLI is available."""
        from arch.orchestrator import check_github_gate, parse_config

        config = parse_config(integration_config)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")
            ok, msg = check_github_gate(config)

        assert ok is True
        assert "testorg/test-project" in msg

    @pytest.mark.asyncio
    async def test_github_gate_gh_not_installed(self, integration_config):
        """GitHub gate fails gracefully when gh not installed."""
        from arch.orchestrator import check_github_gate, parse_config

        config = parse_config(integration_config)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            ok, msg = check_github_gate(config)

        assert ok is False
        assert "not installed" in msg.lower()


class TestTokenTracking:
    """Tests for token usage tracking across agents."""

    @pytest.mark.asyncio
    async def test_token_usage_tracked_per_agent(self, integration_config):
        """Token usage is tracked for each agent."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Register Archie usage
            orch.token_tracker.register_agent("archie", "claude-opus-4-5")
            usage_event = json.dumps({
                "type": "usage",
                "input_tokens": 5000,
                "output_tokens": 2000,
                "cache_read_input_tokens": 500,
                "cache_creation_input_tokens": 100
            })
            orch.token_tracker.parse_stream_event("archie", usage_event)

            # Verify usage tracked
            all_usage = orch.token_tracker.get_all_usage()
            assert "archie" in all_usage
            usage = all_usage["archie"]
            assert usage["input_tokens"] == 5000
            assert usage["output_tokens"] == 2000
            assert usage["cost_usd"] > 0

            total_cost = orch.token_tracker.get_total_cost()
            assert total_cost > 0

            # Cleanup
            await orch.shutdown()


class TestAutoResume:
    """Tests for Archie auto-resume functionality (Issue #2)."""

    @pytest.mark.asyncio
    async def test_auto_resume_on_new_message(self, integration_config):
        """Archie auto-resumes when new messages arrive after exit."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Simulate Archie exit
            orch._archie_session._running = False
            orch._archie_session._session_id = "archie-session-123"
            orch._archie_last_exit_time = time.time() - 15  # Past cooldown

            # Add unread message
            orch.state.add_message("user", "archie", "Please check the PR")

            # Check unread messages
            assert orch.state.has_unread_messages_for("archie") is True

            # Mock spawn for resume
            new_session = MagicMock()
            new_session.is_running = True
            orch.session_manager.spawn = AsyncMock(return_value=new_session)

            # Trigger auto-resume check
            await orch._check_auto_resume()

            # Verify spawn was called with resume
            orch.session_manager.spawn.assert_called_once()
            call_kwargs = orch.session_manager.spawn.call_args[1]
            assert call_kwargs["resume_session_id"] == "archie-session-123"

            # Cleanup
            await orch.shutdown()


class TestMultiAgentCoordination:
    """Tests for coordinating multiple agents."""

    @pytest.mark.asyncio
    async def test_spawn_multiple_agents(self, integration_config):
        """Orchestrator can manage multiple concurrent agents."""
        orch = Orchestrator(integration_config)

        with mock_external_tools():
            await orch.startup()

            # Spawn multiple frontend agents (max_instances: 2)
            agent1 = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build navbar"
            )
            agent2 = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build footer"
            )

            # Both should succeed
            assert "agent_id" in agent1
            assert "agent_id" in agent2
            assert agent1["agent_id"] != agent2["agent_id"]

            # Third should fail (max_instances exceeded)
            agent3 = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build sidebar"
            )
            assert "error" in agent3
            assert "Max instances" in agent3["error"]

            # List agents should show all active
            agents = orch.state.list_agents()
            agent_ids = [a["id"] for a in agents]
            assert "archie" in agent_ids
            assert agent1["agent_id"] in agent_ids
            assert agent2["agent_id"] in agent_ids

            # Cleanup
            await orch.shutdown()

    @pytest.mark.asyncio
    async def test_mixed_local_and_sandboxed_agents(self, integration_config):
        """Can run local and sandboxed agents simultaneously."""
        orch = Orchestrator(integration_config)

        with mock_external_tools() as mock_exec:
            mock_exec.side_effect = [
                create_mock_claude_process(session_id="archie-session"),
                create_mock_claude_process(session_id="frontend-session"),
                create_mock_docker_process(),
            ]

            await orch.startup()

            # Spawn local agent
            local_agent = await orch._handle_spawn_agent(
                role="frontend-dev",
                assignment="Build UI"
            )

            # Spawn sandboxed agent
            sandboxed_agent = await orch._handle_spawn_agent(
                role="security-auditor",
                assignment="Audit code"
            )

            assert local_agent["sandboxed"] is False
            assert sandboxed_agent["sandboxed"] is True

            # Both in state
            agents = orch.state.list_agents()
            assert len(agents) == 3  # archie + 2 spawned

            # Cleanup
            await orch.shutdown()
