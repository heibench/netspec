"""How netspec chooses an engine, and when it refuses to choose a different one.

An engine named explicitly -- by argument or by ``$NETSPEC_KICAD_CLI`` -- is a
request for a *particular* oracle. Falling through to another one silently
(issue #17) meant a typo or a stale CI path produced a green run adjudicated
against an engine the caller never asked for.
"""

from __future__ import annotations

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
