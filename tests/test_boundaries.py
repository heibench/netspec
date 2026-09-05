"""Structural rules that keep netspec migratable (docs/DECISIONS.md D3, D4)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "kicad_netspec"


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_never_imports_pcbnew() -> None:
    """SWIG pcbnew is already deleted in KiCad master. Nothing may depend on it."""
    offenders = [
        p.relative_to(SRC) for p in _py_files() if "import pcbnew" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"pcbnew has no future in KiCad 11: {offenders}"


# Modules permitted to spawn a process for reasons unrelated to KiCad (D4.1).
# Adding to this list is a design change; argue for it in docs/DECISIONS.md first.
#
#   ops/run.py  runs the command `guard` was pointed at
#   mcp.py      runs the netspec CLI, one process per call (D18)
#   isolate.py  runs the contract in a child, so it cannot decide what the board is (D24)
SPAWN_EXEMPT = {"ops/run.py", "mcp.py", "isolate.py"}


def _in_oracle(path: Path) -> bool:
    return "oracle" in path.relative_to(SRC).parts


def _docstrings(tree: ast.AST) -> set[int]:
    """Ids of string constants that are docstrings, which may say anything."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                out.add(id(body[0].value))
    return out


def test_kicad_is_only_invoked_from_the_oracle() -> None:
    """No KiCad binary is named in runnable code outside oracle/ (D4).

    Docstrings are exempt: prose explaining the rule is not a breach of it. What this
    catches is a hardcoded command somewhere that should be going through the oracle.
    """
    offenders = []
    for p in _py_files():
        if _in_oracle(p):
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        exempt = _docstrings(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in exempt
                and ("kicad-cli" in node.value or "flatpak" in node.value)
            ):
                offenders.append((p.relative_to(SRC), node.value[:40]))
    assert not offenders, (
        f"KiCad must only be reachable through the Oracle protocol; found in {offenders}"
    )


# Names that start a process by any route. The plain-string check this replaced caught
# `__import__("subprocess")` for free and an AST-only version did not, so it is a union:
# real imports by AST, plus these as text. Weaker than what came before is not a fix.
_SPAWN_TEXT = (
    "subprocess",
    "os.system",
    "os.popen",
    "os.exec",
    "os.spawn",
    "os.posix_spawn",
    "multiprocessing",
    "pty.spawn",
)


def _spawns(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "subprocess" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom) and (node.module or "") == "subprocess":
            return True

    # Docstrings and comments explain why a module is safe, and mention the word doing
    # it, so prose is stripped before the text sweep rather than exempting whole files.
    stripped = ast.unparse(_without_docstrings(tree))
    return any(name in stripped for name in _SPAWN_TEXT)


def _without_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


def test_only_the_oracle_and_the_command_runner_spawn_processes() -> None:
    """Everything else works on the model and touches no process at all (D4.1)."""
    offenders = []
    for p in _py_files():
        rel = p.relative_to(SRC)
        if _in_oracle(p) or rel.as_posix() in SPAWN_EXEMPT:
            continue
        if _spawns(p.read_text(encoding="utf-8")):
            offenders.append(rel)
    assert not offenders, f"unexpected process spawning outside the oracle: {offenders}"


