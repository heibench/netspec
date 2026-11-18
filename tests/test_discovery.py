"""How netspec chooses an engine, and when it refuses to choose a different one.

An engine named explicitly -- by argument or by ``$NETSPEC_KICAD_CLI`` -- is a
request for a *particular* oracle. Falling through to another one silently
(issue #17) meant a typo or a stale CI path produced a green run adjudicated
against an engine the caller never asked for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_netspec import cli
from kicad_netspec.oracle import KiCadNotFound
from kicad_netspec.oracle.discovery import ENV_VAR, find_kicad_cli


@pytest.fixture
def nothing_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every candidate fails to probe, so only the choice of failure differs."""
    monkeypatch.setattr("kicad_netspec.oracle.discovery._probe", lambda _argv: None)


def test_an_env_engine_that_does_not_work_fails_closed(
    nothing_works: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "/nonexistent/kicad-cli")
    with pytest.raises(KiCadNotFound) as exc:
        find_kicad_cli()
    assert "/nonexistent/kicad-cli" in str(exc.value), "the rejected path must be named"
    assert ENV_VAR in str(exc.value)
    assert "will not fall back" in str(exc.value)


def test_an_explicit_engine_that_does_not_work_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Another engine works, so only failing closed can stop it being used."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.setattr(
        "kicad_netspec.oracle.discovery._probe",
        lambda argv: None if "nonexistent" in " ".join(argv) else "10.0.6",
    )
    with pytest.raises(KiCadNotFound) as exc:
        find_kicad_cli(explicit="/nonexistent/kicad-cli")
    assert "/nonexistent/kicad-cli" in str(exc.value)
    assert "will not fall back" in str(exc.value)


def test_an_env_engine_is_never_silently_replaced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The bug: a working engine existed, so the bad override fell through to it."""
    monkeypatch.setenv(ENV_VAR, "/nonexistent/kicad-cli")
    monkeypatch.setattr(
        "kicad_netspec.oracle.discovery._probe",
        lambda argv: None if "nonexistent" in " ".join(argv) else "10.0.6",
    )
    with pytest.raises(KiCadNotFound):
        find_kicad_cli()


def test_a_working_env_engine_is_still_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against the check rejecting every override."""
    monkeypatch.setenv(ENV_VAR, "/somewhere/kicad-cli")
    monkeypatch.setattr("kicad_netspec.oracle.discovery._probe", lambda _argv: "10.0.6")
    found = find_kicad_cli()
    assert found.origin == "env"
    assert found.version == "10.0.6"


def _name_engine(how: str, value: str, monkeypatch: pytest.MonkeyPatch) -> str | None:
    """Name the engine by ``$NETSPEC_KICAD_CLI`` or by argument; return the argument."""
    if how == "env":
        monkeypatch.setenv(ENV_VAR, value)
        return None
    monkeypatch.delenv(ENV_VAR, raising=False)
    return value


@pytest.mark.parametrize("how", ["env", "explicit"])
def test_a_named_engine_under_a_path_with_spaces_is_one_command(
    how: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Issue #51: ``C:\\Program Files\\KiCad\\...`` is where Windows installs KiCad.

    A real stub is probed, so a split path fails to run rather than passing a fake probe.
    """
    stub = tmp_path / "Program Files" / "KiCad" / "kicad-cli"
    stub.parent.mkdir(parents=True)
    stub.write_text("#!/bin/sh\necho 10.0.8\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    found = find_kicad_cli(explicit=_name_engine(how, str(stub), monkeypatch))
    assert found.argv == (str(stub),)
    assert found.version == "10.0.8"
    assert found.origin == how


@pytest.mark.parametrize("how", ["env", "explicit"])
@pytest.mark.parametrize(
    ("value", "argv"),
    [
        (
            "flatpak run --command=kicad-cli org.kicad.KiCad",
            ("flatpak", "run", "--command=kicad-cli", "org.kicad.KiCad"),
        ),
        (
            "'/opt/my builds/kicad-cli' --some-flag",
            ("/opt/my builds/kicad-cli", "--some-flag"),
        ),
    ],
    ids=["arguments", "quoted-path"],
)
def test_a_named_command_line_is_split_like_a_shell(
    how: str, value: str, argv: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A value that is not a file is a command line; quoting keeps a spaced path whole."""
    monkeypatch.setattr("kicad_netspec.oracle.discovery._probe", lambda _argv: "10.0.6")
    found = find_kicad_cli(explicit=_name_engine(how, value, monkeypatch))
    assert found.argv == argv
    assert found.origin == how


@pytest.mark.parametrize("how", ["env", "explicit"])
def test_an_unparseable_named_command_line_is_an_environment_fault(
    how: str, nothing_works: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unbalanced quote is a bad override, not a traceback at exit 1."""
    with pytest.raises(KiCadNotFound) as exc:
        find_kicad_cli(explicit=_name_engine(how, "'/opt/kicad-cli", monkeypatch))
    assert "'/opt/kicad-cli" in str(exc.value)


def test_unnamed_candidates_still_fall_through(
    nothing_works: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no override, discovery keeps probing and reports everything it tried."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(KiCadNotFound) as exc:
        find_kicad_cli()
    assert "will not fall back" not in str(exc.value), "this is the fall-through path"
    assert "Tried:" in str(exc.value)


def test_the_cli_reports_a_bad_override_as_an_environment_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit 4, not a green run against the engine the caller did not ask for.

    A working engine is available here, so before the fix this exited 0 having
    used it -- indistinguishable from a run that honoured the override.
    """
    monkeypatch.setenv(ENV_VAR, "/nonexistent/kicad-cli")
    monkeypatch.setattr(
        "kicad_netspec.oracle.discovery._probe",
        lambda argv: None if "nonexistent" in " ".join(argv) else "10.0.6",
    )
    monkeypatch.setattr(
        "kicad_netspec.cli.Cli10Backend",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not reach a backend")),
    )
    assert cli.main(["doctor"]) == cli.EXIT_ENVIRONMENT
