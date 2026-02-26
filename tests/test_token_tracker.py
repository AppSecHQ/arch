"""Unit tests for ARCH Token Tracker."""

import json
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from arch.token_tracker import (
    AgentUsage,
    StreamParser,
    TokenTracker,
    calculate_cost,
    load_pricing,
    DEFAULT_PRICING,
    FALLBACK_MODEL,
)


# --- Stream-JSON Fixtures ---

USAGE_EVENT = {
    "type": "usage",
    "input_tokens": 1500,
    "output_tokens": 500,
    "cache_read_input_tokens": 200,
    "cache_creation_input_tokens": 100
}

RESULT_EVENT = {
    "type": "result",
    "session_id": "abc123-def456",
    "is_error": False
}

ASSISTANT_EVENT = {
    "type": "assistant",
    "message": {
        "content": [{"type": "text", "text": "Hello!"}]
    }
}

# Multi-turn stream output fixture
STREAM_OUTPUT = """
{"type": "assistant", "message": {"content": [{"type": "text", "text": "Starting task..."}]}}
{"type": "usage", "input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 500}
{"type": "assistant", "message": {"content": [{"type": "text", "text": "Continuing..."}]}}
{"type": "usage", "input_tokens": 800, "output_tokens": 300, "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0}
{"type": "result", "session_id": "session-xyz-789"}
""".strip()


class TestLoadPricing:
    """Tests for pricing loading."""

    def test_load_default_pricing(self):
        """load_pricing returns defaults when no file provided."""
        pricing = load_pricing(None)
        assert "claude-sonnet-4-6" in pricing
        assert pricing["claude-sonnet-4-6"]["input"] == 3.00

    def test_load_pricing_from_file(self, tmp_path):
        """load_pricing loads from YAML file."""
        pricing_file = tmp_path / "pricing.yaml"
        pricing_file.write_text("""
claude-test-model:
  input: 5.00
  output: 25.00
  cache_read: 0.50
  cache_write: 6.25
""")

        pricing = load_pricing(pricing_file)
        assert "claude-test-model" in pricing
        assert pricing["claude-test-model"]["input"] == 5.00

    def test_load_pricing_missing_file(self, tmp_path):
        """load_pricing returns defaults for missing file."""
        pricing = load_pricing(tmp_path / "nonexistent.yaml")
        assert pricing == DEFAULT_PRICING

    def test_load_pricing_invalid_yaml(self, tmp_path):
        """load_pricing returns defaults for invalid YAML."""
        pricing_file = tmp_path / "bad.yaml"
        pricing_file.write_text("not: valid: yaml: {{}")

        pricing = load_pricing(pricing_file)
        assert pricing == DEFAULT_PRICING


class TestCalculateCost:
    """Tests for cost calculation."""

    def test_calculate_cost_sonnet(self):
        """calculate_cost computes correctly for Sonnet."""
        cost = calculate_cost(
            input_tokens=1_000_000,
            output_tokens=100_000,
            cache_read_tokens=500_000,
            cache_creation_tokens=200_000,
            model="claude-sonnet-4-6",
            pricing=DEFAULT_PRICING
        )

        # input: 1M * 3.00/M = 3.00
        # output: 100K * 15.00/M = 1.50
        # cache_read: 500K * 0.30/M = 0.15
        # cache_write: 200K * 3.75/M = 0.75
        # Total: 5.40
        assert cost == 5.4

    def test_calculate_cost_opus(self):
        """calculate_cost computes correctly for Opus."""
        cost = calculate_cost(
            input_tokens=100_000,
            output_tokens=50_000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-opus-4-5",
            pricing=DEFAULT_PRICING
        )

        # input: 100K * 15.00/M = 1.50
        # output: 50K * 75.00/M = 3.75
        # Total: 5.25
        assert cost == 5.25

    def test_calculate_cost_haiku(self):
        """calculate_cost computes correctly for Haiku."""
        cost = calculate_cost(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-haiku-4-5",
            pricing=DEFAULT_PRICING
        )

        # input: 1M * 0.80/M = 0.80
        # output: 1M * 4.00/M = 4.00
        # Total: 4.80
        assert cost == 4.8

    def test_calculate_cost_unknown_model_fallback(self):
        """calculate_cost uses fallback for unknown model."""
        cost = calculate_cost(
            input_tokens=1_000_000,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-unknown-99",
            pricing=DEFAULT_PRICING
        )

        # Falls back to sonnet: 1M * 3.00/M = 3.00
        assert cost == 3.0

    def test_calculate_cost_zero_tokens(self):
        """calculate_cost handles zero tokens."""
        cost = calculate_cost(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            model="claude-sonnet-4-6",
            pricing=DEFAULT_PRICING
        )
        assert cost == 0.0


