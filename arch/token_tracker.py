"""
ARCH Token Tracker

Parses stream-json output from claude CLI and accumulates per-agent token
usage and cost. Pricing is loaded from a configurable YAML file.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Optional

import yaml

logger = logging.getLogger(__name__)

# Default pricing per million tokens (as of 2026-02)
# Kept as fallback if pricing.yaml is not found
DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-opus-4-5": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4-6": {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00, "cache_read": 0.30, "cache_write": 3.75},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.00, "cache_read": 0.08, "cache_write": 1.00},
}

# Fallback model for unknown model IDs
FALLBACK_MODEL = "claude-sonnet-4-6"


def load_pricing(pricing_path: Optional[Path] = None) -> dict[str, dict[str, float]]:
    """
    Load pricing from YAML file.

    Args:
        pricing_path: Path to pricing.yaml. If None, uses default pricing.

    Returns:
        Pricing dict mapping model ID to rate dict.
    """
    if pricing_path is None or not pricing_path.exists():
        return DEFAULT_PRICING.copy()

    try:
        with open(pricing_path) as f:
            data = yaml.safe_load(f)

        if not isinstance(data, dict):
            logger.warning(f"Invalid pricing file format: {pricing_path}")
            return DEFAULT_PRICING.copy()

        return data

    except (yaml.YAMLError, IOError) as e:
        logger.warning(f"Failed to load pricing from {pricing_path}: {e}")
        return DEFAULT_PRICING.copy()


def calculate_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_creation_tokens: int,
    model: str,
    pricing: dict[str, dict[str, float]]
) -> float:
    """
    Calculate cost in USD for token usage.

    Args:
        input_tokens: Number of input tokens (non-cached).
        output_tokens: Number of output tokens.
        cache_read_tokens: Number of tokens read from cache.
        cache_creation_tokens: Number of tokens written to cache.
        model: Model ID (e.g., "claude-sonnet-4-6").
        pricing: Pricing dict from load_pricing().

    Returns:
        Cost in USD.
    """
    # Get model pricing, fall back to default model if unknown
    if model not in pricing:
        logger.warning(f"Unknown model '{model}', using {FALLBACK_MODEL} pricing")
        model = FALLBACK_MODEL

    rates = pricing.get(model, pricing.get(FALLBACK_MODEL, {}))

    if not rates:
        logger.error(f"No pricing available for model: {model}")
        return 0.0

    cost = (
        (input_tokens / 1_000_000) * rates.get("input", 0) +
        (output_tokens / 1_000_000) * rates.get("output", 0) +
        (cache_read_tokens / 1_000_000) * rates.get("cache_read", 0) +
        (cache_creation_tokens / 1_000_000) * rates.get("cache_write", 0)
    )

    return round(cost, 6)  # Round to avoid floating point noise


class AgentUsage:
    """Tracks token usage for a single agent."""

    def __init__(self, agent_id: str, model: str):
        """
        Initialize agent usage tracker.

        Args:
            agent_id: Unique agent identifier.
            model: Claude model ID being used.
        """
        self.agent_id = agent_id
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_creation_tokens = 0
        self.turns = 0
        self.cost_usd = 0.0

    def add_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        pricing: Optional[dict[str, dict[str, float]]] = None
    ) -> float:
        """
        Add usage from a single turn.

        Args:
            input_tokens: Input tokens for this turn.
            output_tokens: Output tokens for this turn.
            cache_read_tokens: Cache read tokens for this turn.
            cache_creation_tokens: Cache creation tokens for this turn.
            pricing: Pricing dict for cost calculation.

        Returns:
            Cost for this turn in USD.
        """
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_read_tokens += cache_read_tokens
        self.cache_creation_tokens += cache_creation_tokens
        self.turns += 1

        # Calculate turn cost
        if pricing is None:
            pricing = DEFAULT_PRICING

        turn_cost = calculate_cost(
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_creation_tokens,
            self.model,
            pricing
        )

        self.cost_usd += turn_cost
        self.cost_usd = round(self.cost_usd, 6)

        return turn_cost

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "agent_id": self.agent_id,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "turns": self.turns,
            "cost_usd": self.cost_usd
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentUsage":
        """Create from dictionary."""
        usage = cls(data["agent_id"], data["model"])
        usage.input_tokens = data.get("input_tokens", 0)
        usage.output_tokens = data.get("output_tokens", 0)
        usage.cache_read_tokens = data.get("cache_read_tokens", 0)
        usage.cache_creation_tokens = data.get("cache_creation_tokens", 0)
        usage.turns = data.get("turns", 0)
        usage.cost_usd = data.get("cost_usd", 0.0)
        return usage


class TokenTracker:
    """
    Tracks token usage across all agents.

    Parses stream-json output from claude CLI and accumulates usage.
    Persists to state/usage.json after every update.
    """

    def __init__(
        self,
        state_dir: Optional[Path] = None,
        pricing_path: Optional[Path] = None,
        on_usage_update: Optional[Callable[[str, dict[str, Any]], None]] = None
    ):
        """
        Initialize the token tracker.

        Args:
            state_dir: Directory for persisting usage.json.
            pricing_path: Path to pricing.yaml.
            on_usage_update: Callback called after each usage update.
                            Receives (agent_id, usage_dict).
        """
        self.state_dir = Path(state_dir) if state_dir else None
        self.pricing = load_pricing(pricing_path)
        self.on_usage_update = on_usage_update

        self._agents: dict[str, AgentUsage] = {}

        # Load existing usage if present
        if self.state_dir:
            self._load()

    def register_agent(self, agent_id: str, model: str) -> None:
        """
        Register a new agent for tracking.

        Args:
            agent_id: Unique agent identifier.
            model: Claude model ID.
        """
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentUsage(agent_id, model)
            self._persist()

    def parse_stream_event(self, agent_id: str, line: str) -> Optional[dict[str, Any]]:
        """
        Parse a single line of stream-json output.

        Args:
            agent_id: Agent that produced this output.
            line: Single line of stream-json output.

        Returns:
            Parsed event dict if valid JSON, None otherwise.
        """
        line = line.strip()
        if not line:
            return None

        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None

        # Handle usage events
        if event.get("type") == "usage":
            self._handle_usage_event(agent_id, event)

        # Handle result events (contains session_id)
        if event.get("type") == "result":
            # Return the event so caller can extract session_id
            pass

        return event

    def _handle_usage_event(self, agent_id: str, event: dict[str, Any]) -> None:
        """Process a usage event and update agent stats."""
        if agent_id not in self._agents:
            logger.warning(f"Usage event for unregistered agent: {agent_id}")
            return

        input_tokens = event.get("input_tokens", 0)
        output_tokens = event.get("output_tokens", 0)
        cache_read = event.get("cache_read_input_tokens", 0)
        cache_creation = event.get("cache_creation_input_tokens", 0)

        agent = self._agents[agent_id]
        agent.add_usage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
            pricing=self.pricing
        )

        self._persist()

        # Notify callback
        if self.on_usage_update:
            self.on_usage_update(agent_id, agent.to_dict())

    def get_agent_usage(self, agent_id: str) -> Optional[dict[str, Any]]:
        """
        Get usage for a specific agent.

        Returns:
            Usage dict or None if agent not found.
        """
        agent = self._agents.get(agent_id)
        return agent.to_dict() if agent else None

    def get_all_usage(self) -> dict[str, dict[str, Any]]:
        """
        Get usage for all agents.

        Returns:
            Dict mapping agent_id to usage dict.
        """
        return {aid: agent.to_dict() for aid, agent in self._agents.items()}

    def get_total_cost(self) -> float:
        """Get total cost across all agents."""
        return round(sum(a.cost_usd for a in self._agents.values()), 6)

    def get_total_tokens(self) -> dict[str, int]:
        """Get total token counts across all agents."""
        return {
            "input_tokens": sum(a.input_tokens for a in self._agents.values()),
            "output_tokens": sum(a.output_tokens for a in self._agents.values()),
            "cache_read_tokens": sum(a.cache_read_tokens for a in self._agents.values()),
            "cache_creation_tokens": sum(a.cache_creation_tokens for a in self._agents.values()),
            "total_turns": sum(a.turns for a in self._agents.values())
        }

    def remove_agent(self, agent_id: str) -> bool:
        """
        Remove an agent from tracking.

        Returns:
            True if agent was removed, False if not found.
        """
        if agent_id in self._agents:
            del self._agents[agent_id]
            self._persist()
            return True
        return False

    def _persist(self) -> None:
        """Persist usage to JSON file."""
        if self.state_dir is None:
            return

        self.state_dir.mkdir(parents=True, exist_ok=True)
        usage_file = self.state_dir / "usage.json"
        temp_file = usage_file.with_suffix(".tmp")

        data = {aid: agent.to_dict() for aid, agent in self._agents.items()}

        with open(temp_file, "w") as f:
            json.dump(data, f, indent=2)

        temp_file.replace(usage_file)

    def _load(self) -> None:
        """Load usage from JSON file."""
        if self.state_dir is None:
            return

        usage_file = self.state_dir / "usage.json"
        if not usage_file.exists():
            return

        try:
            with open(usage_file) as f:
                data = json.load(f)

            for agent_id, usage_data in data.items():
                self._agents[agent_id] = AgentUsage.from_dict(usage_data)

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load usage from {usage_file}: {e}")


class StreamParser:
    """
    Parses stream-json output from a claude CLI subprocess.

    Provides a line-by-line interface for processing output and
    extracting relevant events (usage, result, assistant messages).
    """

    def __init__(self, agent_id: str, tracker: TokenTracker):
        """
        Initialize the stream parser.

        Args:
            agent_id: Agent ID for this stream.
            tracker: TokenTracker instance to report usage to.
        """
        self.agent_id = agent_id
        self.tracker = tracker
        self.session_id: Optional[str] = None
        self.last_event: Optional[dict[str, Any]] = None

    def parse_line(self, line: str) -> Optional[dict[str, Any]]:
        """
        Parse a single line of stream output.

        Args:
            line: Raw line from stdout.

        Returns:
            Parsed event dict or None.
        """
        event = self.tracker.parse_stream_event(self.agent_id, line)

        if event:
            self.last_event = event

            # Extract session_id from result event
            if event.get("type") == "result":
                self.session_id = event.get("session_id")

        return event

    def get_session_id(self) -> Optional[str]:
        """Get the session ID if available (from result event)."""
        return self.session_id
