"""Contract adjudication. Netlists built directly, so no KiCad needed."""

from __future__ import annotations

import pytest

from kicad_netspec.check import check_spec
from kicad_netspec.contract import Spec, forbid, net, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist


def _rail(c1_plus_on: str = "VIN", c1_minus_on: str = "GND"):
    """A cap across a rail; which pin sits on which net is the variable."""
    nets = {"VIN": [Node("R1", "1")], "GND": [Node("R1", "2")]}
    nets[c1_plus_on].append(Node("C1", "1"))
    nets[c1_minus_on].append(Node("C1", "2"))
    return build_netlist(
        [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()],
        [Component("C1", "100uF"), Component("R1", "1k")],
    )


def _status(spec: Spec, netlist) -> list[str]:
    return [r.status for r in check_spec(spec, netlist).results]


# -- polarity: the rule that exists because ERC has none ------------------------------


def test_polarity_passes_when_the_part_is_the_right_way_round() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail())
    assert report.verdict == "pass"


def test_polarity_fails_and_says_so_plainly_when_reversed() -> None:
    spec = Spec(source="x", rules=[polarity("C1", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail(c1_plus_on="GND", c1_minus_on="VIN"))

    assert report.verdict == "fail"
    (result,) = report.failures
    assert "REVERSED" in result.detail
    assert "ERC does not check this" in result.detail


def test_polarity_on_an_absent_part_is_skipped_not_passed() -> None:
    """A rule about a part that is not there has not been satisfied (D9)."""
    spec = Spec(source="x", rules=[polarity("C9", plus="VIN", minus="GND")])
    report = check_spec(spec, _rail())
    assert _status(spec, _rail()) == ["skipped"]
    assert report.verdict != "pass", "skipped is not green"
    assert report.verdict == "incomplete", "and it is not a finding about the board either"


# -- nets ------------------------------------------------------------------------------


def test_exact_net_rejects_an_unexpected_pin() -> None:
    netlist = _rail()
    assert _status(Spec(source="x", rules=[net("VIN", ["R1.1"])]), netlist) == ["fail"]
    assert _status(Spec(source="x", rules=[net("VIN", ["R1.1", "C1.1"])]), netlist) == ["pass"]


def test_non_exact_net_allows_extras() -> None:
    spec = Spec(source="x", rules=[net("VIN", ["R1.1"], exact=False)])
    assert _status(spec, _rail()) == ["pass"]


def test_a_missing_net_fails_rather_than_skipping() -> None:
    """A net the contract names that does not exist is a real failure, not an excuse."""
    spec = Spec(source="x", rules=[net("+5V", ["R1.1"])])
    report = check_spec(spec, _rail())
    assert report.results[0].status == "fail"
    assert "no net called" in report.results[0].detail


def test_a_pin_that_does_not_exist_is_skipped() -> None:
    spec = Spec(source="x", rules=[net("VIN", ["R1.1", "C9.1"])])
    assert _status(spec, _rail()) == ["skipped"]


# -- forbid ----------------------------------------------------------------------------


def test_forbid_passes_when_the_rails_are_separate() -> None:
    spec = Spec(source="x", rules=[forbid("VIN", "GND")])
    assert _status(spec, _rail()) == ["pass"]


def test_the_netlist_forbid_used_to_hunt_for_cannot_be_built() -> None:
    """This test used to construct a pin on two nets and assert forbid caught it.

    KiCad cannot produce that shape -- a short merges the nets -- so the branch it
    exercised never ran on a real design. The model now rejects it outright, and the
    real short is covered by test_forbid_catches_a_real_short_where_one_net_was_swallowed.
    """
    with pytest.raises(ValueError):
        build_netlist(
            [
                Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1")})),
                Net("GND", frozenset({Node("R1", "1"), Node("C1", "2")})),
            ],
            [Component("C1"), Component("R1")],
        )


# -- forbid: what a short actually looks like ------------------------------------------


def _really_shorted():
    """A short as KiCad emits it: the two nets are MERGED and one name is gone.

    Verified against kicad-cli by relabelling VIN to GND in ``good_ldo.kicad_sch`` --
    KiCad then reports two nets where there were three. There is no netlist in which a
    pin sits on two named nets, which is why looking for one never fired.
    """
    return build_netlist(
        [
            Net(
                "GND",
                frozenset({Node("R1", "1"), Node("R1", "2"), Node("C1", "1"), Node("C1", "2")}),
            )
        ],
        [Component("C1", "100uF"), Component("R1", "1k")],
    )


def test_forbid_catches_a_real_short_where_one_net_was_swallowed() -> None:
    """The regression: a declared-distinct net that vanished IS the short (LVS n:1 merge)."""
    spec = Spec(source="x", rules=[forbid("VIN", "GND")])
    report = check_spec(spec, _really_shorted())

    assert report.verdict == "fail"
    (result,) = report.failures
    assert result.status == "fail", "a swallowed net is a finding, not a non-result"
    assert "VIN" in result.detail
    assert "cannot be shorted" not in result.detail, "the old message said the opposite"


def test_forbid_on_nets_that_are_all_absent_still_fails() -> None:
    """A rule naming nothing real has not been satisfied; it was never tested."""
    spec = Spec(source="x", rules=[forbid("SDA", "SCL")])
    report = check_spec(spec, _rail())
    assert report.verdict == "fail"
    assert report.results[0].status == "fail"


# -- polarity: pins named the way KiCad names them -------------------------------------


def _diode(anode_on: str = "VCC", cathode_on: str = "GND"):
    """A diode whose pins carry KiCad's own function names, as Device:D does."""
    nets: dict[str, list[Node]] = {"VCC": [], "GND": []}
    nets[anode_on].append(Node("D1", "1", function="A"))
    nets[cathode_on].append(Node("D1", "2", function="K"))
    return build_netlist(
        [Net(name=k, nodes=frozenset(v)) for k, v in nets.items()],
        [Component("D1", "1N4148", lib_id="Device:D")],
    )


def test_polarity_accepts_pins_named_by_function() -> None:
    """Device:D calls its pins A and K. Resolving them is D12; net_of alone cannot."""
    spec = Spec(
        source="x", rules=[polarity("D1", plus="VCC", minus="GND", plus_pin="A", minus_pin="K")]
    )
    report = check_spec(spec, _diode())
    assert report.verdict == "pass", "a correctly wired diode must not raise a false alarm"


def test_polarity_by_function_still_catches_a_reversed_part() -> None:
    spec = Spec(
        source="x", rules=[polarity("D1", plus="VCC", minus="GND", plus_pin="A", minus_pin="K")]
    )
    report = check_spec(spec, _diode(anode_on="GND", cathode_on="VCC"))
    assert report.verdict == "fail"
    assert "REVERSED" in report.failures[0].detail


# -- a contract that asserts nothing ---------------------------------------------------


def test_a_contract_with_no_rules_is_not_green() -> None:
    """all([]) is True, so an empty contract used to exit 0 and protect nothing."""
    report = check_spec(Spec(source="x", rules=[]), _rail())
    assert report.verdict == "fail"
    assert any(
        "nothing" in r.detail.lower() or "no rules" in r.detail.lower() for r in report.results
    )


# -- the spec object itself ------------------------------------------------------------


def test_a_spec_needs_a_source() -> None:
    with pytest.raises(ValueError):
        Spec(source="")


def test_a_spec_rejects_a_non_rule() -> None:
    with pytest.raises(TypeError):
        Spec(source="x", rules=("not a rule",))  # type: ignore[arg-type]


def test_forbid_needs_two_nets() -> None:
    with pytest.raises(ValueError):
        forbid("VIN")


def test_require_no_floating_pins_is_opt_in() -> None:
    """The flag is off by default -- but the contract still has to assert something.

    This used to lean on an empty ``Spec`` being green, which conflated "did not ask
    for the floating check" with "asserted nothing at all". Only the first is opt-in.
    """
    floating = build_netlist(
        [
            Net("VIN", frozenset({Node("C1", "2")})),
            Net("unconnected-(C1-Pad1)", frozenset({Node("C1", "1")})),
        ],
        [Component("C1")],
    )
    rules = [net("VIN", ["C1.2"])]
    assert check_spec(Spec(source="x", rules=rules), floating).verdict == "pass"
    strict = Spec(source="x", rules=rules, require_no_floating_pins=True)
    assert check_spec(strict, floating).verdict == "fail"


def test_a_rule_type_with_no_checker_fails_rather_than_crashing() -> None:
    """Defensive: the boundary test prevents this in-repo, but a Spec is user code."""
    from dataclasses import dataclass

    from kicad_netspec.contract import Rule

    @dataclass(frozen=True)
    class Invented(Rule):
        def __str__(self) -> str:
            return "something netspec has never heard of"

    report = check_spec(Spec(source="x", rules=[Invented()]), _rail())
    assert report.verdict == "fail"
    assert "no way to check" in report.results[0].detail
