"""Adjudicate a contract against what KiCad says.

Four statuses (DECISIONS D9), of which only ``pass`` is green::

    pass         evaluated and satisfied
    fail         evaluated and violated
    unsupported  this backend cannot evaluate this rule
    skipped      not evaluated -- a part or net the rule names is not in the design

``skipped`` is deliberately not green. A rule about a component that is missing has not
been satisfied; it has not been tested, and silently treating that as success is how a
contract stops protecting anything.

There is no fifth status for an indeterminate result, because connectivity is exact.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast

from kicad_netspec.contract import (
    Forbid,
    Mirrors,
    Net,
    Polarity,
    Rule,
    Spec,
    Through,
    Unknown,
)
from kicad_netspec.model import Netlist
from kicad_netspec.resolve import Resolved, resolve_spec

__all__ = ["CHECKERS", "CheckReport", "CheckResult", "check_spec", "checks"]

Status = Literal["pass", "fail", "unsupported", "skipped"]
Verdict = Literal["pass", "fail"]


@dataclass(frozen=True)
class CheckResult:
    """One rule, adjudicated."""

    rule: str
    """The rendered sentence. What a person reads; not what a machine should parse."""

    status: Status
    detail: str = ""

    data: Mapping[str, Any] = field(default_factory=dict, hash=False)
    """The rule as fields, from :meth:`Rule.describe`. Empty for a synthetic result that
    corresponds to no rule -- a contract that asserted nothing, or a rule type with no
    checker."""

    @property
    def green(self) -> bool:
        return self.status == "pass"

    def __str__(self) -> str:
        mark = {"pass": "ok  ", "fail": "FAIL", "unsupported": "n/a ", "skipped": "skip"}[
            self.status
        ]
        return f"{mark}  {self.rule}" + (f"\n        {self.detail}" if self.detail else "")


@dataclass(frozen=True)
class CheckReport:
    """Every rule in a contract, adjudicated against one reading of the design."""

    results: tuple[CheckResult, ...] = ()
    source: str = ""
    kicad_version: str = ""

    @property
    def verdict(self) -> Verdict:
        """Green only when there was something to check and every rule passed.

        A report with no results is not green: ``all([])`` is ``True``, so an empty
        contract would otherwise exit 0 while protecting nothing.
        """
        return "pass" if self.results and all(r.green for r in self.results) else "fail"

    def of_status(self, status: Status) -> tuple[CheckResult, ...]:
        return tuple(r for r in self.results if r.status == status)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return self.of_status("fail")

    def __str__(self) -> str:
        counts = {s: len(self.of_status(s)) for s in ("pass", "fail", "unsupported", "skipped")}
        return (
            f"CheckReport({self.verdict}: " + ", ".join(f"{v} {k}" for k, v in counts.items()) + ")"
        )


# -- the rule registry -----------------------------------------------------------------
#
# One checker per rule type, claimed by decorator at import. Adding a primitive used to
# mean editing six places, one of which -- a literal isinstance tuple in `Spec` -- failed
# at import with "not a rule" and named nothing useful. Now the dataclass and the checker
# below are the whole of it, and `test_boundaries` fails if either is missing.

Checker = Callable[[Rule, Netlist, Resolved], CheckResult]
CHECKERS: dict[type[Rule], Checker] = {}


def checks[R: Rule](
    kind: type[R],
) -> Callable[[Callable[[R, Netlist, Resolved], CheckResult]], Checker]:
    """Register the adjudicator for one rule type."""

    def claim(fn: Callable[[R, Netlist, Resolved], CheckResult]) -> Checker:
        registered = cast(Checker, fn)
        CHECKERS[kind] = registered
        return registered

    return claim


def check_spec(spec: Spec, netlist: Netlist) -> CheckReport:
    """Adjudicate every rule in ``spec`` against ``netlist``."""
    resolved = resolve_spec(spec, netlist)
    results = [_adjudicate(rule, netlist, resolved) for rule in spec.rules]
    if spec.require_no_floating_pins:
        results.append(_check_no_floating(netlist))
    if not results:
        results.append(
            CheckResult(
                rule="this contract asserts something",
                status="fail",
                detail="the contract has no rules, so it checked nothing and protects nothing",
                data={"kind": "spec", "subject": "asserts_something"},
            )
        )
    return CheckReport(
        results=tuple(results),
        source=netlist.source,
        kicad_version=netlist.kicad_version,
    )


def _adjudicate(rule: Rule, netlist: Netlist, resolved: Resolved) -> CheckResult:
    """Evaluate one rule, unless its names do not pick out one net each.

    An ambiguous name is a defect in the *contract*, not a finding about the board, so
    it is reported before the rule runs rather than resolved by guesswork. Absence is
    left to the rule: for ``forbid`` a vanished net is the answer, not an obstacle.
    """
    # kind LAST: a describe() returning its own kind could otherwise diverge from
    # the ClassVar Spec dedupes on, producing two rules Spec accepts and one id.
    described = {**rule.describe(), "kind": rule.kind}

    problems = resolved.problems_for(rule)
    if problems:
        # Described the same way as any other outcome. Passing the bare describe() here
        # dropped `kind`, so a rule naming an ambiguous net silently changed id -- read
        # by a report diff as "assertion deleted, unknown assertion appeared", the exact
        # false alarm the id exists to prevent.
        return CheckResult(
            rule=str(rule), status="fail", detail="; ".join(problems), data=described
        )

    checker = CHECKERS.get(type(rule))
    if checker is None:
        # Not `unsupported`: that means "this backend cannot evaluate this rule" (D9).
        # netspec knowing no way to check something is a defect in netspec.
        return CheckResult(
            rule=str(rule),
            status="fail",
            detail=f"netspec has no way to check a {type(rule).__name__} rule",
            data=described,
        )
    # Attached here rather than in every checker: a checker that forgot would produce a
    # result the report cannot key, and nothing would say so.
    outcome = checker(rule, netlist, resolved)
    return replace(outcome, data=described) if not outcome.data else outcome


@checks(Through)
def _check_through(rule: Through, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)
    a, b = (resolved.net(n) or n for n in rule.nets)

    if a == b:
        # The constructor refuses two identical spellings, but resolution (D19) can map
        # two different ones onto one net afterwards, and nothing re-checked.
        return CheckResult(
            rule=label,
            status="fail",
            detail=f"{rule.nets[0]} and {rule.nets[1]} are both {a}; a net cannot bridge to itself",
        )

    absent = [n for n, found in zip(rule.nets, (a, b), strict=True) if found not in netlist.nets]
    if absent:
        # Distinguished the way _check_forbid distinguishes them: a merge keeps one of
        # the names, so both ends vanishing is not a merge, it is a contract naming
        # nothing real.
        if len(absent) == len(rule.nets):
            detail = (
                f"neither {' nor '.join(absent)} is in this design; the rule names nothing real"
            )
        else:
            detail = f"{absent[0]} is not in this design; it may have merged with the other end"
        return CheckResult(rule=label, status="fail", detail=detail)

    if rule.ref not in netlist.components:
        return CheckResult(rule=label, status="skipped", detail=f"{rule.ref} is not in this design")

    sits_on = {netlist.net_of(rule.ref, n.pin) for n in netlist.nodes_of(rule.ref)}
    if not {a, b} <= sits_on:
        missing = sorted({a, b} - sits_on)
        return CheckResult(
            rule=label,
            status="fail",
            detail=f"{rule.ref} has no pin on {', '.join(missing)}",
        )

    if rule.only:
        both = {n.ref for n in netlist.nets[a].nodes} & {n.ref for n in netlist.nets[b].nodes}
        others = sorted(both - {rule.ref})
        if others:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"{', '.join(others)} also has pins on both {a} and {b}. A second "
                    "bridge is legal wiring, so ERC will not report it."
                ),
            )
    return CheckResult(rule=label, status="pass")


@checks(Mirrors)
def _check_mirrors(rule: Mirrors, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)
    a, b = rule.parts

    for ref in (a, b):
        if ref not in netlist.components:
            return CheckResult(rule=label, status="skipped", detail=f"{ref} is not in this design")

    on_a = {n.pin: str(netlist.net_of(a, n.pin)) for n in netlist.nodes_of(a)}
    on_b = {n.pin: str(netlist.net_of(b, n.pin)) for n in netlist.nodes_of(b)}

    if set(on_a) != set(on_b):
        only_a = sorted(set(on_a) - set(on_b))
        only_b = sorted(set(on_b) - set(on_a))
        said = [f"{a} has pin {', '.join(only_a)}"] if only_a else []
        said += [f"{b} has pin {', '.join(only_b)}"] if only_b else []
        return CheckResult(rule=label, status="fail", detail="; ".join(said))

    # KiCad reports a pin wired to nothing as a one-node net it named itself,
    # `unconnected-(U3-IN-Pad1)`. Left in the mapping, a dangling pin paired with a wired
    # one as if it were a net. A one-node net the *designer* named is a different thing
    # -- a deliberate label on a sheet pin -- so anonymity is part of the test.
    floating = {name for name, net in netlist.nets.items() if net.anonymous and not net.connected}
    for pin in sorted(on_a):
        loose_a, loose_b = on_a[pin] in floating, on_b[pin] in floating
        if loose_a != loose_b:
            wired, bare = (b, a) if loose_a else (a, b)
            return CheckResult(
                rule=label,
                status="fail",
                detail=f"pin {pin}: {bare} is connected to nothing, {wired} is wired",
            )

    # A net carried by BOTH parts must map to itself. Without this the rule is graph
    # isomorphism, which is invariant under relabelling, so any permutation passed --
    # a VCC/GND swap included.
    shared = set(on_a.values()) & set(on_b.values())
    forward: dict[str, str] = {}
    backward: dict[str, str] = {}
    for pin in sorted(on_a):
        x, y = on_a[pin], on_b[pin]
        if x in floating and y in floating:
            continue
        if (x in shared or y in shared) and x != y:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"pin {pin}: {a} is on {x} where {b} is on {y}, and both parts use "
                    f"{x if x in shared else y} -- a net they share must line up"
                ),
            )
        if forward.setdefault(x, y) != y:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"pin {pin}: {a} on {x} pairs with {b} on {y} here, but with "
                    f"{forward[x]} on another pin"
                ),
            )
        if backward.setdefault(y, x) != x:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"pin {pin}: {b} on {y} pairs with {a} on {x} here, but with "
                    f"{backward[y]} on another pin"
                ),
            )
    if not forward:
        # Two pinless parts, or two entirely unwired ones, paired nothing. Returning
        # green there is what check.py's own docstring calls "how a contract stops
        # protecting anything" -- an earlier fix to the presence check turned a
        # wrongly-worded failure into a genuinely vacuous pass.
        return CheckResult(
            rule=label,
            status="skipped",
            detail=f"{a} and {b} have no wired pins between them; nothing was compared",
        )
    return CheckResult(rule=label, status="pass", detail=f"{len(forward)} nets paired")


@checks(Unknown)
def _check_unknown(rule: Unknown, netlist: Netlist, resolved: Resolved) -> CheckResult:
    return CheckResult(
        rule=str(rule),
        status="fail",
        detail=(
            f"the contract declares a {rule.declared!r} rule; this netspec has no "
            "vocabulary for it, so the assertion was not evaluated"
        ),
    )


@checks(Net)
def _check_net(rule: Net, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)

    found = netlist.nets.get(resolved.net(rule.name) or rule.name)
    if found is None:
        return CheckResult(
            rule=label,
            status="fail",
            detail=f"there is no net called {rule.name!r} in this design",
        )

    wanted: set[str] = set()
    for pin in rule.pins:
        node = netlist.resolve(pin)
        if node is None:
            return CheckResult(
                rule=label,
                status="skipped",
                detail=f"{pin} is not a pin in this design, so the rule was not evaluated",
            )
        wanted.add(str(node))

    actual = {str(n) for n in found.nodes}
    missing = sorted(wanted - actual)
    extra = sorted(actual - wanted)

    if missing or (rule.exact and extra):
        parts = []
        if missing:
            parts.append(f"missing {', '.join(missing)}")
        if extra and rule.exact:
            parts.append(f"unexpected {', '.join(extra)}")
        return CheckResult(rule=label, status="fail", detail="; ".join(parts))
    return CheckResult(rule=label, status="pass")


@checks(Polarity)
def _check_polarity(rule: Polarity, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = (
        f"{rule.ref} polarity: pin {rule.plus_pin}->{rule.plus}, pin {rule.minus_pin}->{rule.minus}"
    )

    if rule.ref not in netlist.components:
        return CheckResult(rule=label, status="skipped", detail=f"{rule.ref} is not in this design")

    actual_plus = netlist.net_of(rule.ref, rule.plus_pin)
    actual_minus = netlist.net_of(rule.ref, rule.minus_pin)

    # Compare against what the design calls these nets, so a contract may name a
    # hierarchical net by its leaf the same way it does everywhere else.
    want_plus = resolved.net(rule.plus) or rule.plus
    want_minus = resolved.net(rule.minus) or rule.minus

    wrong = []
    if actual_plus != want_plus:
        wrong.append(f"pin {rule.plus_pin} is on {actual_plus or 'nothing'}, expected {rule.plus}")
    if actual_minus != want_minus:
        wrong.append(
            f"pin {rule.minus_pin} is on {actual_minus or 'nothing'}, expected {rule.minus}"
        )

    if not wrong:
        return CheckResult(rule=label, status="pass")

    # The specific, dangerous case: the two are simply the wrong way round.
    reversed_ = actual_plus == want_minus and actual_minus == want_plus
    detail = "; ".join(wrong)
    if reversed_:
        detail += f"  -- {rule.ref} IS REVERSED. ERC does not check this."
    return CheckResult(rule=label, status="fail", detail=detail)


@checks(Forbid)
def _check_forbid(rule: Forbid, netlist: Netlist, resolved: Resolved) -> CheckResult:
    label = str(rule)

    targets = {n: resolved.net(n) for n in rule.nets}
    present = [n for n, t in targets.items() if t]
    absent = [n for n, t in targets.items() if not t]

    # This is what a short actually looks like. KiCad does not emit a pin sitting on two
    # named nets -- it MERGES the nets and keeps one name, so the other simply
    # disappears. A net a contract declared distinct that has vanished is therefore the
    # signature of a short, which LVS calls an n:1 merge. Verified against kicad-cli:
    # relabelling VIN to GND in a three-net design yields two nets, and no VIN.
    if absent:
        if not present:
            return CheckResult(
                rule=label,
                status="fail",
                detail=(
                    f"none of these nets exist in this design: {', '.join(absent)} -- "
                    "the rule names nothing real, so nothing was checked"
                ),
            )
        return CheckResult(
            rule=label,
            status="fail",
            detail=(
                f"gone: {', '.join(absent)}; still here: {', '.join(present)}. "
                "A short merges two nets and keeps one of the names, so a net this "
                "contract declared separate that has vanished is what a short looks like."
            ),
        )

    return CheckResult(rule=label, status="pass")


def _check_no_floating(netlist: Netlist) -> CheckResult:
    """The one Spec-level option that adjudicates, so it reports as a `spec` result."""
    floating = netlist.isolated_nodes
    label = "no pin is left unconnected"
    described = {"kind": "spec", "subject": "no_floating_pins"}
    if not floating:
        return CheckResult(rule=label, status="pass", data=described)
    shown = ", ".join(str(n) for n in floating[:10])
    more = f" (and {len(floating) - 10} more)" if len(floating) > 10 else ""
    return CheckResult(
        rule=label,
        status="fail",
        detail=f"{len(floating)} floating: {shown}{more}",
        data=described,
    )
