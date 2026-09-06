"""The third verdict, and what each exit code means (A1, A2).

Two verdicts could not carry the difference between "the board is wrong" and
"netspec could not check it", so a rule that was skipped or unsupported made the
report say `fail` -- the org's founding property violated in the tool that exists
to enforce it. And exit 2 meant EXIT_USAGE here while meaning INCOMPLETE in
partspec, so a consumer branching on 2 across both read two different facts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_netspec import cli
from kicad_netspec.check import CheckReport, CheckResult, Status, check_spec
from kicad_netspec.contract import Spec, net, polarity
from kicad_netspec.model import Component, Net, Node, build_netlist


def _rail():
    return build_netlist(
        [
            Net("VIN", frozenset({Node("R1", "1"), Node("C1", "1")})),
            Net("GND", frozenset({Node("R1", "2"), Node("C1", "2")})),
        ],
        [Component("C1", "100uF"), Component("R1", "1k")],
    )


def _report(*statuses: Status) -> CheckReport:
    return CheckReport(results=tuple(CheckResult(rule=s, status=s) for s in statuses))


# -- the verdict -----------------------------------------------------------------------


@pytest.mark.parametrize("status", ["skipped", "unsupported"])
def test_a_rule_that_was_not_evaluated_is_incomplete_not_a_failure(status: Status) -> None:
    """The A2 defect: neither of these is a statement about the design."""
    assert _report("pass", status).verdict == "incomplete"


def test_a_real_violation_outranks_something_that_could_not_be_evaluated() -> None:
    """A finding stays a finding even when another rule could not be checked."""
    assert _report("fail", "skipped").verdict == "fail"


def test_every_rule_passing_is_still_pass() -> None:
    assert _report("pass", "pass").verdict == "pass"


def test_a_contract_that_asserts_nothing_is_a_failure_not_an_incomplete() -> None:
    """A contract with no rules is a defect in the contract, not something netspec
    was unable to determine. Deliberately not `incomplete`."""
    report = check_spec(Spec(source="x", rules=[]), _rail())
    assert report.verdict == "fail"


def test_a_skipped_rule_reaches_the_verdict_through_a_real_check() -> None:
    """Not only against hand-built results: a rule naming an absent part."""
    report = check_spec(Spec(source="x", rules=[polarity("C9", plus="VIN", minus="GND")]), _rail())
    assert [r.status for r in report.results] == ["skipped"]
    assert report.verdict == "incomplete"


# -- the exit codes --------------------------------------------------------------------


def test_exit_codes_match_partspec() -> None:
    """A1: the whole point is that these agree across the two tools."""
    assert cli.EXIT_OK == 0
    assert cli.EXIT_VIOLATION == 1
    assert cli.EXIT_INCOMPLETE == 2
    assert cli.EXIT_ENVIRONMENT == 4
    assert cli.EXIT_USAGE == 64


def test_usage_no_longer_collides_with_could_not_tell() -> None:
    """The conflict itself: 2 must not mean "you invoked me wrong" any more."""
    assert cli.EXIT_USAGE != cli.EXIT_INCOMPLETE


def test_every_verdict_has_an_exit_code() -> None:
    """A verdict with no mapping would raise KeyError at the moment of reporting."""
    from typing import get_args

    from kicad_netspec.check import Verdict

    assert set(get_args(Verdict)) == set(cli._EXIT)


def test_check_exits_2_when_it_could_not_evaluate_the_contract(tmp_path: Path, monkeypatch) -> None:
    """End to end: the CI gate must be able to tell this from exit 1."""
    contract = tmp_path / "c.py"
    contract.write_text(
        "from kicad_netspec import Spec, polarity\n"
        "board = Spec(source='b.kicad_sch', rules=[polarity('C9', plus='VIN', minus='GND')])\n"
    )
    monkeypatch.setattr("kicad_netspec.cli.Cli10Backend", lambda *a, **k: _FakeBackend())
    assert cli.main(["check", str(contract)]) == cli.EXIT_INCOMPLETE


def test_check_still_exits_1_on_a_real_violation(tmp_path: Path, monkeypatch) -> None:
    """Guard against everything becoming incomplete."""
    contract = tmp_path / "c.py"
    contract.write_text(
        "from kicad_netspec import Spec, net\n"
        # Both parts exist; VIN actually carries C1.1 too, so this is a real finding.
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1'])])\n"
    )
    monkeypatch.setattr("kicad_netspec.cli.Cli10Backend", lambda *a, **k: _FakeBackend())
    assert cli.main(["check", str(contract)]) == cli.EXIT_VIOLATION


def test_check_still_exits_0_when_every_rule_passes(tmp_path: Path, monkeypatch) -> None:
    contract = tmp_path / "c.py"
    contract.write_text(
        "from kicad_netspec import Spec, net\n"
        "board = Spec(source='b.kicad_sch', rules=[net('VIN', ['R1.1', 'C1.1'])])\n"
    )
    monkeypatch.setattr("kicad_netspec.cli.Cli10Backend", lambda *a, **k: _FakeBackend())
    assert cli.main(["check", str(contract)]) == cli.EXIT_OK


class _FakeBackend:
    def netlist(self, source, variant=None):
        return _rail()


# -- what a caller is told -------------------------------------------------------------


def test_the_mcp_meanings_cover_both_changed_codes() -> None:
    """An agent reads `meaning`, so 2 must no longer say "usage error"."""
    from kicad_netspec.mcp import _MEANING

    assert "could not evaluate" in _MEANING[2]
    assert _MEANING[64] == "usage error"
    assert cli.EXIT_INCOMPLETE in _MEANING
    assert cli.EXIT_USAGE in _MEANING


def test_the_report_says_why_it_was_incomplete() -> None:
    """`verdict: incomplete` beside `fail: 0` would otherwise be a puzzle."""
    from kicad_netspec.report import check_report

    doc = check_report(check_spec(Spec(source="x", rules=[net("VIN", ["R1.1", "C1.1"])]), _rail()))
    assert doc["verdict"] == "pass"

    skipped = check_spec(Spec(source="x", rules=[polarity("C9", plus="VIN", minus="GND")]), _rail())
    doc = check_report(skipped)
    assert doc["verdict"] == "incomplete"
    assert "could not be evaluated" in doc["verdict_reason"]