def test_the_command_runner_never_invokes_kicad() -> None:
    """`guard` runs whatever the user names. That must never be a KiCad binary.

    Checked against what the module actually references, not against the word "kicad" --
    the docstring is allowed to explain the rule it is subject to.
    """
    runner = SRC / "ops" / "run.py"
    if not runner.exists():
        return

    tree = ast.parse(runner.read_text(encoding="utf-8"))
    referenced = {
        node.value.lower()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    # Docstrings are constants too, so look only for something that could be executed.
    invocations = {s for s in referenced if "kicad-cli" in s or "flatpak" in s}
    assert not invocations, f"ops/run.py must not invoke KiCad: {invocations}"

    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert not any("oracle" in name for name in imports), (
        "ops/run.py must not reach into the oracle"
    )


def test_report_carries_no_tolerance() -> None:
    """Guard against cargo-culting partspec's interval model.

    Connectivity is discrete: two pins are connected or they are not. If a tolerance
    concept appears in the model, partspec's epistemics have leaked in (design/audit).
    """
    banned = ("tolerance", "approximate", "interval")
    offenders = []
    for p in _py_files():
        lowered = p.read_text(encoding="utf-8").lower()
        for word in banned:
            if word in lowered:
                offenders.append((p.relative_to(SRC), word))
    assert not offenders, f"netspec has no tolerances; connectivity is exact: {offenders}"


def test_every_rule_type_can_be_adjudicated() -> None:
    """The invariant that used to rot silently: a rule with no checker.

    Adding a primitive once meant editing six places, one of which (`Spec`'s isinstance
    tuple) failed at import with a message naming nothing useful. A rule type registered
    nowhere would previously have fallen through to `unsupported`, which is not green but
    is also not the truth -- netspec knowing no way to check something is a defect in
    netspec, not a limit of the backend.
    """
    from kicad_netspec.check import CHECKERS
    from kicad_netspec.contract import Rule

    unhandled = sorted(c.__name__ for c in Rule.__subclasses__() if c not in CHECKERS)
    assert not unhandled, f"rule types with no checker: {unhandled}"


def test_every_rule_type_declares_the_nets_it_names() -> None:
    """Resolution runs over `net_names()`. A rule that forgets it silently resolves none.

    That would not fail loudly -- it would skip the hierarchical and ambiguity handling
    for that rule and quietly compare raw strings, which is the D19 bug all over again.
    """
    from kicad_netspec.contract import Forbid, Net, Polarity, Rule

    expect = {Net: 1, Polarity: 2, Forbid: 2}
    for kind in Rule.__subclasses__():
        assert "net_names" in vars(kind), f"{kind.__name__} does not declare net_names()"

    assert Net("N", ("a.1",)).net_names() == ("N",)
    assert Polarity("C1", "VIN", "GND").net_names() == ("VIN", "GND")
    assert Forbid(("A", "B")).net_names() == ("A", "B")
    assert len(expect) <= len(Rule.__subclasses__())


def test_every_rule_type_can_appear_in_the_report() -> None:
    """A rule with no `kind` or no `subject` produces a result the report cannot key.

    Nothing would fail: it would land under kind "none" with an id derived from its
    English sentence, so it would silently stop being alignable across runs -- which is
    the one property D8 depends on. Guarded here rather than discovered later.
    """
    from kicad_netspec.contract import Rule

    for kind in Rule.__subclasses__():
        assert kind.kind, f"{kind.__name__} declares no kind slug"
        assert "describe" in vars(kind), f"{kind.__name__} does not declare describe()"


def test_every_rule_describes_a_subject_and_stays_json_safe() -> None:
    from kicad_netspec.contract import Forbid, Mirrors, Net, Polarity, Rule, Through, Unknown

    samples = [
        Net("VIN", ("R1.1",)),
        Polarity("C1", "VIN", "GND"),
        Forbid(("VIN", "GND")),
        Through(ref="F1", nets=("VIN", "VBUS")),
        Mirrors(parts=("U2", "U3")),
        Unknown(declared="something_from_a_future_netspec"),
    ]
    assert {type(s) for s in samples} == set(Rule.__subclasses__()), (
        "a rule type exists that this test does not sample"
    )

    for rule in samples:
        described = rule.describe()
        assert described.get("subject"), f"{rule.kind} describes no subject"
        json.dumps(described)  # a tuple or frozenset would raise here


def test_the_mcp_check_tool_states_the_contract_trust_boundary() -> None:
    """A contract owns the process, so the report is only as trustworthy as the contract.

    That cannot be fixed by validation and is not fixed here. An agent reading the tool
    description must be told, so this asserts the warning stays put until the day process
    isolation makes it untrue.
    """
    from kicad_netspec import mcp

    text = (SRC / "mcp.py").read_text(encoding="utf-8")
    assert "as trustworthy as the contract" in text
    assert mcp.build_server  # the tool this describes still exists
