"""Unit tests for ARCH MCP Server."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from starlette.applications import Starlette

from arch.mcp_server import (
    MCPServer,
    WORKER_TOOLS,
    ARCHIE_ONLY_TOOLS,
    GITHUB_TOOLS,
)
from arch.state import StateStore


class TestToolDefinitions:
    """Tests for tool definitions."""

    def test_worker_tools_defined(self):
        """Worker tools are defined correctly."""
        tool_names = {t.name for t in WORKER_TOOLS}
        assert tool_names == {"send_message", "get_messages", "update_status", "report_completion"}

    def test_archie_tools_defined(self):
        """Archie-only tools are defined correctly."""
        tool_names = {t.name for t in ARCHIE_ONLY_TOOLS}
        expected = {
            "spawn_agent", "teardown_agent", "list_agents", "escalate_to_user",
            "request_merge", "get_project_context", "close_project", "update_brief"
        }
        assert tool_names == expected

    def test_github_tools_defined(self):
        """GitHub tools are defined correctly."""
        tool_names = {t.name for t in GITHUB_TOOLS}
        expected = {
            "gh_create_issue", "gh_list_issues", "gh_close_issue",
            "gh_update_issue", "gh_add_comment", "gh_create_milestone", "gh_list_milestones"
        }
        assert tool_names == expected

    def test_all_tools_have_schemas(self):
        """All tools have input schemas."""
        all_tools = WORKER_TOOLS + ARCHIE_ONLY_TOOLS + GITHUB_TOOLS
        for tool in all_tools:
            assert tool.inputSchema is not None
            assert tool.inputSchema.get("type") == "object"


class TestAccessControl:
    """Tests for tool access controls."""

    def test_worker_tools_available_to_all(self, mcp_server):
        """Worker tools are available to all agents."""
        # Test worker agent
        tools = mcp_server._get_tools_for_agent("frontend-1")
        tool_names = {t.name for t in tools}

        assert "send_message" in tool_names
        assert "get_messages" in tool_names
        assert "update_status" in tool_names
        assert "report_completion" in tool_names

    def test_archie_tools_restricted(self, mcp_server):
        """Archie-only tools are not available to workers."""
        tools = mcp_server._get_tools_for_agent("frontend-1")
        tool_names = {t.name for t in tools}

        assert "spawn_agent" not in tool_names
        assert "teardown_agent" not in tool_names
        assert "list_agents" not in tool_names
        assert "escalate_to_user" not in tool_names

    def test_archie_gets_all_tools(self, mcp_server):
        """Archie gets both worker and Archie-only tools."""
        tools = mcp_server._get_tools_for_agent("archie")
        tool_names = {t.name for t in tools}

        # Worker tools
        assert "send_message" in tool_names
        assert "get_messages" in tool_names

        # Archie-only tools
        assert "spawn_agent" in tool_names
        assert "teardown_agent" in tool_names
        assert "list_agents" in tool_names
        assert "escalate_to_user" in tool_names

    def test_github_tools_available_to_archie_when_configured(self, mcp_server_with_github):
        """GitHub tools available to Archie when GitHub is configured."""
        tools = mcp_server_with_github._get_tools_for_agent("archie")
        tool_names = {t.name for t in tools}

        assert "gh_create_issue" in tool_names
        assert "gh_list_issues" in tool_names

    def test_github_tools_not_available_without_config(self, mcp_server):
        """GitHub tools not available when GitHub is not configured."""
        tools = mcp_server._get_tools_for_agent("archie")
        tool_names = {t.name for t in tools}

        assert "gh_create_issue" not in tool_names

    def test_check_tool_access_worker(self, mcp_server):
        """check_tool_access correctly restricts workers."""
        # Worker can access worker tools
        assert mcp_server._check_tool_access("frontend-1", "send_message") is True
        assert mcp_server._check_tool_access("frontend-1", "get_messages") is True

        # Worker cannot access Archie tools
        assert mcp_server._check_tool_access("frontend-1", "spawn_agent") is False
        assert mcp_server._check_tool_access("frontend-1", "escalate_to_user") is False

    def test_check_tool_access_archie(self, mcp_server):
        """check_tool_access correctly allows Archie."""
        # Archie can access all tools
        assert mcp_server._check_tool_access("archie", "send_message") is True
        assert mcp_server._check_tool_access("archie", "spawn_agent") is True
        assert mcp_server._check_tool_access("archie", "escalate_to_user") is True


class TestWorkerTools:
    """Tests for worker tool implementations."""

    @pytest.mark.asyncio
    async def test_send_message(self, mcp_server):
        """send_message creates a message."""
        result = await mcp_server._handle_send_message(
            agent_id="frontend-1",
            to="archie",
            content="Task complete"
        )

        assert "message_id" in result
        assert "timestamp" in result

        # Verify message was stored
        messages, _ = mcp_server.state.get_messages("archie")
        assert len(messages) == 1
        assert messages[0]["content"] == "Task complete"

    @pytest.mark.asyncio
    async def test_get_messages(self, mcp_server):
        """get_messages retrieves messages."""
        # Add some messages
        mcp_server.state.add_message("archie", "frontend-1", "Please build X")
        mcp_server.state.add_message("archie", "frontend-1", "Also build Y")

        result = await mcp_server._handle_get_messages("frontend-1")

        assert len(result["messages"]) == 2
        assert "cursor" in result

    @pytest.mark.asyncio
    async def test_get_messages_with_since_id(self, mcp_server):
        """get_messages respects since_id."""
        msg1 = mcp_server.state.add_message("archie", "frontend-1", "First")
        mcp_server.state.add_message("archie", "frontend-1", "Second")

        result = await mcp_server._handle_get_messages("frontend-1", since_id=msg1["id"])

        assert len(result["messages"]) == 1
        assert result["messages"][0]["content"] == "Second"

    @pytest.mark.asyncio
    async def test_update_status(self, mcp_server):
        """update_status updates agent status."""
        mcp_server.state.register_agent("frontend-1", "frontend", "/wt")

        result = await mcp_server._handle_update_status(
            agent_id="frontend-1",
            task="Building navbar",
            status="working"
        )

        assert result["ok"] is True
        agent = mcp_server.state.get_agent("frontend-1")
        assert agent["task"] == "Building navbar"
        assert agent["status"] == "working"

    @pytest.mark.asyncio
    async def test_report_completion(self, mcp_server):
        """report_completion updates status and notifies Archie."""
        mcp_server.state.register_agent("frontend-1", "frontend", "/wt")

        result = await mcp_server._handle_report_completion(
            agent_id="frontend-1",
            summary="Navbar built",
            artifacts=["src/navbar.tsx", "src/navbar.css"]
        )

        assert result["ok"] is True

        # Agent status updated
        agent = mcp_server.state.get_agent("frontend-1")
        assert agent["status"] == "done"

        # Message sent to Archie
        messages, _ = mcp_server.state.get_messages("archie")
        assert any("Navbar built" in m["content"] for m in messages)


class TestArchieOnlyTools:
    """Tests for Archie-only tool implementations."""

    @pytest.mark.asyncio
    async def test_spawn_agent_calls_callback(self, mcp_server):
        """spawn_agent calls the configured callback."""
        callback = AsyncMock(return_value={"agent_id": "new-agent", "status": "spawning"})
        mcp_server.on_spawn_agent = callback

        result = await mcp_server._handle_spawn_agent(
            role="frontend-dev",
            assignment="Build the navbar"
        )

        callback.assert_called_once()
        assert result["agent_id"] == "new-agent"

    @pytest.mark.asyncio
    async def test_spawn_agent_without_callback(self, mcp_server):
        """spawn_agent returns error without callback."""
        result = await mcp_server._handle_spawn_agent(
            role="frontend-dev",
            assignment="Build the navbar"
        )

        assert "error" in result

    @pytest.mark.asyncio
    async def test_teardown_agent_calls_callback(self, mcp_server):
        """teardown_agent calls the configured callback."""
        mcp_server.state.register_agent("test-agent", "test", "/wt")
        callback = AsyncMock(return_value=True)
        mcp_server.on_teardown_agent = callback

        result = await mcp_server._handle_teardown_agent(
            agent_id="test-agent",
            reason="Task complete"
        )

        callback.assert_called_once_with("test-agent")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_list_agents(self, mcp_server):
        """list_agents returns all agents with usage."""
        mcp_server.state.register_agent("frontend-1", "frontend", "/wt1")
        mcp_server.state.register_agent("backend-1", "backend", "/wt2")
        mcp_server.state.update_agent("frontend-1", status="working", task="Building")

        result = await mcp_server._handle_list_agents()

        assert len(result["agents"]) == 2
        agents_by_id = {a["id"]: a for a in result["agents"]}
        assert agents_by_id["frontend-1"]["status"] == "working"
        assert agents_by_id["frontend-1"]["task"] == "Building"

    @pytest.mark.asyncio
    async def test_escalate_to_user_blocks(self, mcp_server):
        """escalate_to_user blocks until answered."""
        # Start the escalation in background
        escalation_task = asyncio.create_task(
            mcp_server._handle_escalate_to_user(
                question="Merge frontend-1?",
                options=["Yes", "No"]
            )
        )

        # Give it time to register
        await asyncio.sleep(0.1)

        # Verify decision was created
        decisions = mcp_server.state.get_pending_decisions()
        assert len(decisions) == 1
        decision_id = decisions[0]["id"]

        # Answer the escalation
        mcp_server.answer_escalation(decision_id, "Yes")

        # Wait for result
        result = await asyncio.wait_for(escalation_task, timeout=1.0)
        assert result["answer"] == "Yes"

    @pytest.mark.asyncio
    async def test_get_project_context(self, mcp_server_with_repo):
        """get_project_context returns project info."""
        mcp_server_with_repo.state.init_project("Test Project", "A test", str(mcp_server_with_repo.repo_path))
        mcp_server_with_repo.state.register_agent("archie", "lead", "/wt")

        result = await mcp_server_with_repo._handle_get_project_context()

        assert result["name"] == "Test Project"
        assert result["description"] == "A test"
        assert len(result["active_agents"]) == 1

    @pytest.mark.asyncio
    async def test_close_project_calls_callback(self, mcp_server):
        """close_project calls the configured callback."""
        callback = AsyncMock(return_value=True)
        mcp_server.on_close_project = callback

        result = await mcp_server._handle_close_project(summary="All done")

        callback.assert_called_once_with("All done")
        assert result["ok"] is True


class TestUpdateBrief:
    """Tests for update_brief tool."""

    @pytest.mark.asyncio
    async def test_update_current_status(self, mcp_server_with_brief):
        """update_brief updates current_status section."""
        result = await mcp_server_with_brief._handle_update_brief(
            section="current_status",
            content="Phase 1 complete. Starting Phase 2."
        )

        assert result["ok"] is True

        # Verify content was updated
        brief = (mcp_server_with_brief.repo_path / "BRIEF.md").read_text()
        assert "Phase 1 complete" in brief

    @pytest.mark.asyncio
    async def test_update_decisions_log(self, mcp_server_with_brief):
        """update_brief appends to decisions_log."""
        result = await mcp_server_with_brief._handle_update_brief(
            section="decisions_log",
            content="Use React for frontend | Better component model"
        )

        assert result["ok"] is True

        # Verify row was appended
        brief = (mcp_server_with_brief.repo_path / "BRIEF.md").read_text()
        assert "Use React for frontend" in brief

    @pytest.mark.asyncio
    async def test_update_brief_without_repo(self, mcp_server):
        """update_brief fails without repo_path."""
        result = await mcp_server._handle_update_brief(
            section="current_status",
            content="Test"
        )

        assert result["ok"] is False
        assert "error" in result


class TestToolDispatch:
    """Tests for tool dispatch and access control."""

    @pytest.mark.asyncio
    async def test_dispatch_worker_tool(self, mcp_server):
        """Tool dispatch works for worker tools."""
        mcp_server.state.register_agent("frontend-1", "frontend", "/wt")

        result = await mcp_server._handle_tool_call(
            agent_id="frontend-1",
            tool_name="update_status",
            arguments={"task": "Building", "status": "working"}
        )

        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_dispatch_denies_archie_tool_to_worker(self, mcp_server):
        """Tool dispatch denies Archie-only tools to workers."""
        result = await mcp_server._handle_tool_call(
            agent_id="frontend-1",
            tool_name="spawn_agent",
            arguments={"role": "backend", "assignment": "Build API"}
        )

        assert "error" in result
        assert "Access denied" in result["error"]

    @pytest.mark.asyncio
    async def test_dispatch_archie_tool(self, mcp_server):
        """Tool dispatch works for Archie tools."""
        result = await mcp_server._handle_tool_call(
            agent_id="archie",
            tool_name="list_agents",
            arguments={}
        )

        assert "agents" in result

    @pytest.mark.asyncio
    async def test_dispatch_unknown_tool(self, mcp_server):
        """Tool dispatch denies access to unknown tools."""
        result = await mcp_server._handle_tool_call(
            agent_id="archie",
            tool_name="nonexistent_tool",
            arguments={}
        )

        assert "error" in result
        # Access denied is returned (we don't expose tool existence)
        assert "Access denied" in result["error"] or "Unknown tool" in result["error"]


class TestGitHubTools:
    """Tests for GitHub tool implementations."""

    @pytest.mark.asyncio
    async def test_gh_create_issue_without_github(self, mcp_server):
        """gh_create_issue fails without GitHub config."""
        result = await mcp_server._handle_gh_create_issue(
            title="Test Issue",
            body="Test body"
        )

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_create_issue_mocked(self, mcp_server_with_github):
        """gh_create_issue calls gh CLI correctly."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://github.com/test/repo/issues/42\n"
            )

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Test Issue",
                body="Test body",
                labels=["bug", "urgent"]
            )

            assert result["issue_number"] == 42
            assert "42" in result["url"]

            # Verify CLI was called correctly
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "issue" in call_args
            assert "create" in call_args

    @pytest.mark.asyncio
    async def test_gh_list_issues_mocked(self, mcp_server_with_github):
        """gh_list_issues parses output correctly."""
        mock_output = json.dumps([
            {
                "number": 1,
                "title": "Issue 1",
                "labels": [{"name": "bug"}],
                "state": "open",
                "assignees": [{"login": "user1"}],
                "url": "https://github.com/test/repo/issues/1"
            }
        ])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=mock_output)

            result = await mcp_server_with_github._handle_gh_list_issues()

            assert len(result["issues"]) == 1
            assert result["issues"][0]["number"] == 1
            assert result["issues"][0]["labels"] == ["bug"]

    @pytest.mark.asyncio
    async def test_gh_close_issue_mocked(self, mcp_server_with_github):
        """gh_close_issue calls gh CLI correctly."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            result = await mcp_server_with_github._handle_gh_close_issue(
                issue_number=42,
                comment="Fixed in PR #43"
            )

            assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_gh_timeout_handling(self, mcp_server_with_github):
        """GitHub tools handle timeouts gracefully."""
        with patch("subprocess.run") as mock_run:
            from subprocess import TimeoutExpired
            mock_run.side_effect = TimeoutExpired("gh", 30)

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Test",
                body="Test"
            )

            assert "error" in result
            assert "timed out" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_update_issue_mocked(self, mcp_server_with_github):
        """gh_update_issue calls gh CLI correctly."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            result = await mcp_server_with_github._handle_gh_update_issue(
                issue_number=42,
                add_labels=["priority:high"],
                remove_labels=["needs-triage"],
                milestone="Sprint 1",
                assignee="developer1"
            )

            assert result["ok"] is True
            assert result.get("error") is None

            # Verify CLI was called with correct arguments
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "issue" in call_args
            assert "edit" in call_args
            assert "42" in call_args
            assert "--add-label" in call_args
            assert "--remove-label" in call_args
            assert "--milestone" in call_args
            assert "--add-assignee" in call_args

    @pytest.mark.asyncio
    async def test_gh_update_issue_without_github(self, mcp_server):
        """gh_update_issue fails without GitHub config."""
        result = await mcp_server._handle_gh_update_issue(
            issue_number=42,
            add_labels=["bug"]
        )

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_add_comment_mocked(self, mcp_server_with_github):
        """gh_add_comment calls gh CLI correctly."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            result = await mcp_server_with_github._handle_gh_add_comment(
                issue_number=42,
                body="This is a progress update."
            )

            assert result["ok"] is True

            # Verify CLI was called correctly
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "issue" in call_args
            assert "comment" in call_args
            assert "42" in call_args
            assert "--body" in call_args

    @pytest.mark.asyncio
    async def test_gh_add_comment_without_github(self, mcp_server):
        """gh_add_comment fails without GitHub config."""
        result = await mcp_server._handle_gh_add_comment(
            issue_number=42,
            body="Comment"
        )

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_create_milestone_mocked(self, mcp_server_with_github):
        """gh_create_milestone calls gh API correctly."""
        mock_response = json.dumps({
            "number": 1,
            "html_url": "https://github.com/test/repo/milestone/1"
        })

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=mock_response)

            result = await mcp_server_with_github._handle_gh_create_milestone(
                title="Sprint 1",
                description="First sprint",
                due_date="2026-03-15"
            )

            assert result["milestone_number"] == 1
            assert "milestone/1" in result["url"]

            # Verify API was called correctly
            call_args = mock_run.call_args[0][0]
            assert "gh" in call_args
            assert "api" in call_args
            assert "milestones" in str(call_args)
            assert "-X" in call_args
            assert "POST" in call_args

    @pytest.mark.asyncio
    async def test_gh_create_milestone_without_github(self, mcp_server):
        """gh_create_milestone fails without GitHub config."""
        result = await mcp_server._handle_gh_create_milestone(
            title="Sprint 1"
        )

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_list_milestones_mocked(self, mcp_server_with_github):
        """gh_list_milestones parses JSONL output correctly."""
        # gh api with --jq returns JSONL (one JSON object per line)
        mock_output = '{"number": 1, "title": "Sprint 1", "open_issues": 5, "closed_issues": 3, "due_on": "2026-03-15", "html_url": "https://github.com/test/repo/milestone/1"}\n{"number": 2, "title": "Sprint 2", "open_issues": 10, "closed_issues": 0, "due_on": null, "html_url": "https://github.com/test/repo/milestone/2"}'

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=mock_output)

            result = await mcp_server_with_github._handle_gh_list_milestones()

            assert len(result["milestones"]) == 2
            assert result["milestones"][0]["number"] == 1
            assert result["milestones"][0]["title"] == "Sprint 1"
            assert result["milestones"][0]["open_issues"] == 5
            assert result["milestones"][0]["closed_issues"] == 3
            assert result["milestones"][1]["number"] == 2

    @pytest.mark.asyncio
    async def test_gh_list_milestones_empty(self, mcp_server_with_github):
        """gh_list_milestones handles empty response."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            result = await mcp_server_with_github._handle_gh_list_milestones()

            assert result["milestones"] == []

    @pytest.mark.asyncio
    async def test_gh_list_milestones_without_github(self, mcp_server):
        """gh_list_milestones fails without GitHub config."""
        result = await mcp_server._handle_gh_list_milestones()

        assert "error" in result
        assert "not configured" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_create_issue_with_all_options(self, mcp_server_with_github):
        """gh_create_issue handles all optional parameters."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://github.com/test/repo/issues/99\n"
            )

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Feature Request",
                body="Add dark mode",
                labels=["enhancement", "frontend"],
                milestone="Sprint 2",
                assignee="designer1"
            )

            assert result["issue_number"] == 99

            # Verify all parameters were passed
            call_args = mock_run.call_args[0][0]
            assert "--label" in call_args
            assert "--milestone" in call_args
            assert "--assignee" in call_args

    @pytest.mark.asyncio
    async def test_gh_list_issues_with_filters(self, mcp_server_with_github):
        """gh_list_issues handles filter parameters."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="[]")

            await mcp_server_with_github._handle_gh_list_issues(
                labels=["bug", "critical"],
                milestone="Sprint 1",
                state="closed",
                limit=50
            )

            call_args = mock_run.call_args[0][0]
            # Each label gets its own --label flag
            assert call_args.count("--label") == 2
            assert "--milestone" in call_args
            assert "--state" in call_args
            assert "closed" in call_args
            assert "--limit" in call_args
            assert "50" in call_args

    @pytest.mark.asyncio
    async def test_gh_list_issues_empty_response(self, mcp_server_with_github):
        """gh_list_issues handles empty issue list."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="[]")

            result = await mcp_server_with_github._handle_gh_list_issues()

            assert result["issues"] == []

    @pytest.mark.asyncio
    async def test_gh_list_issues_no_assignee(self, mcp_server_with_github):
        """gh_list_issues handles issues with no assignee."""
        mock_output = json.dumps([
            {
                "number": 1,
                "title": "Unassigned Issue",
                "labels": [],
                "state": "open",
                "assignees": [],
                "url": "https://github.com/test/repo/issues/1"
            }
        ])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=mock_output)

            result = await mcp_server_with_github._handle_gh_list_issues()

            assert result["issues"][0]["assignee"] is None

    @pytest.mark.asyncio
    async def test_gh_cli_error_handling(self, mcp_server_with_github):
        """GitHub tools handle CLI errors gracefully."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="",
                stderr="error: Could not resolve to a Repository"
            )

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Test",
                body="Test"
            )

            assert "error" in result
            assert "Repository" in result["error"]

    @pytest.mark.asyncio
    async def test_gh_close_issue_with_comment(self, mcp_server_with_github):
        """gh_close_issue passes comment to CLI."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            await mcp_server_with_github._handle_gh_close_issue(
                issue_number=42,
                comment="Resolved in PR #50"
            )

            call_args = mock_run.call_args[0][0]
            assert "--comment" in call_args
            assert "Resolved in PR #50" in call_args

    @pytest.mark.asyncio
    async def test_gh_close_issue_without_comment(self, mcp_server_with_github):
        """gh_close_issue works without comment."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="")

            await mcp_server_with_github._handle_gh_close_issue(
                issue_number=42
            )

            call_args = mock_run.call_args[0][0]
            assert "--comment" not in call_args

    @pytest.mark.asyncio
    async def test_gh_close_issue_failure(self, mcp_server_with_github):
        """gh_close_issue handles CLI failure."""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="",
                stderr="issue not found"
            )

            result = await mcp_server_with_github._handle_gh_close_issue(
                issue_number=9999
            )

            assert result["ok"] is False
            assert "error" in result

    @pytest.mark.asyncio
    async def test_gh_create_issue_url_parsing(self, mcp_server_with_github):
        """gh_create_issue correctly parses issue number from URL."""
        with patch("subprocess.run") as mock_run:
            # Test with trailing newline and whitespace
            mock_run.return_value = Mock(
                returncode=0,
                stdout="https://github.com/org/repo/issues/123\n  "
            )

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Test",
                body="Test"
            )

            assert result["issue_number"] == 123

    @pytest.mark.asyncio
    async def test_gh_exception_handling(self, mcp_server_with_github):
        """GitHub tools handle unexpected exceptions."""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("Command not found")

            result = await mcp_server_with_github._handle_gh_create_issue(
                title="Test",
                body="Test"
            )

            assert "error" in result
            assert "Command not found" in result["error"]


