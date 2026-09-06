"""The machine-readable report D2 promises.

D2 states netspec's product as "a persisted, schema'd report and an exit code that gates
CI". Until this module there was only the exit code: a result's ``rule`` was a rendered
English sentence and no command emitted JSON. Three primitives of prose an agent can
read; the vocabulary being built here has nine, and it cannot.

**Every result carries an ``id``, and that is the load-bearing part.** D8 answers the
objection "an agent can silently weaken an assertion" with "a semantic report diff that
reports removed assertions" -- which only works if two reports can be *aligned*. The id
is ``kind:subject``: stable across runs, stable when the assertion is edited, different
when it is about something else. So a rule that has been deleted goes missing from the
id set, while a rule that has been quietly weakened keeps its id and changes its body.
Those are different findings and a report that cannot tell them apart answers nothing.

The rendered sentence is not thrown away; it is demoted to one field, ``text``, among
the fields a machine actually wants.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from kicad_netspec import __version__
from kicad_netspec.check import CheckReport, CheckResult
from kicad_netspec.model import Netlist

__all__ = ["REPORT_SCHEMA", "check_report", "design_digest"]

REPORT_SCHEMA = 1
"""Bumped when the document's shape changes in a way a reader could trip over."""

_STATUSES = ("pass", "fail", "unsupported", "skipped")


def design_digest(netlist: Netlist) -> str:
    """A stable fingerprint of the connectivity netspec actually read.

    Over the **design**, not the file and not the snapshot. A snapshot embeds the
    absolute ``source`` path and the KiCad version, so hashing one gave the same board
    two digests when it was read from two checkouts -- which breaks the only workflow
    this exists for: recompute from the committed files and compare. It covers every
    sheet of a hierarchical design, because it is taken over KiCad's fully expanded
    netlist rather than the root file.

    It exists because isolation (D24) cannot stop a contract swapping the schematic on
    disk before the parent reads it. Recording this makes such a swap **detectable**.
    Detection, not prevention, and D24 says so.
    """
    canonical = json.dumps(
        {
            "components": [
                [c.ref, c.value, c.footprint, c.lib_id]
                for c in sorted(netlist.components.values(), key=lambda c: c.ref)
            ],
            "nets": [
                [net.name, sorted(f"{n.ref}.{n.pin}" for n in net.nodes)]
                for net in sorted(netlist.nets.values(), key=lambda n: n.name)
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_report(
    report: CheckReport,
    *,
    contract: str = "",
    name: str = "",
    netlist: Netlist | None = None,
) -> dict[str, Any]:
    """Render an adjudicated contract as a JSON-safe document.

    ``contract`` and ``name`` identify where the assertions came from. Without them two
    entirely different contracts produce byte-identical reports, which is a poor footing
    for a document whose stated job (D8) is noticing that a contract was tampered with.
    """
    counts = {s: len(report.of_status(s)) for s in _STATUSES}
    return {
        "schema": REPORT_SCHEMA,
        "netspec": __version__,
        "command": "check",
        "contract": contract,
        "name": name,
        "source": report.source,
        "design_digest": design_digest(netlist) if netlist is not None else "",
        "kicad_version": report.kicad_version,
        "verdict": report.verdict,
        "verdict_reason": _why(report.verdict, counts),
        "counts": counts,
        "results": [_result(r) for r in report.results],
    }


def _why(verdict: str, counts: dict[str, int]) -> str:
    """Say why, because ``verdict: fail`` beside ``fail: 0`` is otherwise a puzzle.

    Only ``pass`` is green (D9): a skipped rule was not evaluated and an unsupported one
    could not be, and neither has been satisfied. The text format prints that note; the
    machine format used to leave a reader to infer it.
    """
    if verdict == "pass":
        return "every rule passed"
    if not any(counts.values()):
        return "the contract asserted nothing"
    not_green = [s for s in ("fail", "skipped", "unsupported") if counts[s]]
    lead = "could not be evaluated: " if verdict == "incomplete" else "not green: "
    return lead + ", ".join(f"{counts[s]} {s}" for s in not_green)


def _result(result: CheckResult) -> dict[str, Any]:
    data = dict(result.data)
    kind = str(data.pop("kind", "") or "unknown")
    subject = str(data.pop("subject", ""))

    # The rule's own fields go in first: spreading them last let a third-party rule
    # returning {"status": "pass"} from describe() overwrite its actual adjudication.
    return {
        **data,
        "id": _identify(kind, subject, result.rule),
        "kind": kind,
        "subject": subject,
        "status": result.status,
        "detail": result.detail,
        "text": result.rule,
    }


def _identify(kind: str, subject: str, text: str) -> str:
    """``kind:subject`` where there is one, else a digest of the rendered sentence.

    Ordinary rules always have both, and ``Spec`` refuses two rules sharing them, so an
    id is a real key rather than a hopeful one. The fallback is for a result that
    corresponds to no rule at all -- a rule type with no checker (``unknown``) -- which
    still needs an id that will not collide with the next one.
    """
    if subject:
        return f"{kind}:{subject}"
    return f"{kind}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
