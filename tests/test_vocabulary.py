"""The two primitives the real-board analysis asked for, and neither survey tool has.

``through`` catches a series part being bypassed or paralleled -- a fuse, a ferrite, a
sense resistor, a net tie. ``mirrors`` catches one instance of a repeated block being
wired differently from another.

Both were prototyped against a real dual-channel board before being written here, which
is where their shape comes from: ``through``'s exclusivity clause is what makes a star
ground assertable, and ``mirrors`` needs no channel index because a structural bijection
says what string surgery was going to approximate.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from kicad_netspec.check import check_spec
from kicad_netspec.contract import Spec, mirrors, through
from kicad_netspec.model import Component, Net, Node, build_netlist


def _star_ground(bridge: str = "NT1", extra: bool = False):
    """Two grounds meeting through a net tie, optionally bridged a second time."""
    gnd = [Node("U1", "1"), Node(bridge, "1")]
    gndpwr = [Node("U2", "1"), Node(bridge, "2")]
    if extra:
        gnd.append(Node("R9", "1"))
        gndpwr.append(Node("R9", "2"))
    parts = [Component("U1"), Component("U2"), Component(bridge, lib_id="Device:NetTie_2")]
    if extra:
        parts.append(Component("R9", "0R"))
    return build_netlist([Net("GND", frozenset(gnd)), Net("GNDPWR", frozenset(gndpwr))], parts)


def _status(*rules, netlist):
    return [r.status for r in check_spec(Spec(source="b", rules=list(rules)), netlist).results]


# -- through ----------------------------------------------------------------------------


def test_a_net_tie_is_the_path_between_two_grounds() -> None:
    assert _status(through("GND", "NT1", "GNDPWR"), netlist=_star_ground()) == ["pass"]


def test_a_second_bridge_fails_by_default() -> None:
    """The star-ground assertion. A second path is a ground loop, and ERC allows it."""
    report = check_spec(
        Spec(source="b", rules=[through("GND", "NT1", "GNDPWR")]), _star_ground(extra=True)
    )
    assert report.verdict == "fail"
    assert "R9" in report.failures[0].detail


def test_a_second_bridge_is_allowed_when_you_say_so() -> None:
    spec = Spec(source="b", rules=[through("GND", "NT1", "GNDPWR", only=False)])
    assert check_spec(spec, _star_ground(extra=True)).verdict == "pass"


def test_a_part_that_does_not_bridge_the_two_nets_fails() -> None:
    assert _status(through("GND", "U1", "GNDPWR"), netlist=_star_ground()) == ["fail"]


def test_the_rule_is_symmetric_in_its_two_nets() -> None:
    """through(A, R, B) and through(B, R, A) are one assertion and must key alike."""
    assert (
        through("GND", "NT1", "GNDPWR").describe()["subject"]
        == through("GNDPWR", "NT1", "GND").describe()["subject"]
    )


def test_bridging_a_net_to_itself_is_refused() -> None:
    with pytest.raises(ValueError):
        through("GND", "NT1", "GND")


# -- mirrors ----------------------------------------------------------------------------


def _two_channels(second_differs: bool = False):
    """Two three-pin drivers, wired identically apart from their own signals."""
    nets = {
        "PWM1": [Node("U2", "1")],
        "PWM2": [Node("U3", "1")],
        "OUT1": [Node("U2", "2")],
        "OUT2": [Node("U3", "2")],
        "GND": [Node("U2", "3"), Node("U3", "3")],
    }
    if second_differs:
        # U3 pin 2 lands on its own PWM net instead of its own output: the channels no
        # longer have the same shape, though every net involved still exists.
        nets["OUT2"] = []
        nets["PWM2"].append(Node("U3", "2"))
    return build_netlist(
        [Net(k, frozenset(v)) for k, v in nets.items() if v],
        [Component("U2", lib_id="X:DRV"), Component("U3", lib_id="X:DRV")],
    )


def test_two_channels_wired_alike_mirror() -> None:
    assert _status(mirrors("U2", "U3"), netlist=_two_channels()) == ["pass"]


def test_a_channel_wired_differently_does_not_mirror() -> None:
    report = check_spec(Spec(source="b", rules=[mirrors("U2", "U3")]), _two_channels(True))
    assert report.verdict == "fail"


def test_mirroring_needs_no_channel_index() -> None:
    """A structural bijection, not string surgery: PWM1<->PWM2 pairs, GND maps to itself."""
    report = check_spec(Spec(source="b", rules=[mirrors("U2", "U3")]), _two_channels())
    assert report.results[0].status == "pass"


def test_parts_with_different_pins_do_not_mirror() -> None:
    netlist = build_netlist(
        [
            Net("A", frozenset({Node("U2", "1"), Node("U3", "1")})),
            Net("B", frozenset({Node("U2", "2")})),
        ],
        [Component("U2"), Component("U3")],
    )
    assert _status(mirrors("U2", "U3"), netlist=netlist) == ["fail"]


def test_a_part_absent_from_the_design_is_skipped_not_failed() -> None:
    """D9's taxonomy: a rule naming a part that is not there was not evaluated. Matches
    _check_polarity, which these two diverged from silently."""
    assert _status(mirrors("U2", "U9"), netlist=_two_channels()) == ["skipped"]
    assert (
        check_spec(Spec(source="b", rules=[mirrors("U2", "U9")]), _two_channels()).verdict == "fail"
    ), "skipped is still not green"


def test_mirrors_is_symmetric() -> None:
    assert mirrors("U2", "U3").describe()["subject"] == mirrors("U3", "U2").describe()["subject"]


def test_a_part_cannot_mirror_itself() -> None:
    with pytest.raises(ValueError):
        mirrors("U2", "U2")


# -- against a real KiCad design --------------------------------------------------------


def test_mirrors_pairs_the_two_channels_of_the_hierarchy_fixture() -> None:
    """A real hierarchical board: one sub-sheet instantiated twice, so R1 and R2 are the
    same part in two channels. Their nets differ by channel and pair one-to-one."""
    from pathlib import Path

    from kicad_netspec.parse import parse_kicadxml_file

    nl = parse_kicadxml_file(Path(__file__).parent / "fixtures" / "hierarchy.expected.xml")
    report = check_spec(Spec(source="h", rules=[mirrors("R1", "R2")]), nl)

    assert report.verdict == "pass"
    assert "2 nets paired" in report.results[0].detail


def test_mirrors_says_little_about_two_pin_parts() -> None:
    """A limitation, pinned so nobody mistakes a pass here for a strong statement.

    R1 sits between VIN and /Channel1/OUT; R3 between /Aux/IN and /Aux/SENSE, in an
    unrelated sheet. They still mirror, because two two-pin parts with a distinct net on
    each pin always induce a bijection. The rule is *true* and nearly vacuous: it earns
    its keep on multi-pin parts, where there is a shape to disagree about.
    """
    from pathlib import Path

    from kicad_netspec.parse import parse_kicadxml_file

    nl = parse_kicadxml_file(Path(__file__).parent / "fixtures" / "hierarchy.expected.xml")
    assert check_spec(Spec(source="h", rules=[mirrors("R1", "R3")]), nl).verdict == "pass"


# -- the two ways the bijection was unsound ---------------------------------------------


def _same_nets(u3_pins: Sequence[str]):
    """U2 on A,B,C at pins 1-3; U3 on the same three nets, in the order given."""
    nets: dict[str, list[Node]] = {
        "A": [Node("U2", "1")],
        "B": [Node("U2", "2")],
        "C": [Node("U2", "3")],
    }
    for pin, name in zip("123", u3_pins, strict=True):
        nets[name].append(Node("U3", pin))
    return build_netlist(
        [Net(k, frozenset(v)) for k, v in nets.items()],
        [Component("U2"), Component("U3")],
    )


def test_a_net_both_parts_use_must_line_up() -> None:
    """Without this the rule is graph isomorphism, invariant under relabelling, so every
    permutation passed -- a VCC/GND swap included."""
    import itertools

    passing = [
        p
        for p in itertools.permutations("ABC")
        if check_spec(Spec(source="b", rules=[mirrors("U2", "U3")]), _same_nets(p)).verdict
        == "pass"
    ]
    assert passing == [("A", "B", "C")], "only the identical wiring may mirror"


def test_a_pin_connected_to_nothing_does_not_mirror_a_wired_one() -> None:
    """KiCad names each floating pin uniquely, so to a bare bijection it was just another
    distinct net -- and a part with every pin unwired mirrored a fully wired one."""
    netlist = build_netlist(
        [
            Net("A", frozenset({Node("U2", "1")})),
            Net("B", frozenset({Node("U2", "2"), Node("U3", "2")})),
            Net("unconnected-(U3-IN-Pad1)", frozenset({Node("U3", "1")})),
        ],
        [Component("U2"), Component("U3")],
    )
    report = check_spec(Spec(source="b", rules=[mirrors("U2", "U3")]), netlist)
    assert report.verdict == "fail"
    assert "connected to nothing" in report.failures[0].detail


def test_a_net_a_designer_named_is_not_treated_as_floating() -> None:
    """`/Channel1/OUT` with one pin is a deliberate label on a sheet pin, not a dropped
    wire, and must still pair with its opposite number."""
    from pathlib import Path as _P

    from kicad_netspec.parse import parse_kicadxml_file

    nl = parse_kicadxml_file(_P(__file__).parent / "fixtures" / "hierarchy.expected.xml")
    assert check_spec(Spec(source="h", rules=[mirrors("R1", "R2")]), nl).verdict == "pass"


def test_mirroring_nothing_is_not_a_pass() -> None:
    """Two pinless parts, or two unwired ones, pair no nets. Green there is exactly what
    check.py's docstring calls "how a contract stops protecting anything"."""
    netlist = build_netlist(
        [Net("A", frozenset({Node("U1", "1")}))],
        [Component("U1"), Component("MH1"), Component("MH2")],
    )
    report = check_spec(Spec(source="b", rules=[mirrors("MH1", "MH2")]), netlist)

    assert report.results[0].status == "skipped"
    assert "nothing was compared" in report.results[0].detail
    assert report.verdict == "fail", "skipped is not green"


def test_a_permutation_passes_when_the_parts_share_no_net() -> None:
    """The limit, pinned so it cannot quietly be believed closed.

    The shared-net clause is the only anchor available, so two instances with per-channel
    supplies -- an isolated gate driver, a bootstrap leg -- have nothing to line up
    against and a swapped pair mirrors. Nothing in a netlist can fix that: the
    correspondence lives in a naming convention.
    """
    netlist = build_netlist(
        [
            Net("VDDA", frozenset({Node("U2", "1")})),
            Net("VSSA", frozenset({Node("U2", "2")})),
            Net("VDDB", frozenset({Node("U3", "2")})),  # swapped relative to U2
            Net("VSSB", frozenset({Node("U3", "1")})),
            Net("SIG", frozenset({Node("U2", "3"), Node("U3", "3")})),
        ],
        [Component("U2"), Component("U3")],
    )
    assert check_spec(Spec(source="b", rules=[mirrors("U2", "U3")]), netlist).verdict == "pass"
