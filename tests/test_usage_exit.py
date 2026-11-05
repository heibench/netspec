"""A usage error exits EXIT_USAGE (64), not argparse's 2 (#37).

argparse calls `sys.exit(2)` on an unrecognised flag, a missing argument or a bad
choice, and 2 is EXIT_INCOMPLETE -- "netspec could not decide" (A1). `main()` now
remaps that SystemExit to 64, as partspec does.

`EXIT_USAGE` was declared 64 while a bad flag exited 2, so a test of the constant
passed throughout. These measure a real process running the console-script entry
point, and pin what must *not* move: `--help` and `--version` stay 0, any other
SystemExit code passes through, and a handler's own 2 (`incomplete`) stays 2.
"""

from __future__ import annotations

import argparse
import subprocess
import sys

import pytest

from kicad_netspec import cli


def _exit(*args: str) -> int:
    """Exit status of `netspec <args>`, run exactly as the console script runs it."""
    proc = subprocess.run(
        [sys.executable, "-c", "import sys; from kicad_netspec.cli import main; sys.exit(main())"]
        + list(args),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["--bogus-flag"], id="unknown-flag"),
        pytest.param(["check"], id="missing-argument"),
        pytest.param(["gate", "board.kicad_pcb", "--fail-on", "nope"], id="bad-choice"),
        pytest.param(["no-such-verb"], id="unknown-verb"),
        pytest.param([], id="no-arguments"),
    ],
)
def test_a_usage_error_exits_64(args: list[str]) -> None:
    assert _exit(*args) == cli.EXIT_USAGE


def test_the_mcp_path_reports_a_usage_error_as_usage() -> None:
    """mcp.py runs `python -m kicad_netspec.cli` and names the code it gets back."""
    from kicad_netspec.mcp import _MEANING

    proc = subprocess.run(
        [sys.executable, "-m", "kicad_netspec.cli", "--bogus-flag"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert _MEANING[proc.returncode] == "usage error"


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["--help"], id="help"),
        pytest.param(["check", "--help"], id="command-help"),
        pytest.param(["--version"], id="version"),
    ],
)
def test_help_and_version_still_exit_0(args: list[str]) -> None:
    """argparse exits 0 for these; a successful run must not become 64."""
    assert _exit(*args) == cli.EXIT_OK


def test_other_systemexit_codes_pass_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only argparse's 2 is a usage error. Any other code it raises is left alone."""

    class _Parser(argparse.ArgumentParser):
        def parse_args(self, *_a: object, **_k: object) -> argparse.Namespace:  # type: ignore[override]
            raise SystemExit(5)

    monkeypatch.setattr(cli, "_parser", lambda: _Parser())
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["doctor"])
    assert excinfo.value.code == 5


def test_a_handlers_own_incomplete_is_not_remapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """The remap is for argparse, not for every 2: `incomplete` comes from a handler
    after parsing succeeded, and must reach the caller as 2."""
    monkeypatch.setattr(cli, "_doctor", lambda _args: cli.EXIT_INCOMPLETE)
    assert cli.main(["doctor"]) == cli.EXIT_INCOMPLETE
