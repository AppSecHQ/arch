"""
ARCH Orchestrator

Responsible for the full lifecycle of the ARCH system:
- Parse and validate arch.yaml
- Initialize all components (state, worktree, MCP server, sessions)
- Startup/shutdown sequences
- Signal handlers for graceful cleanup
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from arch.container import check_docker_available, check_image_exists, pull_image
from arch.mcp_server import MCPServer
from arch.session import AgentConfig, SessionManager, AnySession
from arch.state import StateStore
from arch.token_tracker import TokenTracker
from arch.worktree import WorktreeManager

logger = logging.getLogger(__name__)

# Default configuration values
DEFAULT_MCP_PORT = 3999
DEFAULT_STATE_DIR = "./state"
DEFAULT_MAX_CONCURRENT_AGENTS = 5
DEFAULT_ARCHIE_MODEL = "claude-opus-4-5"
DEFAULT_AGENT_MODEL = "claude-sonnet-4-6"
DEFAULT_CONTAINER_IMAGE = "arch-agent:latest"
DEFAULT_ARCHIE_PERSONA = "personas/archie.md"
DEFAULT_SHUTDOWN_TIMEOUT = 30


# ============================================================================
# Configuration Dataclasses
# ============================================================================


@dataclass
class ProjectConfig:
    """Project configuration from arch.yaml."""
    name: str
    description: str = ""
    repo: str = "."


@dataclass
class ArchieConfig:
    """Archie (lead agent) configuration."""
    persona: str = DEFAULT_ARCHIE_PERSONA
    model: str = DEFAULT_ARCHIE_MODEL


@dataclass
class SandboxConfig:
    """Container sandbox settings for an agent."""
    enabled: bool = False
    image: str = DEFAULT_CONTAINER_IMAGE
    extra_mounts: list[str] = field(default_factory=list)
    network: str = "bridge"
    memory_limit: Optional[str] = None
    cpus: Optional[float] = None


@dataclass
class PermissionsConfig:
    """Permission settings for an agent."""
    skip_permissions: bool = False


@dataclass
class AgentPoolEntry:
    """Configuration for an agent type in the pool."""
    id: str
    persona: str
    model: str = DEFAULT_AGENT_MODEL
    max_instances: int = 1
    sandbox: SandboxConfig = field(default_factory=SandboxConfig)
    permissions: PermissionsConfig = field(default_factory=PermissionsConfig)


@dataclass
class GitHubLabel:
    """GitHub label configuration."""
    name: str
    color: str


@dataclass
class GitHubConfig:
    """GitHub integration configuration."""
    repo: str
    default_branch: str = "main"
    labels: list[GitHubLabel] = field(default_factory=list)
    issue_template: Optional[str] = None


@dataclass
class SettingsConfig:
    """General settings."""
    max_concurrent_agents: int = DEFAULT_MAX_CONCURRENT_AGENTS
    state_dir: str = DEFAULT_STATE_DIR
    mcp_port: int = DEFAULT_MCP_PORT
    token_budget_usd: Optional[float] = None
    auto_merge: bool = False
    require_user_approval: list[str] = field(default_factory=list)


@dataclass
class ArchConfig:
    """Complete ARCH configuration from arch.yaml."""
    project: ProjectConfig
    archie: ArchieConfig = field(default_factory=ArchieConfig)
    agent_pool: list[AgentPoolEntry] = field(default_factory=list)
    github: Optional[GitHubConfig] = None
    settings: SettingsConfig = field(default_factory=SettingsConfig)


def parse_config(config_path: Path) -> ArchConfig:
    """
    Parse arch.yaml into typed configuration.

    Args:
        config_path: Path to arch.yaml file.

    Returns:
        Parsed ArchConfig.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config is invalid.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    if not raw:
        raise ValueError("Config file is empty")

    if "project" not in raw:
        raise ValueError("Config must have 'project' section")

    if "name" not in raw["project"]:
        raise ValueError("Config project.name is required")

    # Parse project
    project = ProjectConfig(
        name=raw["project"]["name"],
        description=raw["project"].get("description", ""),
        repo=raw["project"].get("repo", "."),
    )

    # Parse archie
    archie_raw = raw.get("archie", {})
    archie = ArchieConfig(
        persona=archie_raw.get("persona", DEFAULT_ARCHIE_PERSONA),
        model=archie_raw.get("model", DEFAULT_ARCHIE_MODEL),
    )

    # Parse agent_pool
    agent_pool = []
    for entry in raw.get("agent_pool", []):
        if "id" not in entry:
            raise ValueError("Each agent_pool entry must have 'id'")
        if "persona" not in entry:
            raise ValueError(f"Agent {entry['id']} must have 'persona'")

        sandbox_raw = entry.get("sandbox", {})
        sandbox = SandboxConfig(
            enabled=sandbox_raw.get("enabled", False),
            image=sandbox_raw.get("image", DEFAULT_CONTAINER_IMAGE),
            extra_mounts=sandbox_raw.get("extra_mounts", []),
            network=sandbox_raw.get("network", "bridge"),
            memory_limit=sandbox_raw.get("memory_limit"),
            cpus=sandbox_raw.get("cpus"),
        )

        perms_raw = entry.get("permissions", {})
        permissions = PermissionsConfig(
            skip_permissions=perms_raw.get("skip_permissions", False),
        )

        agent_pool.append(AgentPoolEntry(
            id=entry["id"],
            persona=entry["persona"],
            model=entry.get("model", DEFAULT_AGENT_MODEL),
            max_instances=entry.get("max_instances", 1),
            sandbox=sandbox,
            permissions=permissions,
        ))

    # Parse github
    github = None
    if "github" in raw and raw["github"]:
        gh_raw = raw["github"]
        if "repo" not in gh_raw:
            raise ValueError("github.repo is required if github section is present")

        labels = []
        for label in gh_raw.get("labels", []):
            labels.append(GitHubLabel(
                name=label["name"],
                color=label.get("color", "000000"),
            ))

        github = GitHubConfig(
            repo=gh_raw["repo"],
            default_branch=gh_raw.get("default_branch", "main"),
            labels=labels,
            issue_template=gh_raw.get("issue_template"),
        )

    # Parse settings
    settings_raw = raw.get("settings", {})
    settings = SettingsConfig(
        max_concurrent_agents=settings_raw.get("max_concurrent_agents", DEFAULT_MAX_CONCURRENT_AGENTS),
        state_dir=settings_raw.get("state_dir", DEFAULT_STATE_DIR),
        mcp_port=settings_raw.get("mcp_port", DEFAULT_MCP_PORT),
        token_budget_usd=settings_raw.get("token_budget_usd"),
        auto_merge=settings_raw.get("auto_merge", False),
        require_user_approval=settings_raw.get("require_user_approval", []),
    )

    return ArchConfig(
        project=project,
        archie=archie,
        agent_pool=agent_pool,
        github=github,
        settings=settings,
    )


