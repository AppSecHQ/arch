"""Unit tests for ARCH State Store."""

import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from arch.state import (
    StateStore,
    generate_id,
    utc_now,
    validate_agent_status,
    validate_task_status,
    InvalidStatusError,
    AGENT_STATUSES,
    TASK_STATUSES,
)


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_utc_now_returns_iso_format(self):
        """utc_now returns ISO 8601 formatted string."""
        result = utc_now()
        assert "T" in result
        assert result.endswith("+00:00") or result.endswith("Z")

    def test_generate_id_is_unique(self):
        """generate_id produces unique IDs."""
        ids = [generate_id() for _ in range(100)]
        assert len(set(ids)) == 100

    def test_generate_id_is_8_chars(self):
        """generate_id produces 8-character IDs."""
        assert len(generate_id()) == 8


class TestEnumValidation:
    """Tests for status enum validation."""

    def test_valid_agent_statuses(self):
        """All valid agent statuses pass validation."""
        for status in AGENT_STATUSES:
            assert validate_agent_status(status) == status

    def test_invalid_agent_status_raises(self):
        """Invalid agent status raises InvalidStatusError."""
        with pytest.raises(InvalidStatusError, match="Invalid agent status"):
            validate_agent_status("invalid_status")

    def test_valid_task_statuses(self):
        """All valid task statuses pass validation."""
        for status in TASK_STATUSES:
            assert validate_task_status(status) == status

    def test_invalid_task_status_raises(self):
        """Invalid task status raises InvalidStatusError."""
        with pytest.raises(InvalidStatusError, match="Invalid task status"):
            validate_task_status("invalid_status")

    def test_update_agent_validates_status(self, state_store):
        """update_agent validates status before updating."""
        state_store.register_agent("test", "role", "/wt")

        with pytest.raises(InvalidStatusError):
            state_store.update_agent("test", status="bad_status")

        # Agent should be unchanged
        assert state_store.get_agent("test")["status"] == "idle"

    def test_update_agent_valid_status(self, state_store):
        """update_agent accepts valid status values."""
        state_store.register_agent("test", "role", "/wt")

        for status in AGENT_STATUSES:
            result = state_store.update_agent("test", status=status)
            assert result["status"] == status

    def test_update_task_validates_status(self, state_store):
        """update_task validates status before updating."""
        task = state_store.add_task("agent", "description")

        with pytest.raises(InvalidStatusError):
            state_store.update_task(task["id"], status="bad_status")

        # Task should be unchanged
        tasks = state_store.get_tasks()
        assert tasks[0]["status"] == "pending"

    def test_update_task_valid_status(self, state_store):
        """update_task accepts valid status values."""
        task = state_store.add_task("agent", "description")

        for status in TASK_STATUSES:
            result = state_store.update_task(task["id"], status=status)
            assert result["status"] == status


class TestStateStoreProject:
    """Tests for project operations."""

    def test_init_project(self, state_store):
        """init_project sets project metadata."""
        state_store.init_project("Test Project", "A test", "/path/to/repo")

        project = state_store.get_project()
        assert project["name"] == "Test Project"
        assert project["description"] == "A test"
        assert project["repo"] == "/path/to/repo"
        assert project["started_at"] != ""

    def test_get_project_returns_copy(self, state_store):
        """get_project returns a copy, not the original."""
        state_store.init_project("Test", "Desc", "/repo")
        project = state_store.get_project()
        project["name"] = "Modified"

        assert state_store.get_project()["name"] == "Test"


