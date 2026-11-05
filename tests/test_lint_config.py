"""Both ruffs are the same ruff (#30).

ruff is pinned twice: the dev group in `pyproject.toml` (what `just check` runs, via
`uv.lock`) and the `ruff-pre-commit` rev (what `git commit` runs). The hooks mutate --
`ruff --fix` and `ruff-format` -- so two versions mean `git commit` can write a diff
that `just check` then rejects, and the failure reads as the contributor's mistake.
They had drifted five minors apart (v0.11.12 against 0.16.5) and, by the time this
landed, still sat at v0.17.0 against 0.17.3.

Ported from the same gate in partspec and prusaslicer-py. Bumping one pin alone fails
here instead of drifting quietly.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _resolved_ruff() -> str:
    """The ruff `uv run` installs: the lock, not the declaration."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    resolved = [pkg["version"] for pkg in lock["package"] if pkg["name"] == "ruff"]
    assert len(resolved) == 1, f"expected one ruff in uv.lock, got {resolved}"
    return resolved[0]


def _pre_commit_ruff_revs(text: str) -> list[str]:
    """Every `rev:` of a ruff-pre-commit repo item, read line-wise at its key indent.

    Not a flattened regex over the whole file: a comment or a folded `name:` naming
    another version defeats that. A block scalar's continuation is indented deeper
    than its key, so it cannot pose as a four-space `rev:`. Not pyyaml either -- it is
    not a dependency here, and adding one to read a few lines is the heavier fix.
    """
    revs = []
    in_ruff_repo = False
    for line in text.splitlines():
        bare = line.split("#", 1)[0].rstrip()
        if re.match(r"^  - repo:", bare):
            in_ruff_repo = "ruff-pre-commit" in bare
            continue
        if in_ruff_repo:
            found = re.match(r"^    rev:\s*v?(\S+)$", bare)
            if found:
                revs.append(found.group(1))
    return revs


def test_the_dev_group_pins_ruff_exactly_to_what_the_lock_resolves() -> None:
    """`ruff>=0.11` lets the lock move ruff without a diff to either pin."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pinned = [d for d in pyproject["dependency-groups"]["dev"] if d.startswith("ruff")]
    assert len(pinned) == 1, f"expected one ruff pin in the dev group, got {pinned}"
    running = _resolved_ruff()
    assert pinned[0] == f"ruff=={running}", (
        f"the dev group declares {pinned[0]!r} but `uv.lock` resolves ruff {running}"
    )


def test_both_ruff_pins_name_one_version() -> None:
    revs = _pre_commit_ruff_revs((ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8"))
    assert revs, "could not find the ruff-pre-commit rev"
    running = _resolved_ruff()
    # A set: two ruff-pre-commit blocks at the same rev are not a mismatch.
    assert set(revs) == {running}, (
        f"pre-commit pins {sorted(set(revs))} but `uv run` installs ruff {running}"
    )


def test_the_rev_reader_is_not_fooled_by_a_comment_or_another_repo() -> None:
    """The reader is what the gate rests on, so it gets a control of its own."""
    text = (
        "repos:\n"
        "  # ruff-pre-commit rev: v9.9.9\n"
        "  - repo: https://github.com/astral-sh/ruff-pre-commit\n"
        "    rev: v0.1.0  # not v9.9.9\n"
        "    hooks:\n"
        "      - id: ruff-format\n"
        "        name: >-\n"
        "          ruff-pre-commit rev: v7.7.7\n"
        "  - repo: https://github.com/pre-commit/pre-commit-hooks\n"
        "    rev: v6.0.0\n"
    )
    assert _pre_commit_ruff_revs(text) == ["0.1.0"]
