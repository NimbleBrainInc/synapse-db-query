"""present_result + tools-list contract tests.

Locks the v0.4.0 statelessness change:

  - `get_last_result` is no longer registered (PR #4 breaking removal).
  - `present_result` writes a `query` entity that `list_queries` returns.
  - An entity-store write failure surfaces to the caller — the previous
    implementation swallowed the exception and returned a payload anyway,
    silently dropping the user's answer.
"""

from __future__ import annotations

import pytest
from fastmcp import Client

from .conftest import _call_tool, _list_tool_names, _run


def test_get_last_result_is_not_registered(mcp) -> None:
    """The tool was removed in v0.4.0; it must not reappear in tools/list."""
    names = _run(_list_tool_names(mcp))
    assert "get_last_result" not in names
    # Sanity — present_result should still be there.
    assert "present_result" in names


def test_present_result_persists_a_query_entity(mcp) -> None:
    payload = _run(
        _call_tool(
            mcp,
            "present_result",
            {
                "sql": "SELECT 1 AS n",
                "columns": ["n"],
                "rows": [{"n": 1}],
                "question": "what is one",
            },
        )
    )
    assert isinstance(payload["id"], str)
    assert payload["id"].startswith("qy_")
    assert isinstance(payload["created_at"], str)
    assert payload["row_count"] == 1
    assert payload["question"] == "what is one"


def test_present_result_is_visible_via_list_queries(mcp) -> None:
    """The widget renders by reading list_queries — verify the round-trip."""
    presented = _run(
        _call_tool(
            mcp,
            "present_result",
            {"sql": "SELECT 2", "columns": ["n"], "rows": [{"n": 2}]},
        )
    )
    listed = _run(_call_tool(mcp, "list_queries", {}))
    entities = listed["entities"] if isinstance(listed, dict) else listed
    assert any(e["id"] == presented["id"] for e in entities), (
        "present_result entity must be findable via list_queries — "
        "this is the contract the widget depends on"
    )


def test_present_result_propagates_entity_write_failure(mcp, monkeypatch) -> None:
    """Regression guard for the deliberate fail-loud change in v0.4.0.

    Previously `present_result` wrapped `_app.create_entity` in try/except
    and returned the payload anyway, hiding write failures from the agent.
    The user would see "answer presented" while nothing was actually stored
    or rendered. The fix removed the swallow; this test locks it.
    """
    import synapse_db_query.server as server_module

    def boom(name: str, payload: dict) -> dict:
        raise RuntimeError("entity store on fire")

    monkeypatch.setattr(server_module._app, "create_entity", boom)

    async def _call() -> object:
        async with Client(mcp) as client:
            return await client.call_tool(
                "present_result",
                {"sql": "SELECT 1", "columns": ["n"], "rows": [{"n": 1}]},
            )

    # FastMCP's in-process client re-raises tool exceptions directly; over a
    # transport it would surface as `CallToolResult.is_error=True`. Either is
    # acceptable — what matters is the failure isn't silently swallowed.
    with pytest.raises(Exception, match="entity store on fire"):
        _run(_call())
