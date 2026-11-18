"""`just --list` publishes what the recipes do (#28).

`just` shows the LAST comment line directly above a recipe. A multi-line block therefore
publishes its final line: `setup` was listed as "covering none of that surface." -- the
tail of a rationale -- while its real description sat unseen at the top of the block.

Ported from the same gate in prusaslicer-py, trimmed to what this justfile uses: no
modules and no OS-attributed duplicate recipes, both of which it refuses rather than
silently skips.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JUSTFILE = ROOT / "justfile"

needs_just = pytest.mark.skipif(shutil.which("just") is None, reason="needs the just binary")


def _just_recipes() -> dict[str, dict]:
    """Every recipe as `just` itself sees it, not as a regex over the file would."""
    out = subprocess.run(
        ["just", "--dump", "--dump-format", "json"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0, f"`just --dump --dump-format json` failed:\n{out.stderr}"
    dump = json.loads(out.stdout)
    assert not dump.get("modules"), "this gate does not descend into modules; extend it first"
    return dump["recipes"]


def _comment_blocks_above(name: str, lines: list[str]) -> list[list[str]]:
    """The comment lines directly above each definition of `name`, attributes skipped."""
    blocks: list[list[str]] = []
    for i, line in enumerate(lines):
        if not re.match(rf"^@?{re.escape(name)}(\s|:)", line):
            continue
        if re.match(rf"^@?{re.escape(name)}\s*:=", line):
            continue  # a variable sharing the recipe's name, not the recipe
        block: list[str] = []
        j = i - 1
        while j >= 0:
            above = lines[j].strip()
            if above.startswith("[") and above.endswith("]"):
                j -= 1
                continue
            if not above.startswith("#"):
                break
            block.insert(0, above)
            j -= 1
        blocks.append(block)
    return blocks


@needs_just
def test_every_recipe_has_exactly_one_doc_comment_line() -> None:
    """Rationale is not banned, it is separated: put it above a blank line, where `just`
    cannot reach it. Exactly one line, because a second is how the drift starts."""
    lines = JUSTFILE.read_text(encoding="utf-8").splitlines()
    unlocatable, wrong = [], []
    for name, recipe in sorted(_just_recipes().items()):
        if recipe.get("private"):
            continue
        found = _comment_blocks_above(name, lines)
        if len(found) != 1:
            unlocatable.append(f"{name} ({len(found)} definitions found)")
            continue
        if len(found[0]) != 1:
            wrong.append(f"{name}: {len(found[0])} comment lines directly above it")

    assert not unlocatable, f"could not locate exactly one definition of: {unlocatable}"
    assert not wrong, (
        "put any rationale above a blank line so `just --list` cannot publish it:\n  "
        + "\n  ".join(wrong)
    )


@needs_just
def test_every_listed_recipe_has_a_description() -> None:
    """The property the rule above stands in for, read off `just`'s own output."""
    missing = [
        name
        for name, recipe in sorted(_just_recipes().items())
        if not recipe.get("private") and not (recipe.get("doc") or "").strip()
    ]
    assert not missing, f"listed with no description: {missing}"


def test_no_doc_attribute_is_used() -> None:
    """`[doc(...)]` exists only from just 1.27.0, and an unknown attribute is a parse
    error, so it breaks the whole justfile on Ubuntu 24.04 LTS's 1.21.0."""
    offenders = [
        n
        for n, line in enumerate(JUSTFILE.read_text(encoding="utf-8").splitlines(), 1)
        if re.match(r"\[\s*doc\s*[(\]]", line.lstrip())
    ]
    assert not offenders, f"[doc(...)] on line(s) {offenders}"