class TestStateStoreAgents:
    """Tests for agent operations."""

    def test_register_agent_creates_entry(self, state_store):
        """register_agent creates a new agent entry."""
        agent = state_store.register_agent(
            agent_id="frontend-1",
            role="frontend-dev",
            worktree="/worktrees/frontend-1",
            sandboxed=True
        )

        assert agent["id"] == "frontend-1"
        assert agent["role"] == "frontend-dev"
        assert agent["worktree"] == "/worktrees/frontend-1"
        assert agent["sandboxed"] is True
        assert agent["status"] == "idle"
        assert agent["usage"]["cost_usd"] == 0.0

    def test_register_agent_with_all_options(self, state_store):
        """register_agent accepts all optional parameters."""
        agent = state_store.register_agent(
            agent_id="sec-1",
            role="security",
            worktree="/worktrees/sec-1",
            sandboxed=True,
            skip_permissions=True,
            pid=None,
            container_name="arch-sec-1"
        )

        assert agent["sandboxed"] is True
        assert agent["skip_permissions"] is True
        assert agent["pid"] is None
        assert agent["container_name"] == "arch-sec-1"

    def test_get_agent_returns_agent(self, state_store):
        """get_agent returns the requested agent."""
        state_store.register_agent("test-1", "test", "/wt")
        agent = state_store.get_agent("test-1")

        assert agent is not None
        assert agent["id"] == "test-1"

    def test_get_agent_returns_none_for_unknown(self, state_store):
        """get_agent returns None for unknown agent."""
        assert state_store.get_agent("unknown") is None

    def test_get_agent_returns_copy(self, state_store):
        """get_agent returns a copy, not the original."""
        state_store.register_agent("test-1", "test", "/wt")
        agent = state_store.get_agent("test-1")
        agent["status"] = "modified"

        assert state_store.get_agent("test-1")["status"] == "idle"

    def test_list_agents(self, state_store):
        """list_agents returns all agents."""
        state_store.register_agent("a1", "role1", "/wt1")
        state_store.register_agent("a2", "role2", "/wt2")

        agents = state_store.list_agents()
        assert len(agents) == 2
        assert {a["id"] for a in agents} == {"a1", "a2"}

    def test_update_agent_updates_fields(self, state_store):
        """update_agent modifies agent fields."""
        state_store.register_agent("test-1", "test", "/wt")

        updated = state_store.update_agent(
            "test-1",
            status="working",
            task="Building feature X"
        )

        assert updated["status"] == "working"
        assert updated["task"] == "Building feature X"
        assert state_store.get_agent("test-1")["status"] == "working"

    def test_update_agent_nested_usage(self, state_store):
        """update_agent can update nested usage fields."""
        state_store.register_agent("test-1", "test", "/wt")

        updated = state_store.update_agent(
            "test-1",
            usage={"input_tokens": 1000, "cost_usd": 0.05}
        )

        assert updated["usage"]["input_tokens"] == 1000
        assert updated["usage"]["cost_usd"] == 0.05
        # Original fields should be preserved
        assert updated["usage"]["output_tokens"] == 0

    def test_update_agent_returns_none_for_unknown(self, state_store):
        """update_agent returns None for unknown agent."""
        assert state_store.update_agent("unknown", status="working") is None

    def test_remove_agent(self, state_store):
        """remove_agent removes the agent."""
        state_store.register_agent("test-1", "test", "/wt")
        assert state_store.remove_agent("test-1") is True
        assert state_store.get_agent("test-1") is None

    def test_remove_agent_returns_false_for_unknown(self, state_store):
        """remove_agent returns False for unknown agent."""
        assert state_store.remove_agent("unknown") is False


