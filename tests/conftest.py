"""Shared fixtures and async helpers for synapse-db-query tests.

Mirrors the pattern used by upjack's own test suite: an in-memory
`fastmcp.Client` drives the server, and async interactions are dispatched
through a tiny `_run()` helper so individual tests stay synchronous.
"""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import pytest


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


async def _list_tool_names(mcp) -> set[str]:
    from fastmcp import Client

    async with Client(mcp) as client:
        tools = await client.list_tools()
        return {t.name for t in tools}


async def _call_tool(mcp, name: str, arguments: dict | None = None) -> Any:
    from fastmcp import Client

    async with Client(mcp) as client:
        result = await client.call_tool(name, arguments or {})
        if not result.content:
            return None
        return json.loads(result.content[0].text)


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    """Build a fresh synapse-db-query MCP server bound to a tmp workspace.

    `synapse_db_query.server` constructs its `FastMCP` instance at module
    import time, so we set `UPJACK_ROOT` and `MPAK_WORKSPACE` to the test's
    `tmp_path` and reload the module to force a re-bind. `DATABASE_URL` is
    cleared — none of the custom-instructions tests touch the database, and
    a stale value from the dev shell would otherwise leak in.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("UPJACK_ROOT", str(workspace))
    monkeypatch.setenv("MPAK_WORKSPACE", str(workspace))
    monkeypatch.setenv("DATABASE_URL", "")

    import synapse_db_query.server as server_module

    importlib.reload(server_module)
    return server_module.mcp
