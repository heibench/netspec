"""Guards on the release path.

The release workflow publishes without running the test suite, so what protects PyPI is
the artifact checks in that workflow plus the invariants here. A workflow cannot test
itself: grepping proves a string is present, not that a gate blocks anything.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
GATE = ROOT / "scripts" / "assert_tag_on_main.sh"


def _pyproject() -> dict:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


def test_the_package_version_matches_the_module() -> None:
    """A tag is checked against pyproject; the module must agree or --version lies."""
    from kicad_netspec import __version__

    assert _pyproject()["project"]["version"] == __version__


def test_the_core_declares_no_runtime_dependencies() -> None:
    """The release smoke test asserts this against the built wheel; assert the claim."""
    assert _pyproject()["project"]["dependencies"] == []


def test_the_mcp_extra_pins_below_version_2() -> None:
    """mcp 2.x renamed FastMCP and killed nine servers in the survey behind this project."""
    extras = _pyproject()["project"]["optional-dependencies"]["mcp"]
    assert any("<2" in spec for spec in extras), extras


# -- the Trusted Publishing binding ----------------------------------------------------


def test_the_workflow_filename_and_environment_match_the_registration() -> None:
    """PyPI's publisher names this file and this environment. Renaming breaks releases
    silently -- the workflow runs and the upload is rejected."""
    assert RELEASE.name == "release.yml"
    assert "environment: pypi" in RELEASE.read_text()


def test_every_action_in_the_release_workflow_is_pinned_to_an_exact_ref() -> None:
    """A floating major is mutable: its contents can change between releases.

    The publish step holds this project's OIDC identity for PyPI, which is exactly the
    thing not to leave on a moving ref.
    """
    floating = re.findall(r"uses:\s*(\S+@v\d+)\s*$", RELEASE.read_text(), re.MULTILINE)
    assert not floating, f"pin these to an exact version or SHA: {floating}"


# -- the ancestry gate, exercised rather than grepped ----------------------------------


def _repo(tmp_path: Path) -> Path:
    run = lambda *a: subprocess.run(a, cwd=tmp_path, check=True, capture_output=True)  # noqa: E731
    run("git", "init", "-q", "-b", "main")
    run(
        "git",
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "one",
    )
    return tmp_path


def _gate(cwd: Path, commit: str, ref: str) -> int:
    return subprocess.run([str(GATE), commit, ref], cwd=cwd, capture_output=True).returncode


def test_the_gate_allows_a_commit_on_main(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    assert _gate(repo, head.stdout.strip(), "main") == 0


def test_the_gate_blocks_a_commit_that_is_not_on_main(tmp_path: Path) -> None:
    """`git tag v9.9.9 <any-commit> && git push --tags` must not reach PyPI."""
    repo = _repo(tmp_path)
    subprocess.run(["git", "checkout", "-q", "-b", "side"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "stray",
        ],
        cwd=repo,
        check=True,
    )
    stray = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    assert _gate(repo, stray.stdout.strip(), "main") == 1


def test_the_gate_refuses_when_it_cannot_resolve_main(tmp_path: Path) -> None:
    """A shallow checkout must stop the release, not be assumed safe."""
    repo = _repo(tmp_path)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    assert _gate(repo, head.stdout.strip(), "definitely-not-a-ref") == 1


@pytest.mark.skipif(not GATE.exists(), reason="gate script absent")
def test_the_gate_is_executable() -> None:
    assert GATE.stat().st_mode & 0o111, "release.yml calls it directly"


def test_every_example_contract_still_adjudicates() -> None:
    """Nothing exercised examples/, so one could rot silently against a vocabulary change.

    They are the first thing a reader copies, and `netspec check` executes them -- an
    example that no longer loads is worse than no example.
    """
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    examples = sorted((root / "examples").glob("*.py"))
    assert examples, "examples/ is empty"

    for contract in examples:
        done = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "kicad_netspec.cli", "check", str(contract)],
            capture_output=True,
            text=True,
            check=False,
            cwd=root,
        )
        if done.returncode == 4:
            pytest.skip(f"no KiCad available: {done.stderr.strip()[:80]}")
        assert done.returncode == 0, f"{contract.name}: {done.stdout}\n{done.stderr}"
