"""Custom-instructions contract tests.

Verifies the bundle's half of NimbleBrain's `app://instructions` convention:
the resource returns saved text (or empty), the tool writes it, and the
8 KiB cap surfaces as a tool error rather than a thrown exception.
"""

from __future__ import annotations

from fastmcp import Client

from .conftest import _call_tool, _run


async def _read_resource(mcp, uri: str) -> str:
    async with Client(mcp) as client:
        contents = await client.read_resource(uri)
        first = contents[0]
        text = getattr(first, "text", None)
        return text if text is not None else ""


def test_app_instructions_empty_by_default(mcp) -> None:
    body = _run(_read_resource(mcp, "app://instructions"))
    assert body == ""


def test_set_custom_instructions_round_trips_via_app_uri(mcp) -> None:
    saved = _run(
        _call_tool(
            mcp,
            "set_custom_instructions",
            {"text": "Default time window is the last 90 days."},
        )
    )
    assert saved == {"status": "saved"}

    body = _run(_read_resource(mcp, "app://instructions"))
    assert body == "Default time window is the last 90 days."


def test_set_custom_instructions_writes_to_workspace_root(mcp, tmp_path) -> None:
    _run(
        _call_tool(
            mcp,
            "set_custom_instructions",
            {"text": "Treat deleted_at IS NOT NULL as removed."},
        )
    )
    expected = tmp_path / "workspace" / "custom-instructions.md"
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == "Treat deleted_at IS NOT NULL as removed."


def test_empty_text_clears_existing_instructions(mcp, tmp_path) -> None:
    _run(_call_tool(mcp, "set_custom_instructions", {"text": "first body"}))
    path = tmp_path / "workspace" / "custom-instructions.md"
    assert path.exists()

    cleared = _run(_call_tool(mcp, "set_custom_instructions", {"text": ""}))
    assert cleared == {"status": "cleared"}
    assert not path.exists()
    assert _run(_read_resource(mcp, "app://instructions")) == ""


def test_eight_kib_cap_surfaces_as_structured_error(mcp) -> None:
    huge = "x" * (8 * 1024 + 1)
    result = _run(_call_tool(mcp, "set_custom_instructions", {"text": huge}))
    # Tool returns a structured `{status: "error", error: ...}` rather than
    # throwing — the cap message names the byte limit so the agent (and any
    # caller surfacing this back to the user) can explain what went wrong.
    assert result["status"] == "error"
    assert "8192" in result["error"]


def test_eight_kib_exact_is_accepted(mcp) -> None:
    body = "x" * (8 * 1024)
    saved = _run(_call_tool(mcp, "set_custom_instructions", {"text": body}))
    assert saved == {"status": "saved"}
    assert _run(_read_resource(mcp, "app://instructions")) == body


def test_settings_panel_resource_is_published(mcp) -> None:
    """`/settings/apps/synapse-db-query` reads `ui://db-query/settings`."""
    body = _run(_read_resource(mcp, "ui://db-query/settings"))
    assert "<title>DB Query Settings</title>" in body
    assert "set_custom_instructions" in body
    assert "app://instructions" in body


def test_settings_panel_inspects_structured_error_status(mcp) -> None:
    """Save handler must branch on `result.structuredContent.status === "error"`.

    Regression guard for the silently-swallowed cap-error bug: the server-side
    tool returns a structured `{status: "error", ...}` rather than throwing, so
    a click handler that only `await`s the tool call without inspecting the
    payload would render "Saved" while nothing was written. The HTML below
    proves the branch is in place; if anyone deletes it, this test fails before
    the broken UI ships.
    """
    body = _run(_read_resource(mcp, "ui://db-query/settings"))
    assert "structuredContent" in body, "save handler must read structuredContent"
    assert 'status === "error"' in body, "save handler must branch on the error status"
