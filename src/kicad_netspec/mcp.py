"""MCP server over the same primitives as the CLI.

D5 deferred this until the CLI had real use; D18 fixes what it may be: **stateless verbs
over a design on disk**. Every call re-reads through KiCad and returns what the CLI
returns. Nothing is held between calls.

Each call runs the CLI in a subprocess rather than calling in-process, for two reasons
that are load-bearing rather than stylistic:

- ``check`` imports a contract module, and ``sys.modules`` caches it. A long-lived
  process re-checking an edited contract could adjudicate against the *previous*
  version while reporting on the new one — a stale answer presented as fresh, which is
  the exact failure this project exists to catch. A process per call makes it impossible.
- The exit code is part of the contract: ``0`` clean, ``1`` a finding about the design,
  ``2`` could not evaluate, ``4`` an environment fault, ``64`` usage. A subprocess
  returns the CLI's own, unlaundered.

**``guard`` is deliberately not exposed** (D18). It runs an arbitrary command, and an
agent driving this server can already run commands; handing it a shell through a
verification tool would add risk and no capability. An agent should run its own edit and
then call ``diff``.

The ``mcp`` dependency is an extra, imported lazily inside :func:`build_server`, so
``import kicad_netspec`` stays dependency-free and this module is imported only by the
``netspec-mcp`` entry point and its tests.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.mcpserver import MCPServer

__all__ = ["build_server", "main", "run_cli"]

_TIMEOUT = 600


def run_cli(*args: str) -> dict[str, Any]:
    """Run the netspec CLI once and return its result verbatim.

    The exit code is reported, not interpreted: a caller has to be able to tell "your
    board has a problem" (1) from "I could not look" (4).
    """
    argv = [sys.executable, "-m", "kicad_netspec.cli", *args]
    try:
        done = subprocess.run(  # noqa: S603 - argv built here, never from caller text
            argv, capture_output=True, text=True, timeout=_TIMEOUT, check=False
        )
    except subprocess.TimeoutExpired:
        return {"exit_code": 4, "output": "", "error": f"netspec timed out after {_TIMEOUT}s"}
    except OSError as exc:
        return {"exit_code": 4, "output": "", "error": str(exc)}

    return {
        "exit_code": done.returncode,
        "output": done.stdout.rstrip(),
        "error": done.stderr.rstrip(),
        "meaning": _MEANING.get(done.returncode, "unexpected exit code"),
    }


def _with_report(result: dict[str, Any]) -> dict[str, Any]:
    """Replace the printed text with the parsed document, when there is a real one.

    Validated rather than merely parsed: the document has to identify itself as netspec's
    own. That stops a stray ``print`` in a contract from being read as a verdict. It does
    **not** make the report trustworthy independently of the contract -- see the note on
    ``check`` below, and D23.

    An environment fault (exit 4) prints a message, not a report, and the raw output is
    kept -- an agent must still be able to read why netspec could not look (D10). Either
    way ``report_unavailable`` says so explicitly, because a silently missing key is
    indistinguishable from a key an agent forgot to read.
    """
    text = result.get("output") or ""
    without_text = {k: v for k, v in result.items() if k != "output"}

    if result.get("exit_code") == 4:
        # Exit 4 means netspec could not look. Whatever is on stdout is not its verdict,
        # and returning one anyway contradicted this function's own promise.
        return {**result, "report_unavailable": "netspec could not run; see error"}

    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        reason = "netspec printed no report" if not text.strip() else "output was not JSON"
        return {**result, "report_unavailable": reason}

    if not isinstance(parsed, dict) or parsed.get("command") != "check":
        return {**result, "report_unavailable": "output was JSON, but not a netspec report"}
    if not isinstance(parsed.get("schema"), int):
        return {**result, "report_unavailable": "report carries no schema version"}

    return {**without_text, "report": parsed}


_MEANING = {
    0: "clean",
    1: "a finding about the design",
    2: "netspec could not evaluate part of the contract; this is not a finding",
    64: "usage error",
    4: "environment fault -- KiCad could not be run; this says nothing about the design",
}


def build_server() -> MCPServer:
    """Construct the MCP server. Imports `mcp` lazily; see the module docstring."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(
        "netspec",
        instructions=(
            "Verify PCB connectivity against declared intent, using KiCad as the oracle. "
            "Every tool re-reads the design from disk. Exit code 1 is a finding about the "
            "design; 4 means KiCad could not be run and says nothing about the design. "
            "netspec never modifies a design file."
        ),
    )

    @server.tool()
    def doctor() -> dict[str, Any]:
        """Report which KiCad engine was found and what it can do."""
        return run_cli("doctor")

    @server.tool()
    def netlist(schematic: str, include_unconnected: bool = False) -> dict[str, Any]:
        """Read what KiCad says is connected in a schematic.

        Args:
            schematic: path to a .kicad_sch
            include_unconnected: also list pins connected to nothing
        """
        args = ["netlist", schematic]
        if include_unconnected:
            args.append("--all")
        return run_cli(*args)

    @server.tool()
    def snapshot(schematic: str, output: str) -> dict[str, Any]:
        """Record a schematic's connectivity as stable JSON, to compare against later.

        Args:
            schematic: path to a .kicad_sch
            output: path to write the snapshot to
        """
        return run_cli("snap", schematic, "-o", output)

    @server.tool()
    def diff(before: str, after: str) -> dict[str, Any]:
        """Compare two readings of a design and report what changed.

        Reports net changes, floating pins, component changes, and separately any pin
        swap that reverses a polarised part -- which ERC does not check, because
        reversing one is legal wiring.

        Args:
            before: a snapshot .json, or a .kicad_sch
            after: a snapshot .json, or a .kicad_sch
        """
        return run_cli("diff", before, after)

    @server.tool()
    def check(contract: str) -> dict[str, Any]:
        """Adjudicate a Python contract against the design it names.

        Executes the contract module. Statuses are pass, fail, unsupported and skipped;
        only pass is green, and a skipped rule was not evaluated rather than satisfied.

        Returns the structured report, not the printed text: every result carries the
        rule as fields plus a stable id, so two runs can be compared to see whether an
        assertion was removed or quietly weakened.

        **The report is exactly as trustworthy as the contract.** A contract is executed
        Python (D8), so it runs before the design is read and can do anything this
        process can -- including replacing the oracle, after which netspec emits a
        genuine report about a board the contract invented. Treat a contract as you would
        any executable you are about to run; do not treat this report as evidence about a
        design whose contract you have not read.

        Args:
            contract: path to a contract module, optionally 'file.py:name'
        """
        result = run_cli("check", contract, "--format", "json")
        return _with_report(result)

    @server.tool()
    def gate(design: str, fail_on: str = "error") -> dict[str, Any]:
        """Run KiCad's own ERC or DRC at every severity.

        Always runs at --severity-all: at KiCad's defaults a schematic-parity check can
        report zero problems on a board carrying many, because the relevant rules
        default to warning.

        Args:
            design: path to a .kicad_sch (ERC) or .kicad_pcb (DRC with schematic parity)
            fail_on: which severity fails -- error, warning, or any
        """
        return run_cli("gate", design, "--fail-on", fail_on)

    return server


