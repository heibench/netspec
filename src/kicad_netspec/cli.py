"""Command line entry point.

Argparse, not click or typer: the core has no runtime dependencies and this is the
reason to keep it that way.

Exit codes are part of the contract (DECISIONS D10) and mean different things::

    0  the design was read, and nothing asked for was violated
    1  the design was read, and something was violated -- a statement about the design
    4  the engine could not run, or its input was unreadable -- NOT about the design
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from kicad_netspec import __version__, contract, snapshot
from kicad_netspec.check import CheckReport, Verdict, check_spec
from kicad_netspec.diff import NetlistDiff, diff_netlists
from kicad_netspec.isolate import ContractError, load_isolated
from kicad_netspec.model import Netlist
from kicad_netspec.ops.guard import guard as run_guard
from kicad_netspec.oracle import Cli10Backend, EnvironmentError_, RuleReport, find_kicad_cli
from kicad_netspec.report import check_report

EXIT_OK = 0
EXIT_VIOLATION = 1
EXIT_INCOMPLETE = 2
EXIT_ENVIRONMENT = 4
EXIT_USAGE = 64
"""EX_USAGE. 2 used to mean this, which is what partspec calls INCOMPLETE -- a consumer
branching on 2 across the two tools read "you invoked me wrong" as "I could not
decide" (A1). netspec follows partspec, because 2 is where the third verdict belongs
and usage has a conventional code of its own."""

_EXIT: dict[Verdict, int] = {
    "pass": EXIT_OK,
    "fail": EXIT_VIOLATION,
    "incomplete": EXIT_INCOMPLETE,
    "error": EXIT_ENVIRONMENT,
}


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    # Everything after the first bare `--` is the command `guard` should run. Split it
    # off before argparse sees it: argparse.REMAINDER would otherwise swallow any flag
    # that happens to follow the positional argument.
    trailing: list[str] = []
    if "--" in raw:
        cut = raw.index("--")
        raw, trailing = raw[:cut], raw[cut + 1 :]

    parser = _parser()
    args = parser.parse_args(raw)
    args.trailing = trailing
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_USAGE
    try:
        return int(args.handler(args))
    except EnvironmentError_ as exc:
        # An environment fault is never a statement about the design.
        print(f"error: {exc}", file=sys.stderr)
        print(
            "\nThis is an environment problem, not a finding about your design.",
            file=sys.stderr,
        )
        return EXIT_ENVIRONMENT


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netspec",
        description="Verify PCB connectivity against declared engineering intent.",
    )
    parser.add_argument("--version", action="version", version=f"netspec {__version__}")
    subs = parser.add_subparsers(dest="command")

    doctor = subs.add_parser("doctor", help="check that a KiCad engine can be found and run")
    doctor.set_defaults(handler=_doctor)

    netlist = subs.add_parser("netlist", help="show what KiCad thinks is connected")
    netlist.add_argument("schematic", type=Path)
    netlist.add_argument("--variant", default=None, help="design variant to export")
    netlist.add_argument(
        "--all", action="store_true", help="include nets with only one pin on them"
    )
    netlist.set_defaults(handler=_netlist)

    snap = subs.add_parser("snap", help="record connectivity as stable JSON")
    snap.add_argument("schematic", type=Path)
    snap.add_argument("-o", "--output", type=Path, required=True)
    snap.add_argument("--variant", default=None)
    snap.set_defaults(handler=_snap)

    d = subs.add_parser("diff", help="what changed between two readings of a design")
    d.add_argument("before", type=Path, help="a snapshot, or a schematic")
    d.add_argument("after", type=Path, help="a snapshot, or a schematic")
    d.add_argument("--variant", default=None)
    d.add_argument("--exit-zero", action="store_true", help="report changes but always exit 0")
    d.set_defaults(handler=_diff)

    check = subs.add_parser("check", help="adjudicate a contract against the design")
    check.add_argument("contract", help="path to a contract, optionally 'file.py:name'")
    check.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="text for a person, json for anything else (D2)",
    )
    check.set_defaults(handler=_check)

    gate = subs.add_parser("gate", help="run ERC and DRC at every severity")
    gate.add_argument("design", type=Path, help="a .kicad_sch or a .kicad_pcb")
    gate.add_argument(
        "--fail-on",
        default="error",
        choices=("error", "warning", "any"),
        help="which severity should fail the gate (default: error)",
    )
    gate.set_defaults(handler=_gate)

    g = subs.add_parser("guard", help="run a command and adjudicate what it did")
    g.add_argument("schematic", type=Path)
    g.add_argument("--contract", default=None, help="also check this contract afterwards")
    g.add_argument("--dry-run", action="store_true", help="read twice without running anything")
    g.set_defaults(handler=_guard)

    return parser


def _doctor(_args: argparse.Namespace) -> int:
    cli = find_kicad_cli()
    caps = Cli10Backend(cli).capabilities()
    print(f"engine   : {cli}")
    print(f"invoked  : {' '.join(cli.argv)}")
    if cli.sandboxed:
        print("note     : sandboxed engine; work files are written beside the design")
    print(f"can do   : {caps}")
    return EXIT_OK


def _netlist(args: argparse.Namespace) -> int:
    netlist = Cli10Backend().netlist(args.schematic, variant=args.variant)

    print(f"{netlist.source or args.schematic}")
    print(f"  {len(netlist.components)} components, {len(netlist.nets)} nets")

    shown = netlist.nets.values() if args.all else netlist.connected_nets
    width = max((len(n.name) for n in shown), default=0)
    for net in sorted(shown, key=lambda n: (n.anonymous, n.name)):
        pins = ", ".join(sorted(str(node) for node in net.nodes))
        marker = " " if net.connected else "!"
        print(f"  {marker} {net.name:<{width}}  {pins}")

    isolated = netlist.isolated_nodes
    if isolated and not args.all:
        print(f"\n  {len(isolated)} pin(s) connected to nothing: ", end="")
        print(", ".join(str(n) for n in isolated[:8]) + (" ..." if len(isolated) > 8 else ""))
    if not netlist.connected_nets:
        print("\n  nothing in this schematic is connected to anything.")
        return EXIT_VIOLATION
    return EXIT_OK


def _snap(args: argparse.Namespace) -> int:
    netlist = Cli10Backend().netlist(args.schematic, variant=args.variant)
    out = snapshot.write(netlist, args.output)
    print(f"{out}  ({len(netlist.components)} components, {len(netlist.nets)} nets)")
    return EXIT_OK


def _load_side(path: Path, variant: str | None) -> Netlist:
    """Accept either a snapshot or a design, so `diff` needs no flags to say which."""
    if path.suffix == ".json":
        try:
            return snapshot.read(path)
        except snapshot.SnapshotError as exc:
            raise EnvironmentError_(str(exc)) from exc
    return Cli10Backend().netlist(path, variant=variant)


def _diff(args: argparse.Namespace) -> int:
    before = _load_side(args.before, args.variant)
    after = _load_side(args.after, args.variant)
    result = diff_netlists(before, after)
    _print_diff(result)
    if args.exit_zero or result.empty:
        return EXIT_OK
    return EXIT_VIOLATION


def _print_diff(result: NetlistDiff) -> None:
    if result.empty and not result.benign:
        print("no change in connectivity")
        return

    if result.structural:
        print("NET CHANGES")
        for change in result.structural:
            print(f"  {change}")

    if result.component_changes:
        print("\nCOMPONENTS")
        for change in result.component_changes:
            print(f"  {change}")

    if result.now_floating or result.no_longer_floating:
        print("\nFLOATING PINS")
        for node in result.now_floating:
            print(f"  ! {node}  is no longer connected to anything")
        for node in result.no_longer_floating:
            print(f"  + {node}  is now connected")

    if result.pin_swaps:
        print("\nPIN SWAPS  (a connection moved between pins of one part)")
        for swap in result.pin_swaps:
            print(f"  {swap}")

    if result.benign:
        print(f"\n{len(result.benign)} net(s) renamed with no change in membership")

    print()
    if result.empty:
        print("no change in connectivity")
        return
    bits = []
    if result.structural:
        bits.append(f"{len(result.structural)} net change(s)")
    if result.component_changes:
        bits.append(f"{len(result.component_changes)} component change(s)")
    if result.now_floating:
        bits.append(f"{len(result.now_floating)} pin(s) newly floating")
    risky = [s for s in result.pin_swaps if s.polarity_risk]
    if result.pin_swaps:
        bits.append(f"{len(result.pin_swaps)} pin swap(s)")
    print("CHANGED  " + ", ".join(bits))
    if risky:
        print(
            f"\nWARNING: {len(risky)} pin swap(s) on a two-pin part. If any is polarised, "
            "it is now backwards, and ERC will not tell you."
        )


def _print_check(report: CheckReport) -> None:
    for result in report.results:
        print(f"  {result}")
    counts = {s: len(report.of_status(s)) for s in ("pass", "fail", "skipped", "unsupported")}
    summary = ", ".join(f"{v} {k}" for k, v in counts.items() if v)
    print(f"\n{report.verdict.upper()}  ({summary})")
    if counts["skipped"]:
        print("note: a skipped rule was not evaluated, and is not a pass.")


def _check(args: argparse.Namespace) -> int:
    # A contract is executed Python (D8) and runs before the design is read, so in this
    # process it could replace the oracle and have netspec report on a board it invented.
    # It runs in a child instead and hands back only declarations; the netlist below is
    # read here, by an oracle the contract never touched. See isolate.py.
    try:
        spec = load_isolated(args.contract)
    except ContractError as exc:
        raise EnvironmentError_(str(exc)) from exc

    source = Path(contract.resolve_source(spec, args.contract))
    netlist = Cli10Backend().netlist(source, variant=spec.variant)
    report = check_spec(spec, netlist)

    if getattr(args, "format", "text") == "json":
        document = check_report(
            report, contract=str(args.contract), name=spec.name, netlist=netlist
        )
        print(json.dumps(document, indent=2, sort_keys=False))
    else:
        print(f"{source}")
        _print_check(report)
    return _EXIT[report.verdict]


def _print_rules(report: RuleReport, label: str) -> None:
    errors, warnings = report.errors, report.warnings
    print(f"{label}: {len(errors)} error(s), {len(warnings)} warning(s)")
    for finding in report.findings:
        if finding.severity in ("error", "warning"):
            where = f" [{finding.sheet}]" if finding.sheet else ""
            print(f"  {finding}{where}")


def _gate(args: argparse.Namespace) -> int:
    backend = Cli10Backend()
    design = args.design
    if design.suffix == ".kicad_pcb":
        report = backend.drc(design, schematic_parity=True)
        _print_rules(report, "DRC (with schematic parity)")
    else:
        report = backend.erc(design)
        _print_rules(report, "ERC")

    print(
        "\nseverities requested: "
        + ", ".join(report.severities_requested)
        + "   (KiCad's defaults would hide most of these)"
    )
    threshold = {
        "error": len(report.errors),
        "warning": len(report.errors) + len(report.warnings),
        "any": len(report.findings),
    }[args.fail_on]
    return EXIT_VIOLATION if threshold else EXIT_OK


def _guard(args: argparse.Namespace) -> int:
    argv = list(args.trailing)
    if not argv and not args.dry_run:
        print(
            "guard needs a command after `--`:\n  netspec guard board.kicad_sch -- <command>",
            file=sys.stderr,
        )
        return EXIT_USAGE

    spec = None
    if args.contract:
        try:
            spec = load_isolated(args.contract)
        except ContractError as exc:
            raise EnvironmentError_(str(exc)) from exc

    backend = Cli10Backend()
    print(f"guarding {args.schematic}")
    result = run_guard(args.schematic, argv or ["true"], read=backend.netlist, spec=spec)

    print(f"\n-- {result.command} --\n")
    _print_diff(result.diff)

    if result.check is not None:
        print("\nCONTRACT")
        _print_check(result.check)

    if result.ok:
        return EXIT_OK
    if not result.command.ok:
        print("\nthe command itself failed; the design may be half-edited.")
    return EXIT_VIOLATION


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
