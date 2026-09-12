"""The MCP surface: shape, budget, and what it deliberately does not expose."""

from __future__ import annotations

import asyncio
import json
import os

import pytest

# Skipping is right for a contributor without the extra, and wrong for CI: this whole
# module skipped silently there once, so the MCP surface had no coverage while the run
# reported green. In CI a missing extra is a failure, not a skip.
if os.environ.get("CI"):
    import mcp  # noqa: F401  -- fail loudly if the extra is not installed
else:
    pytest.importorskip("mcp", reason="needs the mcp extra; run `just setup`")

from kicad_netspec.mcp import build_server, run_cli, tool_schema_size  # noqa: E402

# A survey of KiCad MCP servers found tool lists from 2,574 to 48,627 tokens -- the
# largest spending a quarter of a 200K window before the agent reads a file. Exposing
# tools is a cost, and this is the number that keeps it honest.
TOKEN_BUDGET = 3000


def _tools():
    return asyncio.run(build_server().list_tools())


def test_the_tool_list_stays_inside_its_context_budget() -> None:
    size = tool_schema_size()
    assert size["approx_tokens"] < TOKEN_BUDGET, (
        f"{size['tools']} tools now cost ~{size['approx_tokens']} tokens; "
        "adding surface is a real cost to every agent that connects"
    )
    # A ceiling alone is one-directional: a payload that lost the schemas measures
    # smaller and passes. That is the shape this whole project refuses -- a wrong
    # number reported as a success -- and it was reachable, because the attribute
    # holding the schema was renamed between mcp majors.
    assert size["tools"] == len(_tools()), "the measurement counted a different tool set"
    assert size["approx_tokens"] > 100, (
        f"~{size['approx_tokens']} tokens for {size['tools']} tools is too small to be "
        "the real payload; the measurement has probably stopped including the schemas"
    )


def test_the_measured_payload_is_the_one_that_goes_over_the_wire() -> None:
    """The budget measures the protocol's bytes, not netspec's rendering of them.

    `inputSchema` is the wire key; on mcp 2.x the Python attribute behind it is
    `input_schema`. Hand-building the dict from attribute names measured our own
    rendering and broke outright at the rename, which is how this was noticed.
    """
    tools = _tools()
    assert tools, "no tools, so this asserts nothing"
    wire = tools[0].model_dump(by_alias=True, exclude_none=True)
    assert "inputSchema" in wire, (
        "the serialised tool no longer carries `inputSchema`; the budget is measuring "
        f"something other than the protocol payload: {sorted(wire)}"
    )


def test_the_expected_verbs_are_present() -> None:
    assert {t.name for t in _tools()} == {
        "doctor",
        "netlist",
        "snapshot",
        "diff",
        "check",
        "gate",
    }


def test_guard_is_not_exposed() -> None:
    """D18: `guard` runs an arbitrary command.

    An agent driving this server can already run commands; handing it a shell through a
    verification tool adds risk and no capability.
    """
    assert "guard" not in {t.name for t in _tools()}


def test_every_tool_documents_itself() -> None:
    """The description is what an agent reads to choose. An empty one is a bug."""
    for tool in _tools():
        assert tool.description and len(tool.description) > 30, tool.name


def test_no_tool_offers_to_modify_a_design() -> None:
    """netspec never writes to a design file (D2); the surface must not imply otherwise."""
    banned = ("write", "edit", "modify", "place", "route", "delete")
    for tool in _tools():
        assert not any(word in tool.name.lower() for word in banned), tool.name


# -- the subprocess boundary ------------------------------------------------------------


def test_run_cli_reports_the_exit_code_and_its_meaning() -> None:
    result = run_cli("--version")
    assert result["exit_code"] == 0
    assert "netspec" in result["output"]
    assert result["meaning"] == "clean"


def test_an_environment_fault_is_labelled_as_one() -> None:
    """Exit 4 must be legible as 'I could not look', not 'your board is broken' (D10)."""
    result = run_cli("netlist", "/nonexistent/board.kicad_sch")
    assert result["exit_code"] == 4
    assert "says nothing about the design" in result["meaning"]


def test_a_usage_error_is_not_a_finding() -> None:
    result = run_cli("check", "/nonexistent/contract.py")
    assert result["exit_code"] in (2, 4)
    assert result["meaning"] != "a finding about the design"


def test_results_are_json_serialisable() -> None:
    """Whatever a tool returns has to survive the wire."""
    json.dumps(run_cli("--version"))