class TestAgentUsage:
    """Tests for AgentUsage class."""

    def test_init(self):
        """AgentUsage initializes with zero counters."""
        usage = AgentUsage("test-agent", "claude-sonnet-4-6")

        assert usage.agent_id == "test-agent"
        assert usage.model == "claude-sonnet-4-6"
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0
        assert usage.turns == 0
        assert usage.cost_usd == 0.0

    def test_add_usage(self):
        """add_usage accumulates tokens and cost."""
        usage = AgentUsage("test", "claude-sonnet-4-6")

        turn_cost = usage.add_usage(
            input_tokens=1000,
            output_tokens=500,
            cache_read_tokens=100,
            cache_creation_tokens=50,
            pricing=DEFAULT_PRICING
        )

        assert usage.input_tokens == 1000
        assert usage.output_tokens == 500
        assert usage.cache_read_tokens == 100
        assert usage.cache_creation_tokens == 50
        assert usage.turns == 1
        assert turn_cost > 0
        assert usage.cost_usd == turn_cost

    def test_add_usage_multiple_turns(self):
        """add_usage accumulates across multiple turns."""
        usage = AgentUsage("test", "claude-sonnet-4-6")

        usage.add_usage(input_tokens=1000, output_tokens=200, pricing=DEFAULT_PRICING)
        usage.add_usage(input_tokens=800, output_tokens=300, pricing=DEFAULT_PRICING)

        assert usage.input_tokens == 1800
        assert usage.output_tokens == 500
        assert usage.turns == 2

    def test_to_dict(self):
        """to_dict serializes all fields."""
        usage = AgentUsage("test", "claude-sonnet-4-6")
        usage.add_usage(input_tokens=1000, output_tokens=500, pricing=DEFAULT_PRICING)

        data = usage.to_dict()

        assert data["agent_id"] == "test"
        assert data["model"] == "claude-sonnet-4-6"
        assert data["input_tokens"] == 1000
        assert data["output_tokens"] == 500
        assert data["turns"] == 1
        assert "cost_usd" in data

    def test_from_dict(self):
        """from_dict deserializes correctly."""
        data = {
            "agent_id": "restored",
            "model": "claude-opus-4-5",
            "input_tokens": 5000,
            "output_tokens": 2000,
            "cache_read_tokens": 1000,
            "cache_creation_tokens": 500,
            "turns": 3,
            "cost_usd": 0.42
        }

        usage = AgentUsage.from_dict(data)

        assert usage.agent_id == "restored"
        assert usage.model == "claude-opus-4-5"
        assert usage.input_tokens == 5000
        assert usage.turns == 3
        assert usage.cost_usd == 0.42