# ============================================================================
# Gate Checks
# ============================================================================


def check_permission_gate(config: ArchConfig) -> list[str]:
    """
    Check if any agents require skip_permissions.

    Returns:
        List of agent IDs that have skip_permissions=True.
    """
    return [
        agent.id for agent in config.agent_pool
        if agent.permissions.skip_permissions
    ]


def check_container_gate(config: ArchConfig) -> tuple[bool, list[str], list[str]]:
    """
    Check if Docker is available and required images exist.

    Returns:
        Tuple of (docker_available, agents_needing_containers, missing_images).
    """
    sandboxed_agents = [
        agent for agent in config.agent_pool
        if agent.sandbox.enabled
    ]

    if not sandboxed_agents:
        return True, [], []

    # Check Docker availability
    available, msg = check_docker_available()
    if not available:
        return False, [a.id for a in sandboxed_agents], []

    # Check images
    images_needed = set(a.sandbox.image for a in sandboxed_agents)
    missing_images = [img for img in images_needed if not check_image_exists(img)]

    return True, [a.id for a in sandboxed_agents], missing_images


def check_github_gate(config: ArchConfig) -> tuple[bool, str]:
    """
    Check GitHub CLI availability and authentication.

    Returns:
        Tuple of (available, message).
    """
    if not config.github:
        return True, "GitHub integration not configured"

    try:
        # Check gh is installed
        result = subprocess.run(
            ["gh", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False, "gh CLI not found"

        # Check auth status
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False, f"gh not authenticated: {result.stderr}"

        # Check repo access
        result = subprocess.run(
            ["gh", "repo", "view", config.github.repo],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return False, f"Cannot access repo {config.github.repo}: {result.stderr}"

        return True, f"GitHub access verified for {config.github.repo}"

    except FileNotFoundError:
        return False, "gh CLI not installed"
    except subprocess.TimeoutExpired:
        return False, "gh command timed out"
    except Exception as e:
        return False, f"GitHub check failed: {e}"


# ============================================================================
# Orchestrator
# ============================================================================


class Orchestrator:
    """
    Main orchestrator for the ARCH system.

    Manages the complete lifecycle: startup, running, and shutdown.
    """

    def __init__(
        self,
        config_path: Path,
        keep_worktrees: bool = False,
    ):
        """
        Initialize the orchestrator.

        Args:
            config_path: Path to arch.yaml.
            keep_worktrees: If True, don't remove worktrees on shutdown.
        """
        self.config_path = Path(config_path)
        self.keep_worktrees = keep_worktrees

        # Will be initialized during startup
        self.config: Optional[ArchConfig] = None
        self.state: Optional[StateStore] = None
        self.token_tracker: Optional[TokenTracker] = None
        self.worktree_manager: Optional[WorktreeManager] = None
        self.session_manager: Optional[SessionManager] = None
        self.mcp_server: Optional[MCPServer] = None

        # Runtime state
        self._running = False
        self._shutdown_requested = False
        self._archie_session: Optional[AnySession] = None
        self._archie_restart_count = 0
        self._github_enabled = False

        # Signal handling
        self._original_sigint = None
        self._original_sigterm = None

    @property
    def state_dir(self) -> Path:
        """Get the state directory path."""
        if self.config:
            return Path(self.config.settings.state_dir)
        return Path(DEFAULT_STATE_DIR)

    @property
    def repo_path(self) -> Path:
        """Get the repository path."""
        if self.config:
            return Path(self.config.project.repo).resolve()
        return Path(".").resolve()

    async def startup(self) -> bool:
        """
        Execute the full startup sequence.

        Returns:
            True if startup succeeded, False otherwise.
        """
        logger.info("Starting ARCH...")

        try:
            # Step 1: Parse and validate config
            logger.info("Step 1: Parsing arch.yaml...")
            self.config = parse_config(self.config_path)
            logger.info(f"Project: {self.config.project.name}")

            # Step 2: Initialize state store
            logger.info("Step 2: Initializing state store...")
            state_dir = Path(self.config.settings.state_dir)
            state_dir.mkdir(parents=True, exist_ok=True)
            self.state = StateStore(state_dir)
            self.state.init_project(
                name=self.config.project.name,
                description=self.config.project.description,
                repo=str(self.repo_path),
            )

            # Initialize token tracker
            self.token_tracker = TokenTracker(state_dir=state_dir)

            # Step 3: Verify git repo
            logger.info("Step 3: Verifying git repository...")
            if not self._verify_git_repo():
                return False

            # Initialize worktree manager
            self.worktree_manager = WorktreeManager(self.repo_path)

            # Step 4: Permission gate
            logger.info("Step 4: Checking permission requirements...")
            if not await self._permission_gate():
                return False

            # Step 5: Container gate
            logger.info("Step 5: Checking container requirements...")
            if not await self._container_gate():
                return False

            # Step 6: GitHub gate
            logger.info("Step 6: Checking GitHub access...")
            await self._github_gate()

            # Step 7: Start MCP server
            logger.info("Step 7: Starting MCP server...")
            await self._start_mcp_server()

            # Initialize session manager
            self.session_manager = SessionManager(
                state=self.state,
                token_tracker=self.token_tracker,
                state_dir=state_dir,
                mcp_port=self.config.settings.mcp_port,
                on_exit=self._on_agent_exit,
            )

            # Step 8: Create Archie's worktree
            logger.info("Step 8: Creating Archie's worktree...")
            await self._create_archie_worktree()

            # Step 9: Spawn Archie
            logger.info("Step 9: Spawning Archie...")
            if not await self._spawn_archie():
                return False

            # Step 10: Dashboard will be started separately (Step 9 implementation)
            logger.info("Step 10: Dashboard ready (implementation pending)")

            # Register signal handlers
            self._register_signal_handlers()

            self._running = True
            logger.info("ARCH startup complete!")
            return True

        except Exception as e:
            logger.error(f"Startup failed: {e}")
            await self.shutdown()
            return False

    async def shutdown(self, keep_worktrees: Optional[bool] = None) -> None:
        """
        Execute the full shutdown sequence.

        Args:
            keep_worktrees: Override the keep_worktrees setting.
        """
        if keep_worktrees is None:
            keep_worktrees = self.keep_worktrees

        logger.info("Shutting down ARCH...")
        self._shutdown_requested = True

        try:
            # Step 1: Stop all agent sessions
            if self.session_manager:
                logger.info("Stopping all agent sessions...")
                stopped = await self.session_manager.stop_all(timeout=DEFAULT_SHUTDOWN_TIMEOUT)
                logger.info(f"Stopped {stopped} sessions")

            # Step 2: Stop MCP server
            if self.mcp_server:
                logger.info("Stopping MCP server...")
                await self.mcp_server.stop()

            # Step 3: Remove worktrees
            if self.worktree_manager and not keep_worktrees:
                logger.info("Removing worktrees...")
                removed = self.worktree_manager.cleanup_all()
                logger.info(f"Removed {removed} worktrees")

            # Step 4: Final state is auto-persisted by StateStore
            # (StateStore auto-saves on every mutation)
            logger.info("State persisted")

            # Step 5: Print cost summary
            self._print_cost_summary()

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

        finally:
            self._running = False
            self._restore_signal_handlers()

        logger.info("ARCH shutdown complete")

    async def run(self) -> None:
        """
        Main run loop. Blocks until shutdown is requested.
        """
        logger.info("ARCH is running. Press Ctrl+C to stop.")

        while self._running and not self._shutdown_requested:
            await asyncio.sleep(1)

            # Check if Archie needs restart
            if self._archie_session and not self._archie_session.is_running:
                await self._handle_archie_exit()

    def _verify_git_repo(self) -> bool:
        """Verify the git repository is accessible."""
        try:
            result = subprocess.run(
                ["git", "status"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                logger.error(f"Not a git repository: {self.repo_path}")
                return False
            return True
        except FileNotFoundError:
            logger.error("git not found")
            return False
        except subprocess.TimeoutExpired:
            logger.error("git status timed out")
            return False

    async def _permission_gate(self) -> bool:
        """
        Check and confirm skip_permissions usage.

        Returns:
            True if approved or no agents need it, False otherwise.
        """
        agents_needing_perms = check_permission_gate(self.config)

        if not agents_needing_perms:
            logger.info("No agents require skip_permissions")
            return True

        # Print prominent warning
        print("\n" + "=" * 60)
        print("⚠️  WARNING: DANGEROUS PERMISSIONS REQUESTED")
        print("=" * 60)
        print("\nThe following agent roles have skip_permissions enabled:")
        for agent_id in agents_needing_perms:
            print(f"  • {agent_id}")
        print("\nThis allows these agents to execute commands without")
        print("confirmation, which could be dangerous.")
        print("\nDo you want to continue? [y/N]: ", end="", flush=True)

        # Get user confirmation
        try:
            response = input().strip().lower()
        except EOFError:
            response = ""

        if response != "y":
            logger.info("User declined skip_permissions")
            return False

        # Log acknowledgment
        self._log_permission_acknowledgment(agents_needing_perms)
        logger.info("skip_permissions approved by user")
        return True

    def _log_permission_acknowledgment(self, agent_ids: list[str]) -> None:
        """Log permission acknowledgment to state directory."""
        audit_path = self.state_dir / "permissions_audit.log"
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        with open(audit_path, "a") as f:
            for agent_id in agent_ids:
                f.write(f"{timestamp}  STARTUP_APPROVAL  agent_id={agent_id}  approved_by=user\n")

    async def _container_gate(self) -> bool:
        """
        Check Docker availability and images.

        Returns:
            True if containers are ready or not needed, False otherwise.
        """
        docker_ok, sandboxed_agents, missing_images = check_container_gate(self.config)

        if not sandboxed_agents:
            logger.info("No agents require containers")
            return True

        if not docker_ok:
            logger.error("Docker is required but not available")
            print("\n❌ Docker daemon is not running.")
            print("Start Docker and try again.")
            return False

        logger.info(f"Containerized agents: {', '.join(sandboxed_agents)}")

        # Pull missing images
        for image in missing_images:
            logger.info(f"Pulling image: {image}")
            success, msg = pull_image(image)
            if not success:
                logger.error(f"Failed to pull {image}: {msg}")
                print(f"\n❌ Failed to pull Docker image: {image}")
                print(f"   {msg}")
                print("\nBuild or pull the image manually and try again.")
                return False

        return True

    async def _github_gate(self) -> None:
        """Check GitHub availability (warn only, don't fail)."""
        available, msg = check_github_gate(self.config)

        if available:
            logger.info(msg)
            self._github_enabled = True
        else:
            logger.warning(f"GitHub integration disabled: {msg}")
            self._github_enabled = False
            if self.config.github:
                print(f"\n⚠️  GitHub integration disabled: {msg}")
                print("GitHub tools will not be available for this session.")

    async def _start_mcp_server(self) -> None:
        """Start the MCP server."""
        self.mcp_server = MCPServer(
            state=self.state,
            port=self.config.settings.mcp_port,
            github_repo=self.config.github.repo if self.config.github and self._github_enabled else None,
        )
        await self.mcp_server.start()
        logger.info(f"MCP server listening on port {self.config.settings.mcp_port}")

    async def _create_archie_worktree(self) -> None:
        """Create Archie's worktree and write CLAUDE.md."""
        # Create worktree
        worktree_path = self.worktree_manager.create("archie")

        # Read persona file
        persona_path = self.repo_path / self.config.archie.persona
        if persona_path.exists():
            persona_content = persona_path.read_text()
        else:
            logger.warning(f"Archie persona not found at {persona_path}, using default")
            persona_content = "# Archie - Lead Agent\n\nYou are Archie, the lead agent."

        # Write CLAUDE.md with injected context
        self.worktree_manager.write_claude_md(
            agent_id="archie",
            persona_content=persona_content,
            assignment=f"Lead the {self.config.project.name} project",
            project_name=self.config.project.name,
            project_description=self.config.project.description,
            active_agents={},  # No other agents yet
            available_tools=[
                "send_message", "get_messages", "update_status", "report_completion",
                "spawn_agent", "teardown_agent", "list_agents", "escalate_to_user",
                "request_merge", "get_project_context", "close_project", "update_brief",
            ] + (["gh_create_issue", "gh_list_issues", "gh_close_issue", "gh_update_issue",
                  "gh_add_comment", "gh_create_milestone", "gh_list_milestones"]
                 if self._github_enabled else []),
        )

        logger.info(f"Archie worktree created at {worktree_path}")

    async def _spawn_archie(self) -> bool:
        """Spawn the Archie session."""
        worktree_path = self.worktree_manager.get_worktree_path("archie")
        if not worktree_path:
            logger.error("Archie worktree not found")
            return False

        # Register Archie in state
        self.state.register_agent(
            agent_id="archie",
            role="lead",
            worktree=str(worktree_path),
            sandboxed=False,
            skip_permissions=False,
        )

        # Create agent config (Archie never runs in container)
        config = AgentConfig(
            agent_id="archie",
            role="lead",
            model=self.config.archie.model,
            worktree=str(worktree_path),
            sandboxed=False,
            skip_permissions=False,
        )

        # Build initial prompt
        prompt = self._build_archie_prompt()

        # Spawn session
        self._archie_session = await self.session_manager.spawn(config, prompt)

        if not self._archie_session:
            logger.error("Failed to spawn Archie session")
            return False

        logger.info("Archie is online")
        return True

    def _build_archie_prompt(self) -> str:
        """Build the initial prompt for Archie."""
        prompt_parts = [
            f"You are Archie, leading the {self.config.project.name} project.",
            f"\nProject description: {self.config.project.description}",
            "\nStart by calling get_project_context to understand the current state.",
            "Read BRIEF.md to understand the goals and current status.",
        ]

        if self._github_enabled:
            prompt_parts.append(
                "\nGitHub integration is enabled. Use gh_list_milestones and "
                "gh_list_issues to understand the sprint state."
            )

        prompt_parts.append(
            "\nWhen ready, spawn agents from the pool to work on tasks. "
            "Coordinate their work and merge completed branches."
        )

        return "\n".join(prompt_parts)

    async def _on_agent_exit(self, agent_id: str, exit_code: int) -> None:
        """Handle agent exit callback."""
        logger.info(f"Agent {agent_id} exited with code {exit_code}")

        if agent_id == "archie" and exit_code != 0:
            # Archie exited unexpectedly - will be handled by run loop
            pass

    async def _handle_archie_exit(self) -> None:
        """Handle unexpected Archie exit."""
        if self._shutdown_requested:
            return

        self._archie_restart_count += 1

        if self._archie_restart_count > 1:
            logger.error("Archie has exited unexpectedly multiple times")
            print("\n❌ Archie has crashed multiple times. Shutting down.")
            self._shutdown_requested = True
            return

        # Attempt restart with --resume
        logger.warning("Archie exited unexpectedly, attempting restart...")

        session_id = self._archie_session.session_id if self._archie_session else None

        if session_id:
            logger.info(f"Resuming Archie session: {session_id}")
            worktree_path = self.worktree_manager.get_worktree_path("archie")

            config = AgentConfig(
                agent_id="archie",
                role="lead",
                model=self.config.archie.model,
                worktree=str(worktree_path),
                sandboxed=False,
                skip_permissions=False,
            )

            self._archie_session = await self.session_manager.spawn(
                config,
                "Resume previous work",
                resume_session_id=session_id,
            )

            if self._archie_session:
                logger.info("Archie restarted successfully")
            else:
                logger.error("Failed to restart Archie")
                print("\n❌ Failed to restart Archie. Shutting down.")
                self._shutdown_requested = True
        else:
            logger.error("No session ID available for Archie restart")
            print("\n❌ Cannot restart Archie (no session ID). Shutting down.")
            self._shutdown_requested = True

    def _print_cost_summary(self) -> None:
        """Print cost summary to stdout."""
        if not self.token_tracker:
            return

        print("\n" + "=" * 40)
        print("COST SUMMARY")
        print("=" * 40)

        all_usage = self.token_tracker.get_all_usage()
        total_cost = 0.0

        for agent_id, usage in all_usage.items():
            cost = usage.get("cost_usd", 0.0)
            total_cost += cost
            print(f"{agent_id:20} ${cost:.4f}")

        print("-" * 40)
        print(f"{'Total':20} ${total_cost:.4f}")

        if self.config and self.config.settings.token_budget_usd:
            budget = self.config.settings.token_budget_usd
            pct = (total_cost / budget) * 100 if budget > 0 else 0
            print(f"{'Budget':20} ${budget:.2f} ({pct:.1f}% used)")

        print("=" * 40 + "\n")

    def _register_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown."""
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)
        atexit.register(self._atexit_handler)

    def _restore_signal_handlers(self) -> None:
        """Restore original signal handlers."""
        if self._original_sigint:
            signal.signal(signal.SIGINT, self._original_sigint)
        if self._original_sigterm:
            signal.signal(signal.SIGTERM, self._original_sigterm)

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """Handle SIGINT/SIGTERM."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, initiating shutdown...")
        self._shutdown_requested = True

    def _atexit_handler(self) -> None:
        """Handle atexit for emergency cleanup."""
        if self._running:
            logger.warning("Emergency cleanup on exit")
            # Synchronous cleanup
            if self.worktree_manager and not self.keep_worktrees:
                try:
                    self.worktree_manager.cleanup_all()
                except Exception as e:
                    logger.error(f"Worktree cleanup failed: {e}")


# ============================================================================
# Convenience Functions
# ============================================================================


async def run_arch(
    config_path: Path = Path("arch.yaml"),
    keep_worktrees: bool = False,
) -> int:
    """
    Run ARCH with the given configuration.

    Args:
        config_path: Path to arch.yaml.
        keep_worktrees: If True, don't remove worktrees on shutdown.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    orchestrator = Orchestrator(config_path, keep_worktrees)

    try:
        if not await orchestrator.startup():
            return 1

        await orchestrator.run()
        return 0

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        return 0

    finally:
        await orchestrator.shutdown()
