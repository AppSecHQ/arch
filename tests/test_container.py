"""Unit tests for ARCH Container Manager."""

import asyncio
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from arch.container import (
    ContainerConfig,
    ContainerManager,
    ContainerSession,
    DEFAULT_DOCKERFILE,
    build_default_image,
    build_docker_command,
    check_docker_available,
    check_image_exists,
    get_container_name,
    pull_image,
    write_default_dockerfile,
)


class TestContainerConfig:
    """Tests for ContainerConfig dataclass."""

    def test_default_values(self):
        """ContainerConfig has sensible defaults."""
        config = ContainerConfig(agent_id="test")

        assert config.agent_id == "test"
        assert config.image == "arch-agent:latest"
        assert config.memory_limit is None
        assert config.cpus is None
        assert config.network == "bridge"
        assert config.extra_mounts == []

    def test_all_values(self):
        """ContainerConfig accepts all parameters."""
        config = ContainerConfig(
            agent_id="heavy",
            image="custom:v2",
            memory_limit="4g",
            cpus=2.0,
            network="none",
            extra_mounts=["/data", "/config"]
        )

        assert config.image == "custom:v2"
        assert config.memory_limit == "4g"
        assert config.cpus == 2.0
        assert config.network == "none"
        assert len(config.extra_mounts) == 2


