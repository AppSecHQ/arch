"""Unit tests for ARCH Session Manager."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from arch.session import (
    AgentConfig,
    Session,
    SessionManager,
    generate_mcp_config,
    log_permissions_audit,
)
from arch.state import StateStore
from arch.token_tracker import TokenTracker


class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_default_values(self):
        """AgentConfig has sensible defaults."""
        config = AgentConfig(agent_id="test", role="test-role")

        assert config.agent_id == "test"
        assert config.role == "test-role"
        assert config.model == "claude-sonnet-4-6"
        assert config.sandboxed is False
        assert config.skip_permissions is False

    def test_all_values(self):
        """AgentConfig accepts all parameters."""
        config = AgentConfig(
            agent_id="sec-1",
            role="security",
            model="claude-opus-4-5",
            worktree="/path/to/wt",
            sandboxed=True,
            skip_permissions=True,
            container_image="custom:latest",
            container_memory_limit="2g",
            container_cpus=1.5,
        )

        assert config.model == "claude-opus-4-5"
        assert config.sandboxed is True
        assert config.skip_permissions is True
        assert config.container_memory_limit == "2g"


class TestGenerateMCPConfig:
    """Tests for MCP config generation."""

    def test_generate_local_config(self, tmp_path):
        """generate_mcp_config creates correct local config."""
        config_path = generate_mcp_config(
            agent_id="frontend-1",
            mcp_port=3999,
            state_dir=tmp_path,
            is_container=False
        )

        assert config_path.exists()

        config = json.loads(config_path.read_text())
        assert config["mcpServers"]["arch"]["type"] == "sse"
        assert config["mcpServers"]["arch"]["url"] == "http://localhost:3999/sse/frontend-1"

    def test_generate_container_config(self, tmp_path):
        """generate_mcp_config creates correct container config."""
        config_path = generate_mcp_config(
            agent_id="backend-1",
            mcp_port=4000,
            state_dir=tmp_path,
            is_container=True
        )

        config = json.loads(config_path.read_text())
        assert "host.docker.internal" in config["mcpServers"]["arch"]["url"]
        assert ":4000/sse/backend-1" in config["mcpServers"]["arch"]["url"]

    def test_config_file_naming(self, tmp_path):
        """Config file is named {agent_id}-mcp.json."""
        config_path = generate_mcp_config("my-agent", 3999, tmp_path)
        assert config_path.name == "my-agent-mcp.json"


class TestLogPermissionsAudit:
    """Tests for permissions audit logging."""

    def test_log_creates_file(self, tmp_path):
        """log_permissions_audit creates audit log file."""
        log_permissions_audit(tmp_path, "sec-1", "security")

        audit_path = tmp_path / "permissions_audit.log"
        assert audit_path.exists()

    def test_log_format(self, tmp_path):
        """log_permissions_audit writes correct format."""
        log_permissions_audit(tmp_path, "sec-1", "security", "admin")

        content = (tmp_path / "permissions_audit.log").read_text()
        assert "SKIP_PERMISSIONS" in content
        assert "agent_id=sec-1" in content
        assert "role=security" in content
        assert "approved_by=admin" in content

    def test_log_appends(self, tmp_path):
        """log_permissions_audit appends to existing log."""
        log_permissions_audit(tmp_path, "agent-1", "role-1")
        log_permissions_audit(tmp_path, "agent-2", "role-2")

        content = (tmp_path / "permissions_audit.log").read_text()
        assert "agent-1" in content
        assert "agent-2" in content
        assert content.count("SKIP_PERMISSIONS") == 2


class TestSession:
    """Tests for Session class."""

    def test_session_properties(self, session):
        """Session exposes correct properties."""
        assert session.agent_id == "test-agent"
        assert session.is_running is False
        assert session.session_id is None
        assert session.pid is None

    @pytest.mark.asyncio
    async def test_spawn_builds_correct_command(self, session, mock_subprocess):
        """spawn() builds correct claude command."""
        await session.spawn("Build the navbar")

        # Check command was built correctly
        call_args = mock_subprocess.call_args
        cmd = call_args[0]  # First positional arg is the command tuple

        assert cmd[0] == "claude"
        assert "--model" in cmd
        assert "claude-sonnet-4-6" in cmd
        assert "--output-format" in cmd
        assert "stream-json" in cmd
        assert "--mcp-config" in cmd
        assert "--print" in cmd

    @pytest.mark.asyncio
    async def test_spawn_with_skip_permissions(self, session_skip_perms, mock_subprocess, tmp_path):
        """spawn() adds --dangerously-skip-permissions flag."""
        await session_skip_perms.spawn("Audit the code")

        cmd = mock_subprocess.call_args[0]
        assert "--dangerously-skip-permissions" in cmd

        # Check audit log was created
        audit_path = tmp_path / "permissions_audit.log"
        assert audit_path.exists()

    @pytest.mark.asyncio
    async def test_spawn_with_resume(self, session, mock_subprocess):
        """spawn() adds --resume flag when session_id provided."""
        await session.spawn("Continue work", resume_session_id="abc123")

        cmd = mock_subprocess.call_args[0]
        assert "--resume" in cmd
        assert "abc123" in cmd
        # Prompt should not be in command when resuming
        assert "Continue work" not in cmd

    @pytest.mark.asyncio
    async def test_spawn_sets_cwd_to_worktree(self, state, token_tracker, tmp_path):
        """spawn() sets working directory to worktree."""
        worktree = tmp_path / "worktree"
        worktree.mkdir()

        config = AgentConfig(
            agent_id="wt-agent",
            role="test",
            worktree=str(worktree)
        )
        session = Session(config, state, token_tracker, tmp_path, 3999)

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock:
            mock.return_value = create_mock_process()
            await session.spawn("Do work")

            # Check cwd was set
            call_kwargs = mock.call_args[1]
            assert call_kwargs["cwd"] == str(worktree)

    @pytest.mark.asyncio
    async def test_spawn_registers_with_token_tracker(self, session, mock_subprocess):
        """spawn() registers agent with token tracker."""
        await session.spawn("Build something")

        usage = session.token_tracker.get_agent_usage("test-agent")
        assert usage is not None

    @pytest.mark.asyncio
    async def test_spawn_updates_state(self, session, mock_subprocess):
        """spawn() updates agent state to working."""
        session.state.register_agent("test-agent", "test", "/wt")
        await session.spawn("Build something")

        agent = session.state.get_agent("test-agent")
        assert agent["status"] == "working"
        assert agent["pid"] is not None

    @pytest.mark.asyncio
    async def test_spawn_returns_false_if_already_running(self, session, mock_subprocess):
        """spawn() returns False if already running."""
        await session.spawn("First spawn")
        result = await session.spawn("Second spawn")

        assert result is False

    @pytest.mark.asyncio
    async def test_spawn_returns_false_if_claude_not_found(self, session):
        """spawn() returns False if claude CLI not found."""
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await session.spawn("Build something")

        assert result is False


class TestSessionOutputParsing:
    """Tests for session output parsing."""

    @pytest.mark.asyncio
    async def test_parses_usage_events(self, session, mock_subprocess):
        """Session parses usage events and tracks tokens."""
        # Set up mock to emit usage event
        usage_event = json.dumps({
            "type": "usage",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 50
        })

        mock_process = create_mock_process([usage_event])
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")

        # Wait for output processing
        await asyncio.sleep(0.1)

        # Check tokens were tracked
        usage = session.token_tracker.get_agent_usage("test-agent")
        assert usage["input_tokens"] == 1000
        assert usage["output_tokens"] == 500

    @pytest.mark.asyncio
    async def test_extracts_session_id_from_result(self, session, mock_subprocess):
        """Session extracts session_id from result event."""
        result_event = json.dumps({
            "type": "result",
            "session_id": "session-xyz-789"
        })

        mock_process = create_mock_process([result_event])
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")

        # Wait for output processing
        await asyncio.sleep(0.1)

        assert session.session_id == "session-xyz-789"

    @pytest.mark.asyncio
    async def test_persists_session_id_to_state(self, session, mock_subprocess):
        """Session persists session_id to state store."""
        session.state.register_agent("test-agent", "test", "/wt")

        result_event = json.dumps({
            "type": "result",
            "session_id": "persisted-session"
        })

        mock_process = create_mock_process([result_event])
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        await asyncio.sleep(0.1)

        agent = session.state.get_agent("test-agent")
        assert agent["session_id"] == "persisted-session"

    @pytest.mark.asyncio
    async def test_calls_output_callback(self, state, token_tracker, tmp_path, mock_subprocess):
        """Session calls on_output callback for each event."""
        events_received = []

        async def on_output(agent_id, event):
            events_received.append((agent_id, event))

        config = AgentConfig(agent_id="callback-agent", role="test")
        session = Session(config, state, token_tracker, tmp_path, 3999, on_output=on_output)

        mock_process = create_mock_process([
            json.dumps({"type": "assistant", "message": {"content": "Hello"}}),
            json.dumps({"type": "result", "session_id": "abc"})
        ])
        mock_subprocess.return_value = mock_process

        await session.spawn("Test")
        await asyncio.sleep(0.1)

        assert len(events_received) == 2
        assert events_received[0][0] == "callback-agent"


class TestSessionExit:
    """Tests for session exit handling."""

    @pytest.mark.asyncio
    async def test_normal_exit_sets_done_status(self, session, mock_subprocess):
        """Normal exit (code 0) sets status to done."""
        session.state.register_agent("test-agent", "test", "/wt")

        mock_process = create_mock_process([], exit_code=0)
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        await asyncio.sleep(0.1)

        agent = session.state.get_agent("test-agent")
        assert agent["status"] == "done"

    @pytest.mark.asyncio
    async def test_error_exit_sets_error_status(self, session, mock_subprocess):
        """Non-zero exit sets status to error."""
        session.state.register_agent("test-agent", "test", "/wt")

        mock_process = create_mock_process([], exit_code=1)
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        await asyncio.sleep(0.1)

        agent = session.state.get_agent("test-agent")
        assert agent["status"] == "error"

    @pytest.mark.asyncio
    async def test_error_exit_notifies_archie(self, session, mock_subprocess):
        """Non-zero exit sends message to Archie."""
        session.state.register_agent("test-agent", "test", "/wt")

        mock_process = create_mock_process([], exit_code=1)
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        await asyncio.sleep(0.1)

        messages, _ = session.state.get_messages("archie")
        assert len(messages) == 1
        assert "exited unexpectedly" in messages[0]["content"]
        assert "test-agent" in messages[0]["content"]

    @pytest.mark.asyncio
    async def test_calls_exit_callback(self, state, token_tracker, tmp_path, mock_subprocess):
        """Session calls on_exit callback."""
        exit_received = []

        async def on_exit(agent_id, exit_code):
            exit_received.append((agent_id, exit_code))

        config = AgentConfig(agent_id="exit-agent", role="test")
        session = Session(config, state, token_tracker, tmp_path, 3999, on_exit=on_exit)

        mock_process = create_mock_process([], exit_code=0)
        mock_subprocess.return_value = mock_process

        await session.spawn("Test")
        await asyncio.sleep(0.1)

        assert exit_received == [("exit-agent", 0)]


class TestSessionStop:
    """Tests for stopping sessions."""

    @pytest.mark.asyncio
    async def test_stop_terminates_process(self, session, mock_subprocess):
        """stop() terminates the subprocess."""
        mock_process = create_mock_process([], hang=True)
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        result = await session.stop(timeout=0.1)

        assert result is True
        mock_process.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_kills_if_terminate_times_out(self, session, mock_subprocess):
        """stop() kills process if terminate times out."""
        mock_process = create_mock_process([], hang=True)
        # Make wait() time out on first call
        mock_process.wait = AsyncMock(side_effect=[asyncio.TimeoutError, 0])
        mock_subprocess.return_value = mock_process

        await session.spawn("Build something")
        await session.stop(timeout=0.1)

        mock_process.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_returns_true_if_not_running(self, session):
        """stop() returns True if session not running."""
        result = await session.stop()
        assert result is True


class TestSessionManager:
    """Tests for SessionManager class."""

    @pytest.mark.asyncio
    async def test_spawn_creates_session(self, session_manager, mock_subprocess):
        """spawn() creates and tracks a session."""
        config = AgentConfig(agent_id="new-agent", role="test")
        session = await session_manager.spawn(config, "Build something")

        assert session is not None
        assert session.agent_id == "new-agent"
        assert session_manager.get_session("new-agent") is session

    @pytest.mark.asyncio
    async def test_spawn_returns_existing_if_running(self, session_manager, mock_subprocess):
        """spawn() returns existing session if already running."""
        config = AgentConfig(agent_id="existing", role="test")

        session1 = await session_manager.spawn(config, "First")
        session2 = await session_manager.spawn(config, "Second")

        assert session1 is session2

    @pytest.mark.asyncio
    async def test_list_sessions(self, session_manager, mock_subprocess):
        """list_sessions() returns all sessions."""
        config1 = AgentConfig(agent_id="agent-1", role="test")
        config2 = AgentConfig(agent_id="agent-2", role="test")

        await session_manager.spawn(config1, "Task 1")
        await session_manager.spawn(config2, "Task 2")

        sessions = session_manager.list_sessions()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_stop_session(self, session_manager, mock_subprocess):
        """stop() stops a specific session."""
        mock_process = create_mock_process([], hang=True)
        mock_subprocess.return_value = mock_process

        config = AgentConfig(agent_id="to-stop", role="test")
        await session_manager.spawn(config, "Task")

        result = await session_manager.stop("to-stop", timeout=0.1)
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_all(self, session_manager, mock_subprocess):
        """stop_all() stops all running sessions."""
        mock_process = create_mock_process([], hang=True)
        mock_subprocess.return_value = mock_process

        config1 = AgentConfig(agent_id="agent-1", role="test")
        config2 = AgentConfig(agent_id="agent-2", role="test")

        await session_manager.spawn(config1, "Task 1")
        await session_manager.spawn(config2, "Task 2")

        stopped = await session_manager.stop_all(timeout=0.1)
        assert stopped == 2

    def test_remove_session(self, session_manager):
        """remove_session() removes session from tracking."""
        # Add a mock session directly
        session_manager._sessions["to-remove"] = Mock()

        result = session_manager.remove_session("to-remove")
        assert result is True
        assert "to-remove" not in session_manager._sessions


# --- Helper Functions ---

def create_mock_process(output_lines=None, exit_code=0, hang=False):
    """Create a mock asyncio subprocess."""
    mock = MagicMock()
    mock.pid = 12345

    # Create async readline that returns output lines
    if output_lines is None:
        output_lines = []

    output_iter = iter(output_lines + [None])  # None signals EOF

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

    # Wait returns exit code
    if hang:
        # For hang mode, wait times out but process stays alive
        async def wait():
            await asyncio.sleep(10)  # Long sleep that gets cancelled
            return exit_code
        mock.wait = wait
    else:
        mock.wait = AsyncMock(return_value=exit_code)

    mock.terminate = MagicMock()
    mock.kill = MagicMock()
    mock.send_signal = MagicMock()

    return mock


# --- Fixtures ---

@pytest.fixture
def state(tmp_path):
    """Create a StateStore with temporary directory."""
    return StateStore(tmp_path / "state")


@pytest.fixture
def token_tracker(tmp_path):
    """Create a TokenTracker with temporary directory."""
    return TokenTracker(state_dir=tmp_path / "state")


@pytest.fixture
def session(state, token_tracker, tmp_path):
    """Create a Session for testing."""
    config = AgentConfig(agent_id="test-agent", role="test")
    return Session(config, state, token_tracker, tmp_path, 3999)


@pytest.fixture
def session_skip_perms(state, token_tracker, tmp_path):
    """Create a Session with skip_permissions enabled."""
    config = AgentConfig(
        agent_id="secure-agent",
        role="security",
        skip_permissions=True
    )
    return Session(config, state, token_tracker, tmp_path, 3999)


@pytest.fixture
def session_manager(state, token_tracker, tmp_path):
    """Create a SessionManager for testing."""
    return SessionManager(state, token_tracker, tmp_path, 3999)


@pytest.fixture
def mock_subprocess():
    """Mock asyncio.create_subprocess_exec."""
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock:
        mock.return_value = create_mock_process()
        yield mock
