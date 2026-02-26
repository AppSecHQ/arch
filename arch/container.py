"""
ARCH Container Manager

Handles Docker-based agent isolation. Spawns claude CLI processes inside
containers with mounted worktrees and MCP connectivity to the host.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default timeout for Docker operations
DEFAULT_TIMEOUT = 60


@dataclass
class ContainerConfig:
    """Configuration for a containerized agent."""
    agent_id: str
    image: str = "arch-agent:latest"
    memory_limit: Optional[str] = None  # e.g., "2g"
    cpus: Optional[float] = None  # e.g., 1.5
    network: str = "bridge"  # "bridge" | "none" | "host"
    extra_mounts: list[str] = field(default_factory=list)


def check_docker_available() -> tuple[bool, str]:
    """
    Check if Docker daemon is running.

    Returns:
        Tuple of (available, message).
    """
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT
        )
        if result.returncode == 0:
            return True, "Docker is available"
        else:
            return False, f"Docker error: {result.stderr}"
    except FileNotFoundError:
        return False, "Docker CLI not found. Install Docker to use containerized agents."
    except subprocess.TimeoutExpired:
        return False, "Docker info timed out. Is the Docker daemon running?"
    except Exception as e:
        return False, f"Docker check failed: {e}"


def check_image_exists(image: str) -> bool:
    """
    Check if a Docker image exists locally.

    Args:
        image: Image name with optional tag.

    Returns:
        True if image exists locally.
    """
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


def pull_image(image: str) -> tuple[bool, str]:
    """
    Pull a Docker image.

    Args:
        image: Image name with optional tag.

    Returns:
        Tuple of (success, message).
    """
    try:
        logger.info(f"Pulling Docker image: {image}")
        result = subprocess.run(
            ["docker", "pull", image],
            capture_output=True,
            text=True,
            timeout=300  # 5 minutes for pull
        )
        if result.returncode == 0:
            return True, f"Pulled {image}"
        else:
            return False, f"Failed to pull {image}: {result.stderr}"
    except subprocess.TimeoutExpired:
        return False, f"Pull timed out for {image}"
    except Exception as e:
        return False, f"Pull failed: {e}"


def build_docker_command(
    agent_id: str,
    config: ContainerConfig,
    worktree_path: Path,
    mcp_config_path: Path,
    claude_args: list[str],
) -> list[str]:
    """
    Build the docker run command for a containerized agent.

    Args:
        agent_id: Agent identifier.
        config: Container configuration.
        worktree_path: Path to agent's worktree.
        mcp_config_path: Path to MCP config JSON.
        claude_args: Arguments to pass to claude CLI inside container.

    Returns:
        Complete docker command as list of strings.
    """
    container_name = f"arch-{agent_id}"

    cmd = [
        "docker", "run",
        "--rm",  # Auto-remove on exit
        "--name", container_name,
        "-v", f"{worktree_path}:/workspace",  # Mount worktree
        "-v", f"{mcp_config_path}:/arch/mcp-config.json:ro",  # Mount MCP config
        "-w", "/workspace",  # Working directory
        "--add-host", "host.docker.internal:host-gateway",  # Linux host access
    ]

    # Pass API key via environment
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        cmd.extend(["-e", f"ANTHROPIC_API_KEY={api_key}"])
    else:
        logger.warning("ANTHROPIC_API_KEY not set in environment")

    # Apply resource limits
    if config.memory_limit:
        cmd.extend(["--memory", config.memory_limit])

    if config.cpus:
        cmd.extend(["--cpus", str(config.cpus)])

    if config.network == "none":
        cmd.extend(["--network", "none"])
    elif config.network == "host":
        cmd.extend(["--network", "host"])
    # "bridge" is default, no flag needed

    # Extra read-only mounts
    for mount in config.extra_mounts:
        cmd.extend(["-v", f"{mount}:{mount}:ro"])

    # Image
    cmd.append(config.image)

    # Claude command inside container
    cmd.extend(claude_args)

    return cmd


def get_container_name(agent_id: str) -> str:
    """Get the Docker container name for an agent."""
    return f"arch-{agent_id}"


class ContainerSession:
    """
    Manages a containerized claude CLI session.

    Similar to Session but runs inside a Docker container.
    """

    def __init__(
        self,
        agent_id: str,
        config: ContainerConfig,
        worktree_path: Path,
        mcp_config_path: Path,
        model: str = "claude-sonnet-4-6",
        skip_permissions: bool = False,
    ):
        """
        Initialize a container session.

        Args:
            agent_id: Agent identifier.
            config: Container configuration.
            worktree_path: Path to agent's worktree.
            mcp_config_path: Path to MCP config JSON.
            model: Claude model to use.
            skip_permissions: Whether to use --dangerously-skip-permissions.
        """
        self.agent_id = agent_id
        self.config = config
        self.worktree_path = Path(worktree_path)
        self.mcp_config_path = Path(mcp_config_path)
        self.model = model
        self.skip_permissions = skip_permissions

        self._process: Optional[asyncio.subprocess.Process] = None
        self._running = False
        self._container_name = get_container_name(agent_id)

    @property
    def container_name(self) -> str:
        """Get the container name."""
        return self._container_name

    @property
    def is_running(self) -> bool:
        """Check if the container is running."""
        return self._running

    def _build_claude_args(self, prompt: str, resume_session_id: Optional[str] = None) -> list[str]:
        """Build claude CLI arguments for inside the container."""
        args = [
            "claude",
            "--model", self.model,
            "--output-format", "stream-json",
            "--mcp-config", "/arch/mcp-config.json",
            "--print",
        ]

        if self.skip_permissions:
            args.append("--dangerously-skip-permissions")

        if resume_session_id:
            args.extend(["--resume", resume_session_id])
        else:
            args.append(prompt)

        return args

    async def spawn(
        self,
        prompt: str,
        resume_session_id: Optional[str] = None
    ) -> bool:
        """
        Spawn the containerized claude session.

        Args:
            prompt: Initial prompt/assignment.
            resume_session_id: Optional session ID to resume.

        Returns:
            True if spawn succeeded.
        """
        if self._running:
            logger.warning(f"Container {self.agent_id} already running")
            return False

        # Check Docker availability
        available, msg = check_docker_available()
        if not available:
            logger.error(msg)
            return False

        # Check image exists
        if not check_image_exists(self.config.image):
            logger.warning(f"Image {self.config.image} not found locally, attempting pull...")
            success, msg = pull_image(self.config.image)
            if not success:
                logger.error(msg)
                return False

        # Build claude args
        claude_args = self._build_claude_args(prompt, resume_session_id)

        # Build docker command
        docker_cmd = build_docker_command(
            self.agent_id,
            self.config,
            self.worktree_path,
            self.mcp_config_path,
            claude_args
        )

        logger.info(f"Spawning container {self._container_name}")
        logger.debug(f"Docker command: {' '.join(docker_cmd)}")

        try:
            self._process = await asyncio.create_subprocess_exec(
                *docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._running = True
            logger.info(f"Container {self._container_name} started")
            return True

        except Exception as e:
            logger.error(f"Failed to spawn container {self.agent_id}: {e}")
            return False

    async def stop(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Stop the container gracefully.

        Args:
            timeout: Seconds to wait before force killing.

        Returns:
            True if stopped successfully.
        """
        if not self._running:
            return True

        logger.info(f"Stopping container {self._container_name}...")

        try:
            # Use docker stop which sends SIGTERM then SIGKILL
            result = await asyncio.create_subprocess_exec(
                "docker", "stop", "-t", str(int(timeout)), self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()

            self._running = False

            # Wait for our process handle if we have one
            if self._process:
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass

            return True

        except Exception as e:
            logger.error(f"Error stopping container {self._container_name}: {e}")
            return False

    async def kill(self) -> bool:
        """
        Force kill the container.

        Returns:
            True if killed successfully.
        """
        logger.warning(f"Force killing container {self._container_name}")

        try:
            result = await asyncio.create_subprocess_exec(
                "docker", "kill", self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await result.wait()
            self._running = False
            return True

        except Exception as e:
            logger.error(f"Error killing container {self._container_name}: {e}")
            return False

    async def wait(self) -> int:
        """
        Wait for the container to exit.

        Returns:
            Exit code of the container.
        """
        if self._process:
            return await self._process.wait()
        return -1

    async def read_stdout(self) -> Optional[bytes]:
        """Read a line from container stdout."""
        if self._process and self._process.stdout:
            return await self._process.stdout.readline()
        return None

    async def read_stderr(self) -> Optional[bytes]:
        """Read a line from container stderr."""
        if self._process and self._process.stderr:
            return await self._process.stderr.readline()
        return None


class ContainerManager:
    """
    Manages Docker containers for sandboxed agents.

    Handles container lifecycle, image verification, and cleanup.
    """

    def __init__(self):
        """Initialize the container manager."""
        self._containers: dict[str, ContainerSession] = {}

    def check_prerequisites(self) -> tuple[bool, list[str]]:
        """
        Check all prerequisites for container support.

        Returns:
            Tuple of (all_ok, list of error messages).
        """
        errors = []

        # Check Docker
        available, msg = check_docker_available()
        if not available:
            errors.append(msg)

        return len(errors) == 0, errors

    async def spawn(
        self,
        agent_id: str,
        config: ContainerConfig,
        worktree_path: Path,
        mcp_config_path: Path,
        prompt: str,
        model: str = "claude-sonnet-4-6",
        skip_permissions: bool = False,
        resume_session_id: Optional[str] = None,
    ) -> Optional[ContainerSession]:
        """
        Spawn a containerized agent session.

        Args:
            agent_id: Agent identifier.
            config: Container configuration.
            worktree_path: Path to agent's worktree.
            mcp_config_path: Path to MCP config JSON.
            prompt: Initial prompt/assignment.
            model: Claude model to use.
            skip_permissions: Whether to use --dangerously-skip-permissions.
            resume_session_id: Optional session ID to resume.

        Returns:
            ContainerSession if successful, None otherwise.
        """
        if agent_id in self._containers:
            existing = self._containers[agent_id]
            if existing.is_running:
                logger.warning(f"Container {agent_id} already running")
                return existing

        session = ContainerSession(
            agent_id=agent_id,
            config=config,
            worktree_path=worktree_path,
            mcp_config_path=mcp_config_path,
            model=model,
            skip_permissions=skip_permissions,
        )

        if await session.spawn(prompt, resume_session_id):
            self._containers[agent_id] = session
            return session

        return None

    def get_session(self, agent_id: str) -> Optional[ContainerSession]:
        """Get a container session by agent ID."""
        return self._containers.get(agent_id)

    def list_sessions(self) -> list[ContainerSession]:
        """List all container sessions."""
        return list(self._containers.values())

    def list_running_sessions(self) -> list[ContainerSession]:
        """List all running container sessions."""
        return [s for s in self._containers.values() if s.is_running]

    async def stop(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Stop a specific container.

        Args:
            agent_id: Agent to stop.
            timeout: Seconds to wait before force killing.

        Returns:
            True if stopped successfully.
        """
        session = self._containers.get(agent_id)
        if not session:
            return False

        return await session.stop(timeout)

    async def stop_all(self, timeout: float = DEFAULT_TIMEOUT) -> int:
        """
        Stop all running containers.

        Args:
            timeout: Seconds to wait for each container.

        Returns:
            Number of containers stopped.
        """
        stopped = 0
        tasks = []

        for session in self._containers.values():
            if session.is_running:
                tasks.append(session.stop(timeout))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            stopped = sum(1 for r in results if r is True)

        return stopped

    def remove_session(self, agent_id: str) -> bool:
        """
        Remove a container session from tracking.

        Note: Does not stop the container. Use stop() first.

        Returns:
            True if session was removed.
        """
        if agent_id in self._containers:
            del self._containers[agent_id]
            return True
        return False

    async def cleanup_orphaned_containers(self) -> int:
        """
        Clean up any orphaned arch-* containers.

        Returns:
            Number of containers cleaned up.
        """
        try:
            # List all arch-* containers
            result = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a", "--filter", "name=arch-", "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await result.communicate()

            containers = stdout.decode().strip().split("\n")
            containers = [c for c in containers if c]  # Filter empty strings

            cleaned = 0
            for container in containers:
                # Stop and remove each container
                stop_result = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", container,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await stop_result.wait()
                if stop_result.returncode == 0:
                    cleaned += 1
                    logger.info(f"Cleaned up orphaned container: {container}")

            return cleaned

        except Exception as e:
            logger.error(f"Error cleaning up orphaned containers: {e}")
            return 0


# Default Dockerfile content for arch-agent image
DEFAULT_DOCKERFILE = """# ARCH Agent Container Image
# This image provides a sandboxed environment for running claude CLI agents.

FROM python:3.11-slim

# Install Node.js (required for claude CLI)
RUN apt-get update && apt-get install -y \\
    curl \\
    git \\
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \\
    && apt-get install -y nodejs \\
    && apt-get clean \\
    && rm -rf /var/lib/apt/lists/*

# Install anthropic SDK and claude CLI
RUN pip install --no-cache-dir anthropic
RUN npm install -g @anthropic-ai/claude-code

# Set up workspace
WORKDIR /workspace

# No default entrypoint - command is passed at runtime
ENTRYPOINT []
"""


def write_default_dockerfile(path: Path) -> Path:
    """
    Write the default Dockerfile for arch-agent image.

    Args:
        path: Directory to write Dockerfile to.

    Returns:
        Path to the written Dockerfile.
    """
    dockerfile_path = path / "Dockerfile"
    dockerfile_path.write_text(DEFAULT_DOCKERFILE)
    return dockerfile_path


async def build_default_image(
    dockerfile_dir: Path,
    tag: str = "arch-agent:latest"
) -> tuple[bool, str]:
    """
    Build the default arch-agent Docker image.

    Args:
        dockerfile_dir: Directory containing Dockerfile.
        tag: Image tag to use.

    Returns:
        Tuple of (success, message).
    """
    try:
        logger.info(f"Building Docker image {tag}...")

        result = await asyncio.create_subprocess_exec(
            "docker", "build", "-t", tag, str(dockerfile_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await result.communicate()

        if result.returncode == 0:
            return True, f"Built {tag} successfully"
        else:
            return False, f"Build failed: {stderr.decode()}"

    except Exception as e:
        return False, f"Build error: {e}"