class TestTokenTracker:
    """Tests for TokenTracker class."""

    def test_register_agent(self, tracker):
        """register_agent creates agent entry."""
        tracker.register_agent("frontend-1", "claude-sonnet-4-6")

        usage = tracker.get_agent_usage("frontend-1")
        assert usage is not None
        assert usage["agent_id"] == "frontend-1"
        assert usage["model"] == "claude-sonnet-4-6"

    def test_register_agent_idempotent(self, tracker):
        """register_agent doesn't overwrite existing agent."""
        tracker.register_agent("test", "claude-sonnet-4-6")
        tracker._agents["test"].input_tokens = 1000

        tracker.register_agent("test", "claude-opus-4-5")

        # Should keep original data
        assert tracker._agents["test"].input_tokens == 1000
        assert tracker._agents["test"].model == "claude-sonnet-4-6"

    def test_parse_usage_event(self, tracker):
        """parse_stream_event handles usage events."""
        tracker.register_agent("test", "claude-sonnet-4-6")

        event = tracker.parse_stream_event("test", json.dumps(USAGE_EVENT))

        assert event["type"] == "usage"
        usage = tracker.get_agent_usage("test")
        assert usage["input_tokens"] == 1500
        assert usage["output_tokens"] == 500
        assert usage["turns"] == 1

    def test_parse_result_event(self, tracker):
        """parse_stream_event handles result events."""
        tracker.register_agent("test", "claude-sonnet-4-6")

        event = tracker.parse_stream_event("test", json.dumps(RESULT_EVENT))

        assert event["type"] == "result"
        assert event["session_id"] == "abc123-def456"

    def test_parse_invalid_json(self, tracker):
        """parse_stream_event handles invalid JSON."""
        result = tracker.parse_stream_event("test", "not valid json")
        assert result is None

    def test_parse_empty_line(self, tracker):
        """parse_stream_event handles empty lines."""
        result = tracker.parse_stream_event("test", "")
        assert result is None

        result = tracker.parse_stream_event("test", "   \n")
        assert result is None

    def test_get_all_usage(self, tracker):
        """get_all_usage returns all agents."""
        tracker.register_agent("a1", "claude-sonnet-4-6")
        tracker.register_agent("a2", "claude-opus-4-5")

        all_usage = tracker.get_all_usage()

        assert len(all_usage) == 2
        assert "a1" in all_usage
        assert "a2" in all_usage

    def test_get_total_cost(self, tracker):
        """get_total_cost sums all agents."""
        tracker.register_agent("a1", "claude-sonnet-4-6")
        tracker.register_agent("a2", "claude-sonnet-4-6")

        tracker.parse_stream_event("a1", json.dumps({
            "type": "usage",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0
        }))

        tracker.parse_stream_event("a2", json.dumps({
            "type": "usage",
            "input_tokens": 1_000_000,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0
        }))

        # Each: 1M * 3.00/M = 3.00
        assert tracker.get_total_cost() == 6.0

    def test_get_total_tokens(self, tracker):
        """get_total_tokens aggregates across agents."""
        tracker.register_agent("a1", "claude-sonnet-4-6")
        tracker.register_agent("a2", "claude-sonnet-4-6")

        tracker.parse_stream_event("a1", json.dumps({
            "type": "usage",
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 50
        }))

        tracker.parse_stream_event("a2", json.dumps({
            "type": "usage",
            "input_tokens": 2000,
            "output_tokens": 1000,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 100
        }))

        totals = tracker.get_total_tokens()

        assert totals["input_tokens"] == 3000
        assert totals["output_tokens"] == 1500
        assert totals["cache_read_tokens"] == 300
        assert totals["cache_creation_tokens"] == 150
        assert totals["total_turns"] == 2

    def test_remove_agent(self, tracker):
        """remove_agent removes agent from tracking."""
        tracker.register_agent("test", "claude-sonnet-4-6")
        assert tracker.remove_agent("test") is True
        assert tracker.get_agent_usage("test") is None

    def test_remove_agent_not_found(self, tracker):
        """remove_agent returns False for unknown agent."""
        assert tracker.remove_agent("nonexistent") is False

    def test_on_usage_update_callback(self, tmp_path):
        """on_usage_update callback is called on usage events."""
        callback = Mock()
        tracker = TokenTracker(state_dir=tmp_path, on_usage_update=callback)
        tracker.register_agent("test", "claude-sonnet-4-6")

        tracker.parse_stream_event("test", json.dumps(USAGE_EVENT))

        callback.assert_called_once()
        agent_id, usage_dict = callback.call_args[0]
        assert agent_id == "test"
        assert usage_dict["input_tokens"] == 1500


