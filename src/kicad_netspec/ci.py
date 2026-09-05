"""The engine behind the GitHub Action: compare a design against a base revision.

Renders a Markdown report and writes the GitHub Actions outputs. Kept as a module rather
than inline shell so it is testable and so the rendering can be reused.

Everything it needs comes from the environment, because that is how a composite action
passes inputs::

    NETSPEC_SCHEMATICS      newline- or comma-separated paths
    NETSPEC_BASE_REF        revision to compare against
    NETSPEC_CONTRACT        optional contract module
    NETSPEC_FAIL_ON_CHANGE  "true" to fail on any change at all
    GITHUB_OUTPUT           where Actions reads step outputs from
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

from kicad_netspec.check import CheckReport, check_spec
from kicad_netspec.diff import NetlistDiff, diff_netlists
from kicad_netspec.isolate import load_isolated
from kicad_netspec.model import Netlist
from kicad_netspec.ops.run import run_command
from kicad_netspec.oracle import Cli10Backend, EnvironmentError_
from kicad_netspec.report import design_digest

__all__ = ["main", "render"]

MARKER = "<!-- netspec-connectivity -->"


def main() -> int:
    schematics = _paths(os.environ.get("NETSPEC_SCHEMATICS", ""))
    if not schematics:
        print("netspec: no schematic given", file=sys.stderr)
        return 2

    base_ref = os.environ.get("NETSPEC_BASE_REF", "").strip()
    contract_path = os.environ.get("NETSPEC_CONTRACT", "").strip()
    fail_on_change = os.environ.get("NETSPEC_FAIL_ON_CHANGE", "").lower() == "true"

    try:
        backend = Cli10Backend()
    except EnvironmentError_ as exc:
        # No engine is not a finding about anyone's board (DECISIONS D10).
        _emit(report=_env_failure(str(exc)), changed=False, suspicious=False)
        print(f"netspec: {exc}", file=sys.stderr)
        return 0

    sections: list[str] = []
    changed = suspicious = failed = False

    for path in schematics:
        section, has_change, is_suspicious, is_failure = _one(
            backend, path, base_ref, contract_path
        )
        sections.append(section)
        changed |= has_change
        suspicious |= is_suspicious
        failed |= is_failure

    report = render(sections, changed=changed, suspicious=suspicious)
    _emit(report=report, changed=changed, suspicious=suspicious)
    print(report)

    if failed or suspicious:
        return 1
    return 1 if (changed and fail_on_change) else 0


def _one(
    backend: Cli10Backend, path: Path, base_ref: str, contract_path: str
) -> tuple[str, bool, bool, bool]:
    """Compare one schematic. Returns (markdown, changed, suspicious, failed)."""
    head = backend.netlist(path)

    check: CheckReport | None = None
    if contract_path:
        # Isolated, like `check` and `guard` (D24). This is the highest-stakes consumer
        # -- it posts a verdict as a pull-request comment and gates merges -- and it was
        # left on the in-process path when the others were converted.
        spec = load_isolated(contract_path)
        check = check_spec(spec, head)

    base = _base_version(backend, path, base_ref) if base_ref else None
    result = diff_netlists(base, head) if base is not None else None

    # Recorded here too: D24 leans on the digest to make an on-disk schematic swap
    # detectable, and the scenario it describes is a CI one. Emitting it only from
    # `check --format json` left the merge-gating consumer without it.
    digest = design_digest(head)
    body = [f"### `{path}`", "", f"<sub>design `{digest}`</sub>", ""]
    if result is None:
        body.append(
            f"_No base revision to compare against_ — {len(head.components)} components, "
            f"{len(head.connected_nets)} connected nets."
        )
    elif result.empty:
        body.append("Connectivity unchanged.")
        body.append("")
    else:
        body.append(_render_diff(result))

    if check is not None:
        body += ["", _render_check(check)]

    failed = check is not None and check.verdict != "pass"
    return (
        "\n".join(body),
        bool(result and not result.empty),
        bool(result and result.suspicious),
        failed,
    )


def _base_version(backend: Cli10Backend, path: Path, base_ref: str) -> Netlist | None:
    """Read the same schematic as it exists at the base revision.

    Materialises the **whole tree** at that revision in a throwaway worktree, rather than
    extracting the one file. A hierarchical design's root sheet refers to its sub-sheets
    by relative path, so a lone file in an empty directory reads as a design with no
    sub-sheets -- which would report every hierarchical net as deleted.

    The worktree is placed inside the repository, because a sandboxed KiCad cannot read
    ``/tmp``.
    """
    root = _repo_root(path)
    if root is None:
        return None

    try:
        relative = path.resolve().relative_to(root)
    except ValueError:
        return None

    with tempfile.TemporaryDirectory(dir=root, prefix=".netspec-base-") as holder:
        tree = Path(holder) / "base"
        added = run_command(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(tree), base_ref],
            capture_to=Path(holder) / "worktree.log",
        )
        if not added.ok:
            return None
        try:
            return backend.netlist(tree / relative)
        except EnvironmentError_:
            return None
        finally:
            run_command(
                ["git", "-C", str(root), "worktree", "remove", "--force", str(tree)],
                capture_to=Path(holder) / "cleanup.log",
            )


def _repo_root(path: Path) -> Path | None:
    for candidate in [path.resolve().parent, *path.resolve().parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def _render_diff(result: NetlistDiff) -> str:
    lines: list[str] = []

    if result.structural:
        lines += ["**Net changes**", "", "```"]
        lines += [f"{c}" for c in result.structural]
        lines += ["```", ""]

    if result.now_floating or result.no_longer_floating:
        lines += ["**Floating pins**", "", "```"]
        lines += [f"! {n}  no longer connected" for n in result.now_floating]
        lines += [f"+ {n}  now connected" for n in result.no_longer_floating]
        lines += ["```", ""]

    if result.component_changes:
        lines += ["**Components**", "", "```"]
        lines += [f"{c}" for c in result.component_changes]
        lines += ["```", ""]

    repins = [s for s in result.meaningful_swaps if s.significance == "repin"]
    if repins:
        lines += ["**Pins re-assigned**", "", "```"]
        lines += [f"{s}" for s in repins]
        lines += ["```", ""]

    if result.suspicious:
        lines += [
            "> [!WARNING]",
            "> **A polarised part has been reversed.**",
            ">",
            "> Its two terminals are not interchangeable, and they have traded nets.",
            "> ERC will not report this — reversing a polarised part is legal wiring —",
            "> so nothing else in your pipeline is going to tell you.",
            ">",
        ]
        lines += [f"> - `{s}`" for s in result.suspicious]
        lines.append("")

    return "\n".join(lines).rstrip()


def _render_check(report: CheckReport) -> str:
    icon = {"pass": "✅", "fail": "❌", "skipped": "⚠️", "unsupported": "➖"}
    lines = ["**Contract**", "", "| | Rule | Detail |", "|---|---|---|"]
    for result in report.results:
        detail = result.detail.replace("|", "\\|")
        lines.append(f"| {icon[result.status]} | {result.rule} | {detail} |")
    lines += ["", f"**{report.verdict.upper()}**"]
    if report.of_status("skipped"):
        lines.append("")
        lines.append("_A skipped rule was not evaluated, and is not a pass._")
    return "\n".join(lines)


def render(sections: list[str], *, changed: bool, suspicious: bool) -> str:
    if suspicious:
        headline = "Connectivity changed, and part of it looks like a defect"
    elif changed:
        headline = "Connectivity changed"
    else:
        headline = "Connectivity unchanged"

    return "\n".join(
        [
            MARKER,
            f"## {headline}",
            "",
            *sections,
            "",
            "<sub>Reported by "
            "[netspec](https://github.com/heibench/netspec) — KiCad's own netlist, "
            "not a guess.</sub>",
        ]
    )


def _env_failure(message: str) -> str:
    return "\n".join(
        [
            MARKER,
            "## Connectivity not checked",
            "",
            "netspec could not run KiCad, so it has **not** examined this design. "
            "This says nothing about the board.",
            "",
            "```",
            message,
            "```",
            "",
            "<sub>Run the job in a KiCad container, e.g. "
            "`container: ghcr.io/inti-cmnb/kicad10_auto:latest`.</sub>",
        ]
    )


def _paths(raw: str) -> list[Path]:
    parts = [p.strip() for chunk in raw.splitlines() for p in chunk.split(",")]
    return [Path(p) for p in parts if p]


def _emit(*, report: str, changed: bool, suspicious: bool) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        return
    with open(out, "a", encoding="utf-8") as handle:
        handle.write(f"changed={'true' if changed else 'false'}\n")
        handle.write(f"suspicious={'true' if suspicious else 'false'}\n")
        # Multi-line values need a delimiter that cannot occur in the body.
        handle.write("report<<NETSPEC_EOF\n")
        handle.write(report.rstrip() + "\n")
        handle.write("NETSPEC_EOF\n")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
