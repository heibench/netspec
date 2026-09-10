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

from kicad_netspec import mcp as netspec_mcp  # noqa: E402
from kicad_netspec.mcp import (  # noqa: E402
    _diagnose_import_failure,
    build_server,
    run_cli,
    tool_schema_size,
)

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


# -- what the entry point says when the import fails -------------------------------------

# Verbatim from mcp 2.1.1, where `mcp/server/fastmcp.py` is a stub that raises this.
# netspec used to discard it and blame the extra instead.
MCP_2X_MESSAGE = (
    "No module named 'mcp.server.fastmcp'. This is mcp 2.x, where FastMCP was renamed to "
    "MCPServer (from mcp.server.mcpserver import MCPServer) and other APIs changed; see the "
    "migration guide at "
    "https://py.sdk.modelcontextprotocol.io/v2/migration/#fastmcp-renamed-to-mcpserver or "
    "pin 'mcp<2' to keep running v1 code."
)


def test_an_absent_package_is_still_reported_as_a_missing_extra() -> None:
    """The message that was always right for the case it was written for."""
    message = _diagnose_import_failure(
        ModuleNotFoundError("No module named 'mcp'", name="mcp"), mcp_installed=False
    )
    assert "pip install 'kicad-netspec[mcp]'" in message


def test_a_moved_symbol_is_not_reported_as_a_missing_extra() -> None:
    """The extra is installed, so blaming it substitutes a guess for the answer (§2.3)."""
    message = _diagnose_import_failure(
        ModuleNotFoundError(MCP_2X_MESSAGE, name="mcp.server.fastmcp"), mcp_installed=True
    )
    assert "pip install" not in message
    assert MCP_2X_MESSAGE in message


def test_the_entry_point_prints_what_upstream_said_and_exits_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through `main()`, with `mcp` installed as it is in this environment.

    Exit 4 is the right outcome -- netspec could not run (D10). The reason has to be the
    real one.
    """

    def _raise_the_2x_error() -> object:
        raise ModuleNotFoundError(MCP_2X_MESSAGE, name="mcp.server.fastmcp")

    monkeypatch.setattr(netspec_mcp, "build_server", _raise_the_2x_error)

    assert netspec_mcp.main() == 4
    err = capsys.readouterr().err
    assert MCP_2X_MESSAGE in err
    assert "kicad-netspec[mcp]" not in err
