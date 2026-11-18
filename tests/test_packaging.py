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


def test_the_mcp_extra_pins_to_the_major_the_code_imports() -> None:
    """One major, because the import path differs between them and only one can work.

    netspec imports `mcp.server.mcpserver`, which exists on 2.x and not on 1.x. A range
    spanning both majors resolves happily and then fails at import on whichever one the
    resolver picked -- an install that succeeds and a tool that cannot start.

    This pinned `<2` before the port, for the same reason in the other direction.
    """
    extras = _pyproject()["project"]["optional-dependencies"]["mcp"]
    assert any(">=2" in spec and "<3" in spec for spec in extras), extras


# -- the Trusted Publishing binding ----------------------------------------------------


def test_the_workflow_filename_and_environment_match_the_registration() -> None:
    """PyPI's publisher names this file and this environment. Renaming breaks releases
    silently -- the workflow runs and the upload is rejected."""
    assert RELEASE.name == "release.yml"
    assert "environment: pypi" in RELEASE.read_text()


def test_every_action_in_every_workflow_is_pinned_to_a_sha() -> None:
    """A tag is mutable: its contents can change between releases.

    The publish step holds this project's OIDC identity for PyPI, which is exactly the
    thing not to leave on a moving ref.

    A full commit SHA with the version in a trailing comment, in every workflow, not
    an exact tag in release.yml alone (#34). On a major bump Dependabot rewrote exact
    tags to floating majors and failed this gate (#32); a SHA pin it bumps SHA-for-SHA
    and rewrites the comment with it (#44: `dc37677... # v1.14.2` to
    `7245906... # v1.14.3`), majors included (heibench/orlab#57, v5.7.0 to v6.0.0), so
    its PRs pass here unaided. All workflows rather than one file, because the
    actions group bumps them together and a split policy is the conflict again.
    Local actions (`./`) are this repo's own code and are exempt.
    """
    pin = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
    unpinned = [
        f"{wf.name}: {ref}"
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml"))
        for ref in re.findall(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$", wf.read_text(), re.MULTILINE)
        if not ref.startswith("./") and not pin.match(ref)
    ]
    assert not unpinned, f"pin to a full SHA with a `# vX.Y.Z` comment: {unpinned}"


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