class TestDockerChecks:
    """Tests for Docker availability checks."""

    def test_check_docker_available_success(self):
        """check_docker_available returns True when Docker is running."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=0, stderr="")

            available, msg = check_docker_available()

            assert available is True
            assert "available" in msg.lower()

    def test_check_docker_available_not_running(self):
        """check_docker_available returns False when Docker is not running."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=1, stderr="Cannot connect to Docker daemon")

            available, msg = check_docker_available()

            assert available is False
            assert "Cannot connect" in msg

    def test_check_docker_available_not_installed(self):
        """check_docker_available returns False when Docker is not installed."""
        with patch("subprocess.run", side_effect=FileNotFoundError):
            available, msg = check_docker_available()

            assert available is False
            assert "not found" in msg.lower()

    def test_check_docker_available_timeout(self):
        """check_docker_available handles timeout."""
        with patch("subprocess.run", side_effect=TimeoutError):
            available, msg = check_docker_available()

            assert available is False

    def test_check_image_exists_true(self):
        """check_image_exists returns True for existing image."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=0)

            assert check_image_exists("arch-agent:latest") is True

    def test_check_image_exists_false(self):
        """check_image_exists returns False for missing image."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=1)

            assert check_image_exists("nonexistent:latest") is False

    def test_pull_image_success(self):
        """pull_image returns success for valid pull."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=0, stdout="Pulled")

            success, msg = pull_image("arch-agent:latest")

            assert success is True
            assert "Pulled" in msg

    def test_pull_image_failure(self):
        """pull_image returns failure for invalid image."""
        with patch("subprocess.run") as mock:
            mock.return_value = Mock(returncode=1, stderr="not found")

            success, msg = pull_image("nonexistent:latest")

            assert success is False
            assert "Failed" in msg


class TestBuildDockerCommand:
    """Tests for Docker command building."""

    def test_basic_command(self, tmp_path):
        """build_docker_command creates basic command."""
        config = ContainerConfig(agent_id="test")
        worktree = tmp_path / "worktree"
        mcp_config = tmp_path / "mcp.json"

        cmd = build_docker_command(
            "test-agent",
            config,
            worktree,
            mcp_config,
            ["claude", "--print", "Hello"]
        )

        assert cmd[0] == "docker"
        assert "run" in cmd
        assert "--rm" in cmd
        assert "--name" in cmd
        assert "arch-test-agent" in cmd

    def test_volume_mounts(self, tmp_path):
        """build_docker_command includes volume mounts."""
        config = ContainerConfig(agent_id="test")
        worktree = tmp_path / "worktree"
        mcp_config = tmp_path / "mcp.json"

        cmd = build_docker_command("test", config, worktree, mcp_config, ["claude"])

        # Find volume mount flags
        cmd_str = " ".join(cmd)
        assert f"{worktree}:/workspace" in cmd_str
        assert f"{mcp_config}:/arch/mcp-config.json:ro" in cmd_str

    def test_host_docker_internal(self, tmp_path):
        """build_docker_command includes host.docker.internal mapping."""
        config = ContainerConfig(agent_id="test")

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude"]
        )

        assert "--add-host" in cmd
        assert "host.docker.internal:host-gateway" in cmd

    def test_memory_limit(self, tmp_path):
        """build_docker_command includes memory limit."""
        config = ContainerConfig(agent_id="test", memory_limit="2g")

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude"]
        )

        assert "--memory" in cmd
        assert "2g" in cmd

    def test_cpu_limit(self, tmp_path):
        """build_docker_command includes CPU limit."""
        config = ContainerConfig(agent_id="test", cpus=1.5)

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude"]
        )

        assert "--cpus" in cmd
        assert "1.5" in cmd

    def test_network_none(self, tmp_path):
        """build_docker_command includes network none."""
        config = ContainerConfig(agent_id="test", network="none")

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude"]
        )

        assert "--network" in cmd
        assert "none" in cmd

    def test_extra_mounts(self, tmp_path):
        """build_docker_command includes extra mounts."""
        config = ContainerConfig(
            agent_id="test",
            extra_mounts=["/data/models", "/config/certs"]
        )

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude"]
        )

        cmd_str = " ".join(cmd)
        assert "/data/models:/data/models:ro" in cmd_str
        assert "/config/certs:/config/certs:ro" in cmd_str

    def test_api_key_passthrough(self, tmp_path):
        """build_docker_command passes API key via environment."""
        config = ContainerConfig(agent_id="test")

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test-key"}):
            cmd = build_docker_command(
                "test",
                config,
                tmp_path / "wt",
                tmp_path / "mcp.json",
                ["claude"]
            )

        assert "-e" in cmd
        assert "ANTHROPIC_API_KEY=sk-test-key" in cmd

    def test_image_and_claude_args(self, tmp_path):
        """build_docker_command includes image and claude args."""
        config = ContainerConfig(agent_id="test", image="custom:v1")

        cmd = build_docker_command(
            "test",
            config,
            tmp_path / "wt",
            tmp_path / "mcp.json",
            ["claude", "--model", "opus", "--print", "Hello"]
        )

        # Image should be before claude args
        image_idx = cmd.index("custom:v1")
        claude_idx = cmd.index("claude")
        assert image_idx < claude_idx

        # Claude args should be at the end
        assert cmd[-1] == "Hello"


class TestContainerSession:
    """Tests for ContainerSession class."""

    def test_container_name(self, container_session):
        """ContainerSession has correct container name."""
        assert container_session.container_name == "arch-test-agent"

    def test_initial_state(self, container_session):
        """ContainerSession starts not running."""
        assert container_session.is_running is False

    @pytest.mark.asyncio
    async def test_spawn_checks_docker(self, container_session):
        """spawn() checks Docker availability."""
        with patch("arch.container.check_docker_available", return_value=(False, "Not available")):
            result = await container_session.spawn("Test prompt")

        assert result is False

    @pytest.mark.asyncio
    async def test_spawn_checks_image(self, container_session, mock_docker_available):
        """spawn() checks image exists and pulls if needed."""
        with patch("arch.container.check_image_exists", return_value=False):
            with patch("arch.container.pull_image", return_value=(True, "Pulled")) as mock_pull:
                with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                    mock_exec.return_value = create_mock_process()
                    await container_session.spawn("Test")

                mock_pull.assert_called_once()

    @pytest.mark.asyncio
    async def test_spawn_builds_correct_command(self, container_session, mock_docker_available):
        """spawn() builds correct docker command."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()
                await container_session.spawn("Build the navbar")

                # Check docker run was called
                call_args = mock_exec.call_args[0]
                assert call_args[0] == "docker"
                assert "run" in call_args

    @pytest.mark.asyncio
    async def test_spawn_with_resume(self, container_session, mock_docker_available):
        """spawn() includes --resume flag when session_id provided."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()
                await container_session.spawn("Test", resume_session_id="abc123")

                call_args = mock_exec.call_args[0]
                assert "--resume" in call_args
                assert "abc123" in call_args

    @pytest.mark.asyncio
    async def test_spawn_sets_running(self, container_session, mock_docker_available):
        """spawn() sets is_running to True."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()
                await container_session.spawn("Test")

        assert container_session.is_running is True

    @pytest.mark.asyncio
    async def test_stop_uses_docker_stop(self, container_session, mock_docker_available):
        """stop() uses docker stop command."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()
                await container_session.spawn("Test")

                # Reset mock to track stop call
                mock_exec.reset_mock()
                mock_exec.return_value = create_mock_process()

                await container_session.stop(timeout=10)

                # Check docker stop was called
                call_args = mock_exec.call_args[0]
                assert "docker" in call_args
                assert "stop" in call_args

    @pytest.mark.asyncio
    async def test_kill_uses_docker_kill(self, container_session, mock_docker_available):
        """kill() uses docker kill command."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()
                await container_session.spawn("Test")

                mock_exec.reset_mock()
                mock_exec.return_value = create_mock_process()

                await container_session.kill()

                call_args = mock_exec.call_args[0]
                assert "docker" in call_args
                assert "kill" in call_args