class TestMCPServerCreate:
    """Tests for MCP server instance creation."""

    def test_create_mcp_server_for_agent(self, mcp_server):
        """create_mcp_server creates server instance."""
        server = mcp_server.create_mcp_server("frontend-1")
        assert server is not None
        assert server.name == "arch-frontend-1"

    def test_get_or_create_returns_same_instance(self, mcp_server):
        """get_or_create_mcp_server returns the same instance for same agent."""
        server1 = mcp_server.get_or_create_mcp_server("test-agent")
        server2 = mcp_server.get_or_create_mcp_server("test-agent")

        assert server1 is server2

    def test_get_or_create_different_instances_per_agent(self, mcp_server):
        """get_or_create_mcp_server returns different instances for different agents."""
        server1 = mcp_server.get_or_create_mcp_server("agent-1")
        server2 = mcp_server.get_or_create_mcp_server("agent-2")

        assert server1 is not server2
        assert server1.name == "arch-agent-1"
        assert server2.name == "arch-agent-2"

    def test_servers_cached_in_dict(self, mcp_server):
        """MCP servers are cached in _mcp_servers dict."""
        assert len(mcp_server._mcp_servers) == 0

        mcp_server.get_or_create_mcp_server("cached-agent")

        assert "cached-agent" in mcp_server._mcp_servers

    def test_create_app_returns_starlette(self, mcp_server):
        """create_app returns a Starlette application."""
        app = mcp_server.create_app()
        assert isinstance(app, Starlette)


# --- Fixtures ---

@pytest.fixture
def state_store(tmp_path):
    """Create a StateStore with temporary directory."""
    return StateStore(tmp_path / "state")


@pytest.fixture
def mcp_server(state_store):
    """Create an MCPServer without GitHub."""
    return MCPServer(state=state_store, port=3999)


@pytest.fixture
def mcp_server_with_github(state_store):
    """Create an MCPServer with GitHub configured."""
    return MCPServer(
        state=state_store,
        port=3999,
        github_repo="test/repo"
    )


@pytest.fixture
def mcp_server_with_repo(state_store, tmp_path):
    """Create an MCPServer with a repo path."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    return MCPServer(
        state=state_store,
        port=3999,
        repo_path=repo_path
    )


@pytest.fixture
def mcp_server_with_brief(state_store, tmp_path):
    """Create an MCPServer with a BRIEF.md file."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    brief_content = """# BRIEF.md

## Goal
Build something great.

## Done When
- Tests pass
- Code is clean

## Constraints
- Python only

## Current Status
_Not started._

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
"""
    (repo_path / "BRIEF.md").write_text(brief_content)

    return MCPServer(
        state=state_store,
        port=3999,
        repo_path=repo_path
    )