class TestStateStoreMessages:
    """Tests for message operations."""

    def test_add_message_creates_message(self, state_store):
        """add_message creates a message entry."""
        msg = state_store.add_message(
            from_agent="frontend-1",
            to_agent="archie",
            content="Task complete"
        )

        assert msg["from"] == "frontend-1"
        assert msg["to"] == "archie"
        assert msg["content"] == "Task complete"
        assert msg["read"] is False
        assert msg["id"] is not None
        assert msg["timestamp"] is not None

    def test_get_messages_filters_by_recipient(self, state_store):
        """get_messages only returns messages for the specified agent."""
        state_store.add_message("a1", "archie", "For archie")
        state_store.add_message("a2", "frontend-1", "For frontend")

        messages, _ = state_store.get_messages("archie")
        assert len(messages) == 1
        assert messages[0]["content"] == "For archie"

    def test_get_messages_includes_broadcast(self, state_store):
        """get_messages includes broadcast messages."""
        state_store.add_message("archie", "frontend-1", "Direct")
        state_store.add_message("archie", "broadcast", "Broadcast")

        messages, _ = state_store.get_messages("frontend-1")
        assert len(messages) == 2

    def test_get_messages_since_id(self, state_store):
        """get_messages respects since_id parameter."""
        m1 = state_store.add_message("a1", "archie", "First")
        m2 = state_store.add_message("a2", "archie", "Second")
        m3 = state_store.add_message("a3", "archie", "Third")

        messages, _ = state_store.get_messages("archie", since_id=m1["id"])
        assert len(messages) == 2
        assert messages[0]["content"] == "Second"
        assert messages[1]["content"] == "Third"

    def test_get_messages_marks_read(self, state_store):
        """get_messages marks messages as read by default."""
        state_store.add_message("a1", "archie", "Test")

        state_store.get_messages("archie")
        all_messages = state_store.get_all_messages()
        assert all_messages[0]["read"] is True

    def test_get_messages_no_mark_read(self, state_store):
        """get_messages can skip marking as read."""
        state_store.add_message("a1", "archie", "Test")

        state_store.get_messages("archie", mark_read=False)
        all_messages = state_store.get_all_messages()
        assert all_messages[0]["read"] is False

    def test_get_messages_returns_cursor(self, state_store):
        """get_messages returns cursor for pagination."""
        m1 = state_store.add_message("a1", "archie", "First")
        m2 = state_store.add_message("a2", "archie", "Second")

        messages, cursor = state_store.get_messages("archie")
        assert cursor == m2["id"]

    def test_get_messages_persists_cursor(self, state_dir):
        """get_messages persists cursor across store instances."""
        store1 = StateStore(state_dir)
        m1 = store1.add_message("a1", "archie", "First")
        m2 = store1.add_message("a2", "archie", "Second")
        store1.get_messages("archie")  # Sets cursor to m2

        # New message after cursor
        m3 = store1.add_message("a3", "archie", "Third")

        # New store instance - should use persisted cursor
        store2 = StateStore(state_dir)
        messages, _ = store2.get_messages("archie")

        # Should only get the message after the cursor
        assert len(messages) == 1
        assert messages[0]["content"] == "Third"


class TestStateStoreDecisions:
    """Tests for user decision operations."""

    def test_add_pending_decision(self, state_store):
        """add_pending_decision creates a decision entry."""
        decision = state_store.add_pending_decision(
            question="Merge frontend-1?",
            options=["Yes", "No"]
        )

        assert decision["question"] == "Merge frontend-1?"
        assert decision["options"] == ["Yes", "No"]
        assert decision["answer"] is None
        assert decision["answered_at"] is None

    def test_add_pending_decision_no_options(self, state_store):
        """add_pending_decision works without options."""
        decision = state_store.add_pending_decision("Free-form question")
        assert decision["options"] == []

    def test_get_pending_decisions(self, state_store):
        """get_pending_decisions returns unanswered decisions."""
        d1 = state_store.add_pending_decision("Q1")
        d2 = state_store.add_pending_decision("Q2")
        state_store.answer_decision(d1["id"], "Answer 1")

        pending = state_store.get_pending_decisions()
        assert len(pending) == 1
        assert pending[0]["question"] == "Q2"

    def test_answer_decision(self, state_store):
        """answer_decision records the answer."""
        decision = state_store.add_pending_decision("Q?")
        result = state_store.answer_decision(decision["id"], "Yes")

        assert result is True

        pending = state_store.get_pending_decisions()
        assert len(pending) == 0

    def test_answer_decision_unknown_id(self, state_store):
        """answer_decision returns False for unknown ID."""
        assert state_store.answer_decision("unknown", "answer") is False


