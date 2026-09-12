"""What `netspec-mcp` says when it cannot import the MCP server API.

Deliberately not behind the `mcp` extra. Three failures reach one `except ImportError`
and only one of them is an absent extra, so what separates them is the thing worth
pinning -- and none of these tests needs `mcp` to be installed to pin it. The MCP
surface's own tests are gated on the extra in ``test_mcp.py``; gating this file too would
take the §2.3 regression tests with it on any machine without it.
"""

from __future__ import annotations

import importlib.util

import pytest

from kicad_netspec import mcp as netspec_mcp
from kicad_netspec.mcp import _diagnose_import_failure

# Verbatim from mcp 2.1.1, where `mcp/server/fastmcp.py` is a stub that raises this.
# netspec used to discard it and blame the extra instead.
#
# Kept although netspec now imports `mcp.server.mcpserver` and requires 2.x: the
# property under test is "an import that moved is not an absent extra", which is what
# the function decides, and a message this specific is the case that proved it wrong
# once. The version-mismatch a user hits TODAY is the opposite direction and is pinned
# below -- upstream 1.x offers no migration text, so the two are not interchangeable.
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


#: What an `mcp` 1.x install answers now that netspec imports the 2.x path. Plain, with
#: no migration advice of its own -- which is the point: there is nothing to pass through
#: except upstream's own words, and inventing the advice would be §2.3 again.
MCP_1X_MESSAGE = "No module named 'mcp.server.mcpserver'"


def test_a_version_that_lacks_the_module_is_not_reported_as_a_missing_extra() -> None:
    """The direction netspec faces after the 2.x port, and the one users now hit.

    `mcp` 1.x resolves, so the extra is installed and blaming it would tell someone to
    install what they have. The import said only that the module is absent, and that is
    all netspec may repeat -- it does not know a version was the cause.
    """
    message = _diagnose_import_failure(
        ModuleNotFoundError(MCP_1X_MESSAGE, name="mcp.server.mcpserver"), mcp_installed=True
    )
    assert "pip install" not in message
    assert MCP_1X_MESSAGE in message
    assert "1.x" not in message and "2.x" not in message, (
        "netspec named a version as the cause; find_spec established that `mcp` "
        f"resolves and nothing more: {message}"
    )


def test_an_incomplete_install_is_not_told_that_nothing_is_missing() -> None:
    """Reproduced with mcp 1.30.0 and `pydantic-settings` uninstalled.

    `find_spec("mcp")` establishes that one name resolves. It says nothing about the rest
    of the tree, so a message that denies a missing dependency and then prints one is the
    same substitution this fix is about. It is also the wrong steer: reinstalling the
    extra re-resolves the missing transitive dependency and repairs this install, and a
    denial argues against the one thing that works.
    """
    message = _diagnose_import_failure(
        ModuleNotFoundError("No module named 'pydantic_settings'", name="pydantic_settings"),
        mcp_installed=True,
    )
    assert "not a missing dependency" not in message
    assert "No module named 'pydantic_settings'" in message


@pytest.mark.skipif(
    importlib.util.find_spec("mcp") is None,
    reason="reads the real environment through find_spec; needs the mcp extra",
)
def test_the_entry_point_prints_what_upstream_said_and_exits_4(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """End to end through `main()`, against the real `find_spec` rather than an argument.

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
