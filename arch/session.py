"""
ARCH Session Manager

Manages the lifecycle of individual claude CLI subprocesses.
Handles local spawning, stream-json output parsing, session persistence,
and unexpected exit handling.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Awaitable

from arch.state import StateStore
from arch.token_tracker import TokenTracker, StreamParser

logger = logging.getLogger(__name__)

# Default timeout for subprocess operations
DEFAULT_TIMEOUT = 30


@dataclass
class AgentConfig:
    """Configuration for an agent session."""
    agent_id: str
    role: str
    model: str = "claude-sonnet-4-6"
    worktree: str = ""
    sandboxed: bool = False
    skip_permissions: bool = False

    # Container settings (used by container.py)
    container_image: str = "arch-agent:latest"
    container_memory_limit: Optional[str] = None
    container_cpus: Optional[float] = None
    container_network: str = "bridge"
    container_extra_mounts: list[str] = field(default_factory=list)


def generate_mcp_config(
    agent_id: str,
    mcp_port: int,
    state_dir: Path,
    is_container: bool = False
) -> Path:
    """
    Generate MCP config JSON for an agent.

    Args:
        agent_id: Agent identifier.
        mcp_port: Port the MCP server is running on.
        state_dir: Directory to write config file to.
        is_container: Whether this is for a containerized agent.

    Returns:
        Path to the generated config file.
    """
    # Use host.docker.internal for containers, localhost for local
    host = "host.docker.internal" if is_container else "localhost"

    config = {
        "mcpServers": {
            "arch": {
                "type": "sse",
                "url": f"http://{host}:{mcp_port}/sse/{agent_id}"
            }
        }
    }

    config_path = state_dir / f"{agent_id}-mcp.json"
    config_path.write_text(json.dumps(config, indent=2))

    return config_path


def log_permissions_audit(
    state_dir: Path,
    agent_id: str,
    role: str,
    approved_by: str = "user"
) -> None:
    """
    Log usage of --dangerously-skip-permissions to audit log.

    Args:
        state_dir: State directory containing audit log.
        agent_id: Agent using skip_permissions.
        role: Agent's role.
        approved_by: Who approved the permission skip.
    """
    audit_path = state_dir / "permissions_audit.log"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = f"{timestamp}  SKIP_PERMISSIONS  agent_id={agent_id}  role={role}  approved_by={approved_by}\n"

    with open(audit_path, "a") as f:
        f.write(entry)

    logger.warning(f"SKIP_PERMISSIONS granted for {agent_id} (role={role})")


class Session:
    """
    Manages a single claude CLI subprocess.

    Handles spawning, output parsing, token tracking, and lifecycle management.
    """

    def __init__(
        self,
        config: AgentConfig,
        state: StateStore,
        token_tracker: TokenTracker,
        state_dir: Path,
        mcp_port: int = 3999,
        on_output: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
        on_exit: Optional[Callable[[str, int], Awaitable[None]]] = None,
    ):
        """
        Initialize a session.

        Args:
            config: Agent configuration.
            state: StateStore for persistence.
            token_tracker: TokenTracker for usage tracking.
            state_dir: Directory for state files.
            mcp_port: Port the MCP server is running on.
            on_output: Callback for parsed output events (agent_id, event).
            on_exit: Callback when process exits (agent_id, exit_code).
        """
        self.config = config
        self.state = state
        self.token_tracker = token_tracker
        self.state_dir = Path(state_dir)
        self.mcp_port = mcp_port
        self.on_output = on_output
        self.on_exit = on_exit

        self._process: Optional[asyncio.subprocess.Process] = None
        self._stream_parser: Optional[StreamParser] = None
        self._session_id: Optional[str] = None
        self._running = False
        self._output_task: Optional[asyncio.Task] = None

    @property
    def agent_id(self) -> str:
        """Get the agent ID."""
        return self.config.agent_id

    @property
    def is_running(self) -> bool:
        """Check if the session is running."""
        return self._running and self._process is not None

    @property
    def session_id(self) -> Optional[str]:
        """Get the claude session ID (for resume)."""
        return self._session_id

    @property
    def pid(self) -> Optional[int]:
        """Get the process ID."""
        return self._process.pid if self._process else None

    async def spawn(
        self,
        prompt: str,
        resume_session_id: Optional[str] = None
    ) -> bool:
        """
        Spawn the claude CLI subprocess.

        Args:
            prompt: Initial prompt/assignment for the agent.
            resume_session_id: Optional session ID to resume.

        Returns:
            True if spawn succeeded, False otherwise.
        """
        if self._running:
            logger.warning(f"Session {self.agent_id} already running")
            return False

        # Generate MCP config
        mcp_config_path = generate_mcp_config(
            self.config.agent_id,
            self.mcp_port,
            self.state_dir,
            is_container=False
        )

        # Build command
        cmd = [
            "claude",
            "--model", self.config.model,
            "--output-format", "stream-json",
            "--mcp-config", str(mcp_config_path),
            "--print",
        ]

        # Add skip permissions if configured
        if self.config.skip_permissions:
            cmd.append("--dangerously-skip-permissions")
            log_permissions_audit(
                self.state_dir,
                self.config.agent_id,
                self.config.role
            )

        # Resume existing session or start new
        if resume_session_id:
            cmd.extend(["--resume", resume_session_id])
        else:
            cmd.append(prompt)

        logger.info(f"Spawning session {self.agent_id}: {' '.join(cmd[:5])}...")

        try:
            # Spawn subprocess
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.config.worktree if self.config.worktree else None,
            )

            self._running = True

            # Register with token tracker
            self.token_tracker.register_agent(self.config.agent_id, self.config.model)

            # Create stream parser
            self._stream_parser = StreamParser(self.config.agent_id, self.token_tracker)

            # Start output processing task
            self._output_task = asyncio.create_task(self._process_output())

            # Update state
            self.state.update_agent(
                self.config.agent_id,
                status="working",
                pid=self._process.pid
            )

            logger.info(f"Session {self.agent_id} spawned with PID {self._process.pid}")
            return True

        except FileNotFoundError:
            logger.error("claude CLI not found. Ensure it's installed and in PATH.")
            return False
        except Exception as e:
            logger.error(f"Failed to spawn session {self.agent_id}: {e}")
            return False

    async def _process_output(self) -> None:
        """Process stdout from the subprocess line by line."""
        if not self._process or not self._process.stdout:
            return

        try:
            while True:
                line = await self._process.stdout.readline()
                if not line:
                    break

                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                # Parse the line
                event = self._stream_parser.parse_line(line_str)

                if event:
                    # Check for session_id in result event
                    if event.get("type") == "result":
                        self._session_id = event.get("session_id")
                        if self._session_id:
                            self.state.update_agent(
                                self.config.agent_id,
                                session_id=self._session_id
                            )

                    # Notify callback
                    if self.on_output:
                        try:
                            await self.on_output(self.config.agent_id, event)
                        except Exception as e:
                            logger.error(f"Output callback error: {e}")

        except asyncio.CancelledError:
            logger.debug(f"Output processing cancelled for {self.agent_id}")
        except Exception as e:
            logger.error(f"Error processing output for {self.agent_id}: {e}")

        # Wait for process to finish
        if self._process:
            exit_code = await self._process.wait()
            await self._handle_exit(exit_code)

    async def _handle_exit(self, exit_code: int) -> None:
        """Handle subprocess exit."""
        self._running = False
        logger.info(f"Session {self.agent_id} exited with code {exit_code}")

        # Persist session_id if we got one
        if self._session_id:
            self.state.update_agent(
                self.config.agent_id,
                session_id=self._session_id
            )

        if exit_code != 0:
            # Unexpected exit - set error status and notify Archie
            self.state.update_agent(self.config.agent_id, status="error")
            self.state.add_message(
                "harness",
                "archie",
                f"Agent {self.config.agent_id} exited unexpectedly with code {exit_code}. "
                f"Check state/agents.json for details."
            )
            logger.error(f"Session {self.agent_id} exited with non-zero code: {exit_code}")
        else:
            # Normal exit
            self.state.update_agent(self.config.agent_id, status="done")

        # Notify callback
        if self.on_exit:
            try:
                await self.on_exit(self.config.agent_id, exit_code)
            except Exception as e:
                logger.error(f"Exit callback error: {e}")

    async def stop(self, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Stop the subprocess gracefully.

        Args:
            timeout: Seconds to wait before force killing.

        Returns:
            True if stopped successfully.
        """
        if not self._process or not self._running:
            return True

        logger.info(f"Stopping session {self.agent_id}...")

        try:
            # Try graceful termination first
            self._process.terminate()

            try:
                await asyncio.wait_for(self._process.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                # Force kill if graceful termination times out
                logger.warning(f"Session {self.agent_id} did not terminate gracefully, killing...")
                self._process.kill()
                await self._process.wait()

            self._running = False

            # Cancel output task
            if self._output_task and not self._output_task.done():
                self._output_task.cancel()
                try:
                    await self._output_task
                except asyncio.CancelledError:
                    pass

            return True

        except Exception as e:
            logger.error(f"Error stopping session {self.agent_id}: {e}")
            return False

    async def send_signal(self, signal: int) -> bool:
        """
        Send a signal to the subprocess.

        Args:
            signal: Signal number to send.

        Returns:
            True if signal was sent.
        """
        if not self._process or not self._running:
            return False

        try:
            self._process.send_signal(signal)
            return True
        except Exception as e:
            logger.error(f"Error sending signal to {self.agent_id}: {e}")
            return False


class SessionManager:
    """
    Manages multiple agent sessions.

    Provides a higher-level interface for spawning, tracking, and
    tearing down agent sessions.
    """

    def __init__(
        self,
        state: StateStore,
        token_tracker: TokenTracker,
        state_dir: Path,
        mcp_port: int = 3999,
        on_output: Optional[Callable[[str, dict[str, Any]], Awaitable[None]]] = None,
        on_exit: Optional[Callable[[str, int], Awaitable[None]]] = None,
    ):
        """
        Initialize the session manager.

        Args:
            state: StateStore for persistence.
            token_tracker: TokenTracker for usage tracking.
            state_dir: Directory for state files.
            mcp_port: Port the MCP server is running on.
            on_output: Callback for parsed output events.
            on_exit: Callback when process exits.
        """
        self.state = state
        self.token_tracker = token_tracker
        self.state_dir = Path(state_dir)
        self.mcp_port = mcp_port
        self.on_output = on_output
        self.on_exit = on_exit

        self._sessions: dict[str, Session] = {}

    async def spawn(
        self,
        config: AgentConfig,
        prompt: str,
        resume_session_id: Optional[str] = None
    ) -> Optional[Session]:
        """
        Spawn a new agent session.

        Args:
            config: Agent configuration.
            prompt: Initial prompt/assignment.
            resume_session_id: Optional session ID to resume.

        Returns:
            Session if spawn succeeded, None otherwise.
        """
        if config.agent_id in self._sessions:
            existing = self._sessions[config.agent_id]
            if existing.is_running:
                logger.warning(f"Session {config.agent_id} already running")
                return existing

        session = Session(
            config=config,
            state=self.state,
            token_tracker=self.token_tracker,
            state_dir=self.state_dir,
            mcp_port=self.mcp_port,
            on_output=self.on_output,
            on_exit=self._wrap_exit_callback(config.agent_id),
        )

        if await session.spawn(prompt, resume_session_id):
            self._sessions[config.agent_id] = session
            return session

        return None

    def _wrap_exit_callback(self, agent_id: str) -> Callable[[str, int], Awaitable[None]]:
        """Wrap exit callback to clean up session tracking."""
        async def wrapper(aid: str, exit_code: int) -> None:
            # Call user callback first
            if self.on_exit:
                await self.on_exit(aid, exit_code)

        return wrapper

    def get_session(self, agent_id: str) -> Optional[Session]:
        """Get a session by agent ID."""
        return self._sessions.get(agent_id)

    def list_sessions(self) -> list[Session]:
        """List all sessions."""
        return list(self._sessions.values())

    def list_running_sessions(self) -> list[Session]:
        """List all running sessions."""
        return [s for s in self._sessions.values() if s.is_running]

    async def stop(self, agent_id: str, timeout: float = DEFAULT_TIMEOUT) -> bool:
        """
        Stop a specific session.

        Args:
            agent_id: Agent to stop.
            timeout: Seconds to wait before force killing.

        Returns:
            True if stopped successfully.
        """
        session = self._sessions.get(agent_id)
        if not session:
            return False

        return await session.stop(timeout)

    async def stop_all(self, timeout: float = DEFAULT_TIMEOUT) -> int:
        """
        Stop all running sessions.

        Args:
            timeout: Seconds to wait for each session.

        Returns:
            Number of sessions stopped.
        """
        stopped = 0
        tasks = []

        for session in self._sessions.values():
            if session.is_running:
                tasks.append(session.stop(timeout))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            stopped = sum(1 for r in results if r is True)

        return stopped

    def remove_session(self, agent_id: str) -> bool:
        """
        Remove a session from tracking.

        Note: Does not stop the session. Use stop() first.

        Returns:
            True if session was removed.
        """
        if agent_id in self._sessions:
            del self._sessions[agent_id]
            return True
        return False