class TestStateStoreTasks:
    """Tests for task operations."""

    def test_add_task(self, state_store):
        """add_task creates a task entry."""
        task = state_store.add_task(
            assigned_to="frontend-1",
            description="Build the navbar"
        )

        assert task["assigned_to"] == "frontend-1"
        assert task["description"] == "Build the navbar"
        assert task["status"] == "pending"
        assert task["completed_at"] is None

    def test_get_tasks_filter_by_assigned(self, state_store):
        """get_tasks filters by assigned_to."""
        state_store.add_task("a1", "Task 1")
        state_store.add_task("a2", "Task 2")

        tasks = state_store.get_tasks(assigned_to="a1")
        assert len(tasks) == 1
        assert tasks[0]["description"] == "Task 1"

    def test_get_tasks_filter_by_status(self, state_store):
        """get_tasks filters by status."""
        t1 = state_store.add_task("a1", "Task 1")
        t2 = state_store.add_task("a1", "Task 2")
        state_store.update_task(t1["id"], status="done")

        tasks = state_store.get_tasks(status="pending")
        assert len(tasks) == 1
        assert tasks[0]["description"] == "Task 2"

    def test_update_task(self, state_store):
        """update_task modifies task fields."""
        task = state_store.add_task("a1", "Task")
        updated = state_store.update_task(task["id"], status="in_progress")

        assert updated["status"] == "in_progress"

    def test_update_task_sets_completed_at(self, state_store):
        """update_task auto-sets completed_at when status is done."""
        task = state_store.add_task("a1", "Task")
        updated = state_store.update_task(task["id"], status="done")

        assert updated["completed_at"] is not None

    def test_update_task_unknown_id(self, state_store):
        """update_task returns None for unknown ID."""
        assert state_store.update_task("unknown", status="done") is None


class TestStateStorePersistence:
    """Tests for JSON persistence."""

    def test_state_persists_to_json(self, state_dir):
        """State is written to JSON files."""
        store = StateStore(state_dir)
        store.init_project("Test", "Desc", "/repo")
        store.register_agent("a1", "role", "/wt")

        # Check files exist
        assert (state_dir / "project.json").exists()
        assert (state_dir / "agents.json").exists()

    def test_state_loads_from_json(self, state_dir):
        """State loads from existing JSON files."""
        # Create state
        store1 = StateStore(state_dir)
        store1.init_project("Test Project", "Description", "/repo")
        store1.register_agent("agent-1", "frontend", "/wt")

        # Load in new instance
        store2 = StateStore(state_dir)
        assert store2.get_project()["name"] == "Test Project"
        assert store2.get_agent("agent-1") is not None

    def test_atomic_write(self, state_dir):
        """Writes are atomic (use temp file + rename)."""
        store = StateStore(state_dir)
        store.init_project("Test", "Desc", "/repo")

        # No temp files should remain
        tmp_files = list(state_dir.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_clear_state(self, state_store):
        """clear() resets all state."""
        state_store.init_project("Test", "Desc", "/repo")
        state_store.register_agent("a1", "role", "/wt")
        state_store.add_message("a1", "archie", "Hello")

        state_store.clear()

        assert state_store.get_project()["name"] == ""
        assert len(state_store.list_agents()) == 0
        assert len(state_store.get_all_messages()) == 0


class TestStateStoreThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_agent_registration(self, state_store):
        """Multiple threads can register agents safely."""
        errors = []

        def register_agent(agent_id):
            try:
                state_store.register_agent(agent_id, "role", f"/wt/{agent_id}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=register_agent, args=(f"agent-{i}",))
            for i in range(10)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(state_store.list_agents()) == 10

    def test_concurrent_messages(self, state_store):
        """Multiple threads can send messages safely."""
        errors = []

        def send_message(i):
            try:
                state_store.add_message(f"agent-{i}", "archie", f"Message {i}")
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=send_message, args=(i,))
            for i in range(20)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(state_store.get_all_messages()) == 20


class TestStateStoreFullState:
    """Tests for get_full_state."""

    def test_get_full_state(self, state_store):
        """get_full_state returns complete state copy."""
        state_store.init_project("Test", "Desc", "/repo")
        state_store.register_agent("a1", "role", "/wt")
        state_store.add_message("a1", "archie", "Hello")
        state_store.add_pending_decision("Q?")
        state_store.add_task("a1", "Task")

        full = state_store.get_full_state()

        assert full["project"]["name"] == "Test"
        assert len(full["agents"]) == 1
        assert len(full["messages"]) == 1
        assert len(full["pending_user_decisions"]) == 1
        assert len(full["tasks"]) == 1


# --- Fixtures ---

@pytest.fixture
def state_dir():
    """Create a temporary directory for state files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def state_store(state_dir):
    """Create a StateStore instance with temporary directory."""
    return StateStore(state_dir)