_EXTRA_MISSING = "netspec-mcp needs the mcp extra: pip install 'kicad-netspec[mcp]'"


def _diagnose_import_failure(exc: ImportError, *, mcp_installed: bool) -> str:
    """Say which import failure this was, without inventing the other one.

    Two different failures arrive at the same `except ImportError`, and only one of them
    is a missing dependency. The case that made this function necessary was a user on
    `mcp` 2.x while netspec imported `mcp.server.fastmcp`: upstream ships that path as a
    stub raising `ModuleNotFoundError` -- a subclass of `ImportError` -- naming the
    rename, the new import path and the migration guide. Answering that with "install
    the extra" tells a user to install what they already have: a plausible cause
    substituted for the real one, which the org contract's §2.3 forbids.

    netspec now imports `mcp.server.mcpserver` and requires 2.x, so the *direction* has
    flipped: the version-mismatch failure a user hits today is a plain "No module named
    'mcp.server.mcpserver'" from an `mcp` 1.x install, which carries no migration advice
    of its own. This function's behaviour is unchanged and still right for it -- the
    package is present, so the extra is not blamed, and the import's own words are passed
    through rather than a guess about which version is installed.

    So the extra is blamed only when the `mcp` package is genuinely absent. Otherwise the
    import's own words are passed through -- netspec knows that the import failed and not
    why, and upstream does know.

    **The second message claims only what was measured, which is that the `mcp` package
    itself is there.** An earlier version went further and said "this is not a missing
    dependency", which is a categorical claim about the whole dependency tree from a
    lookup of one name in it -- and it is false for an incomplete install, where the
    import names a transitive dependency and the message denies one in the same breath.
    Substituting a plausible cause for the one that was established is the defect this
    function exists to fix; it does not get an exemption for being netspec's own.

    Either way the caller still exits 4: netspec could not run, which says nothing about
    any design (D10). It is the reason that was fabricated, not the outcome.
    """
    if not mcp_installed:
        return _EXTRA_MISSING
    return (
        "netspec-mcp could not import the MCP server API. The mcp package itself is "
        "installed, so the extra is not absent; netspec does not know more than that. "
        "The import said:\n\n"
        f"    {type(exc).__name__}: {exc}"
    )


def main() -> int:
    """Entry point for `netspec-mcp`."""
    try:
        server = build_server()
    except ImportError as exc:
        installed = importlib.util.find_spec("mcp") is not None
        print(_diagnose_import_failure(exc, mcp_installed=installed), file=sys.stderr)
        return 4
    server.run()
    return 0


def tool_schema_size() -> dict[str, Any]:
    """Measure the context cost of this server's tool list.

    Exposing tools is not free: a survey of KiCad MCP servers found one spending 48,000
    tokens of schema before an agent reads a single file. This is asserted in CI.
    """
    import asyncio

    # `list_tools` is still `async def` on mcp 2.x. Its signature READS synchronous --
    # `(self) -> list[MCPTool]`, because the annotation is the awaited type -- so
    # `inspect.signature` is not the way to check, and dropping the `asyncio.run` on
    # that basis made `len(tools)` raise on a coroutine object. `iscoroutinefunction`
    # is what answers it.
    server = build_server()
    tools = asyncio.run(server.list_tools())
    # `by_alias`, because the number that matters is what goes over the wire. On mcp
    # 2.x the Python attribute is `input_schema` and the serialised key is still the
    # protocol's `inputSchema`; hand-building the dict from attribute names measured
    # netspec's rendering of the tool list rather than the tool list, and broke
    # outright when the attribute was renamed.
    payload = [tool.model_dump(by_alias=True, exclude_none=True) for tool in tools]
    text = json.dumps(payload)
    return {"tools": len(tools), "bytes": len(text), "approx_tokens": len(text) // 4}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
