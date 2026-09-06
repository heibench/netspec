"""Find ``kicad-cli``, wherever this machine happens to keep it.

This is not boilerplate. The best-engineered KiCad MCP server in the field fails against
a Flatpak KiCad -- ``Failed to spawn kicad-cli: kicad-cli`` -- and Flatpak is what KiCad
recommends on Linux. A tool that cannot find the engine reports an environment fault, and
an environment fault must never be mistaken for a broken design (DECISIONS D10).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kicad_netspec.oracle.base import EnvironmentError_

__all__ = ["KiCadNotFound", "KiCadCli", "find_kicad_cli"]

ENV_VAR = "NETSPEC_KICAD_CLI"
"""Explicit override. Accepts a bare command or an absolute path."""

FLATPAK_APP = "org.kicad.KiCad"

_MACOS_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/Applications/KiCad/kicad-cli",
)
_WINDOWS_GLOBS = (
    r"C:\Program Files\KiCad\*\bin\kicad-cli.exe",
    r"C:\Program Files (x86)\KiCad\*\bin\kicad-cli.exe",
)


class KiCadNotFound(EnvironmentError_):
    """No usable ``kicad-cli`` on this machine.

    Always an environment fault, never a statement about a design.

    A subclass of :class:`EnvironmentError_` so that ``main``'s handler converts it to
    exit 4 wherever discovery is reached, including direct callers like ``doctor`` that
    do not wrap it themselves.  As a sibling it escaped that handler and surfaced as an
    unhandled traceback at exit 1 -- which is ``EXIT_VIOLATION``, the one code that says
    the design is wrong.
    """


@dataclass(frozen=True)
class KiCadCli:
    """A resolved way to invoke ``kicad-cli``."""

    argv: tuple[str, ...]
    """Command prefix. Arguments are appended to this."""

    version: str
    """Version string as reported by ``--version``, e.g. ``10.0.5``."""

    origin: str
    """How it was found -- ``env``, ``path``, ``flatpak``, ``macos``, ``windows``."""

    @property
    def sandboxed(self) -> bool:
        """True when the engine runs in a sandbox that has its own filesystem view.

        Flatpak KiCad has a private ``/tmp``, so an output path under ``/tmp`` is
        written where the caller cannot see it. Callers must keep paths under ``$HOME``.

        Detected from the command itself rather than from how it was discovered: an
        explicit ``NETSPEC_KICAD_CLI="flatpak run ..."`` is just as sandboxed as one
        found by probing, and keying off the label silently loses that.
        """
        return self.origin == "flatpak" or any("flatpak" in part for part in self.argv)

    def __str__(self) -> str:
        return f"kicad-cli {self.version} ({self.origin})"


def find_kicad_cli(*, explicit: str | None = None) -> KiCadCli:
    """Resolve ``kicad-cli``, first hit wins.

    Order: explicit argument, ``$NETSPEC_KICAD_CLI``, ``PATH``, Flatpak, macOS bundle,
    Windows install. Raises :class:`KiCadNotFound` listing everything tried.
    """
    tried: list[str] = []

    for argv, origin in _candidates(explicit):
        probed = _probe(argv)
        if probed is not None:
            return KiCadCli(argv=tuple(argv), version=probed, origin=origin)
        tried.append(f"{origin}: {' '.join(argv)}")

    raise KiCadNotFound(
        "no working kicad-cli found. Set "
        f"{ENV_VAR}=/path/to/kicad-cli, or install KiCad. Tried:\n  " + "\n  ".join(tried)
    )


def _candidates(explicit: str | None) -> list[tuple[list[str], str]]:
    out: list[tuple[list[str], str]] = []

    for value, origin in ((explicit, "explicit"), (os.environ.get(ENV_VAR), "env")):
        if value:
            out.append((value.split() if " " in value else [value], origin))

    on_path = shutil.which("kicad-cli")
    if on_path:
        out.append(([on_path], "path"))

    if shutil.which("flatpak"):
        out.append((["flatpak", "run", "--command=kicad-cli", FLATPAK_APP], "flatpak"))

    out.extend(([c], "macos") for c in _MACOS_CANDIDATES if Path(c).exists())

    for pattern in _WINDOWS_GLOBS:
        root, _, tail = pattern.partition("*")
        base = Path(root)
        if base.is_dir():
            out.extend(
                ([str(match)], "windows")
                for match in sorted(base.glob("*" + tail.replace("\\", "/")), reverse=True)
            )

    return out


def _probe(argv: list[str]) -> str | None:
    """Return the version string if this command is a working ``kicad-cli``."""
    try:
        done = subprocess.run(  # noqa: S603 - argv is built here, never from user text
            [*argv, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    version = done.stdout.strip().splitlines()
    return version[0].strip() if version else None