class TestTokenTrackerPersistence:
    """Tests for TokenTracker persistence."""

    def test_persist_on_update(self, tmp_path):
        """Tracker persists to usage.json on update."""
        tracker = TokenTracker(state_dir=tmp_path)
        tracker.register_agent("test", "claude-sonnet-4-6")
        tracker.parse_stream_event("test", json.dumps(USAGE_EVENT))

        usage_file = tmp_path / "usage.json"
        assert usage_file.exists()

        data = json.loads(usage_file.read_text())
        assert "test" in data
        assert data["test"]["input_tokens"] == 1500

    def test_load_existing_state(self, tmp_path):
        """Tracker loads existing state on init."""
        # Create initial tracker and add usage
        tracker1 = TokenTracker(state_dir=tmp_path)
        tracker1.register_agent("test", "claude-sonnet-4-6")
        tracker1.parse_stream_event("test", json.dumps(USAGE_EVENT))

        # Create new tracker - should load existing state
        tracker2 = TokenTracker(state_dir=tmp_path)

        usage = tracker2.get_agent_usage("test")
        assert usage is not None
        assert usage["input_tokens"] == 1500
        assert usage["turns"] == 1


class TestStreamParser:
    """Tests for StreamParser class."""

    def test_parse_multi_turn_stream(self, tracker):
        """StreamParser handles multi-turn output."""
        tracker.register_agent("test", "claude-sonnet-4-6")
        parser = StreamParser("test", tracker)

        for line in STREAM_OUTPUT.split("\n"):
            parser.parse_line(line)

        # Should have accumulated 2 usage events
        usage = tracker.get_agent_usage("test")
        assert usage["input_tokens"] == 1800  # 1000 + 800
        assert usage["output_tokens"] == 500  # 200 + 300
        assert usage["turns"] == 2

    def test_extracts_session_id(self, tracker):
        """StreamParser extracts session_id from result event."""
        tracker.register_agent("test", "claude-sonnet-4-6")
        parser = StreamParser("test", tracker)

        for line in STREAM_OUTPUT.split("\n"):
            parser.parse_line(line)

        assert parser.get_session_id() == "session-xyz-789"

    def test_session_id_none_before_result(self, tracker):
        """StreamParser returns None before result event."""
        tracker.register_agent("test", "claude-sonnet-4-6")
        parser = StreamParser("test", tracker)

        parser.parse_line(json.dumps(USAGE_EVENT))

        assert parser.get_session_id() is None


class TestCostAccuracy:
    """Tests for cost calculation accuracy."""

    def test_realistic_session_cost(self, tracker):
        """Test cost for a realistic multi-turn session."""
        tracker.register_agent("archie", "claude-opus-4-5")

        # Simulate 5 turns of conversation
        turns = [
            {"input_tokens": 5000, "output_tokens": 1000, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 2000},
            {"input_tokens": 6000, "output_tokens": 800, "cache_read_input_tokens": 2000, "cache_creation_input_tokens": 500},
            {"input_tokens": 7000, "output_tokens": 1200, "cache_read_input_tokens": 2500, "cache_creation_input_tokens": 300},
            {"input_tokens": 8000, "output_tokens": 1500, "cache_read_input_tokens": 2800, "cache_creation_input_tokens": 200},
            {"input_tokens": 9000, "output_tokens": 2000, "cache_read_input_tokens": 3000, "cache_creation_input_tokens": 100},
        ]

        for turn in turns:
            tracker.parse_stream_event("archie", json.dumps({"type": "usage", **turn}))

        usage = tracker.get_agent_usage("archie")

        # Verify token totals
        assert usage["input_tokens"] == 35000
        assert usage["output_tokens"] == 6500
        assert usage["cache_read_tokens"] == 10300
        assert usage["cache_creation_tokens"] == 3100
        assert usage["turns"] == 5

        # Verify cost is reasonable for Opus
        # Input: 35K * 15/M = 0.525
        # Output: 6.5K * 75/M = 0.4875
        # Cache read: 10.3K * 1.5/M = 0.01545
        # Cache write: 3.1K * 18.75/M = 0.058125
        # Total: ~1.086
        assert 1.0 < usage["cost_usd"] < 1.2


# --- Fixtures ---

@pytest.fixture
def tracker(tmp_path):
    """Create a TokenTracker with temporary state directory."""
    return TokenTracker(state_dir=tmp_path)