class TestContainerManager:
    """Tests for ContainerManager class."""

    def test_check_prerequisites_success(self):
        """check_prerequisites returns True when Docker available."""
        manager = ContainerManager()

        with patch("arch.container.check_docker_available", return_value=(True, "OK")):
            ok, errors = manager.check_prerequisites()

        assert ok is True
        assert len(errors) == 0

    def test_check_prerequisites_failure(self):
        """check_prerequisites returns False when Docker unavailable."""
        manager = ContainerManager()

        with patch("arch.container.check_docker_available", return_value=(False, "Not running")):
            ok, errors = manager.check_prerequisites()

        assert ok is False
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_spawn_creates_session(self, container_manager, tmp_path, mock_docker_available):
        """spawn() creates and tracks a session."""
        config = ContainerConfig(agent_id="new-agent")

        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()

                session = await container_manager.spawn(
                    "new-agent",
                    config,
                    tmp_path / "wt",
                    tmp_path / "mcp.json",
                    "Test prompt"
                )

        assert session is not None
        assert session.agent_id == "new-agent"
        assert container_manager.get_session("new-agent") is session

    @pytest.mark.asyncio
    async def test_spawn_returns_existing(self, container_manager, tmp_path, mock_docker_available):
        """spawn() returns existing session if already running."""
        config = ContainerConfig(agent_id="existing")

        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()

                session1 = await container_manager.spawn(
                    "existing", config, tmp_path / "wt", tmp_path / "mcp.json", "First"
                )
                session2 = await container_manager.spawn(
                    "existing", config, tmp_path / "wt", tmp_path / "mcp.json", "Second"
                )

        assert session1 is session2

    @pytest.mark.asyncio
    async def test_list_sessions(self, container_manager, tmp_path, mock_docker_available):
        """list_sessions() returns all sessions."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()

                await container_manager.spawn(
                    "agent-1",
                    ContainerConfig(agent_id="agent-1"),
                    tmp_path / "wt1",
                    tmp_path / "mcp1.json",
                    "Task 1"
                )
                await container_manager.spawn(
                    "agent-2",
                    ContainerConfig(agent_id="agent-2"),
                    tmp_path / "wt2",
                    tmp_path / "mcp2.json",
                    "Task 2"
                )

        sessions = container_manager.list_sessions()
        assert len(sessions) == 2

    @pytest.mark.asyncio
    async def test_stop_all(self, container_manager, tmp_path, mock_docker_available):
        """stop_all() stops all running containers."""
        with patch("arch.container.check_image_exists", return_value=True):
            with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
                mock_exec.return_value = create_mock_process()

                await container_manager.spawn(
                    "agent-1",
                    ContainerConfig(agent_id="agent-1"),
                    tmp_path / "wt1",
                    tmp_path / "mcp1.json",
                    "Task 1"
                )
                await container_manager.spawn(
                    "agent-2",
                    ContainerConfig(agent_id="agent-2"),
                    tmp_path / "wt2",
                    tmp_path / "mcp2.json",
                    "Task 2"
                )

                stopped = await container_manager.stop_all(timeout=1)

        assert stopped == 2

    def test_remove_session(self, container_manager):
        """remove_session() removes session from tracking."""
        container_manager._containers["to-remove"] = Mock()

        result = container_manager.remove_session("to-remove")

        assert result is True
        assert "to-remove" not in container_manager._containers


class TestDockerfile:
    """Tests for Dockerfile utilities."""

    def test_default_dockerfile_content(self):
        """DEFAULT_DOCKERFILE has required components."""
        assert "FROM python:3.11" in DEFAULT_DOCKERFILE
        assert "nodejs" in DEFAULT_DOCKERFILE
        assert "anthropic" in DEFAULT_DOCKERFILE
        assert "claude-code" in DEFAULT_DOCKERFILE
        assert "WORKDIR /workspace" in DEFAULT_DOCKERFILE

    def test_write_default_dockerfile(self, tmp_path):
        """write_default_dockerfile creates Dockerfile."""
        path = write_default_dockerfile(tmp_path)

        assert path.exists()
        assert path.name == "Dockerfile"
        assert path.read_text() == DEFAULT_DOCKERFILE

    @pytest.mark.asyncio
    async def test_build_default_image_success(self, tmp_path):
        """build_default_image builds image successfully."""
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_process = create_mock_process()
            mock_process.communicate = AsyncMock(return_value=(b"Built", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            success, msg = await build_default_image(tmp_path, "test:latest")

            assert success is True
            assert "successfully" in msg.lower()

    @pytest.mark.asyncio
    async def test_build_default_image_failure(self, tmp_path):
        """build_default_image handles build failure."""
        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
            mock_process = create_mock_process()
            mock_process.communicate = AsyncMock(return_value=(b"", b"Error"))
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            success, msg = await build_default_image(tmp_path, "test:latest")

            assert success is False
            assert "failed" in msg.lower()


class TestGetContainerName:
    """Tests for container name generation."""

    def test_get_container_name(self):
        """get_container_name returns correct format."""
        assert get_container_name("frontend-1") == "arch-frontend-1"
        assert get_container_name("archie") == "arch-archie"
        assert get_container_name("backend-dev-2") == "arch-backend-dev-2"


# --- Helper Functions ---

def create_mock_process():
    """Create a mock asyncio subprocess."""
    mock = MagicMock()
    mock.pid = 12345
    mock.stdout = MagicMock()
    mock.stderr = MagicMock()
    mock.wait = AsyncMock(return_value=0)
    mock.communicate = AsyncMock(return_value=(b"", b""))
    mock.returncode = 0
    return mock


# --- Fixtures ---

@pytest.fixture
def container_session(tmp_path):
    """Create a ContainerSession for testing."""
    config = ContainerConfig(agent_id="test-agent")
    return ContainerSession(
        agent_id="test-agent",
        config=config,
        worktree_path=tmp_path / "worktree",
        mcp_config_path=tmp_path / "mcp.json",
    )


@pytest.fixture
def container_manager():
    """Create a ContainerManager for testing."""
    return ContainerManager()


@pytest.fixture
def mock_docker_available():
    """Mock Docker as available."""
    with patch("arch.container.check_docker_available", return_value=(True, "OK")):
        yield
