"""Tests for the 6 team coordination tools."""
import asyncio
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import config


@pytest.fixture(autouse=True)
def coordinator_mode():
    original = config.COORDINATOR_MODE
    config.COORDINATOR_MODE = True
    yield
    config.COORDINATOR_MODE = original


@pytest.fixture
def fresh_pool():
    from agent.team import tools as t
    from agent.team.worker_pool import WorkerPool
    t._POOL = WorkerPool()
    yield t._POOL


@pytest.mark.asyncio
async def test_worker_spawn_returns_id(fresh_pool):
    from agent.team.tools import worker_spawn
    with patch("agent.team.worker_pool._create_worker_llm") as mock_llm_factory:
        mock_llm = AsyncMock()
        r = MagicMock(); r.tool_calls = []; r.content = "done"
        mock_llm.ainvoke = AsyncMock(return_value=r)
        mock_llm.bind_tools = MagicMock(return_value=mock_llm)
        mock_llm_factory.return_value = mock_llm
        result = await worker_spawn.ainvoke({
            "prompt": "explore auth module",
            "role": "explorer",
            "description": "Auth explorer",
        })
    assert "spawned" in result.lower() or "worker" in result.lower()


@pytest.mark.asyncio
async def test_worker_message_unknown(fresh_pool):
    from agent.team.tools import worker_message
    result = await worker_message.ainvoke({
        "worker_id": "nonexistent-id",
        "message": "hello",
    })
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_worker_stop_unknown(fresh_pool):
    from agent.team.tools import worker_stop
    result = await worker_stop.ainvoke({"worker_id": "nonexistent-id"})
    assert "not found" in result.lower()


@pytest.mark.asyncio
async def test_team_status_empty(fresh_pool):
    from agent.team.tools import team_status
    result = await team_status.ainvoke({})
    assert "no workers" in result.lower() or "0" in result


@pytest.mark.asyncio
async def test_team_create_and_delete(fresh_pool):
    from agent.team.tools import team_create, team_delete
    r1 = await team_create.ainvoke({"name": "auth-team", "description": "Auth fix team"})
    assert "auth-team" in r1
    r2 = await team_delete.ainvoke({"name": "auth-team"})
    assert "auth-team" in r2 or "deleted" in r2.lower() or "no active" in r2.lower()
