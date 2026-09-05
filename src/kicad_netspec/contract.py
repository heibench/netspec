"""Declared intent: what the design is *supposed* to be.

A contract is Python (DECISIONS D8), not a sidecar data file. It is more expressive, it
needs no schema to design or version, and it avoids the well-documented trap of inventing
a policy DSL.

The consequence is that ``netspec check`` **imports and executes** the module you name.
A contract is code. ``netspec diff`` executes nothing.

Pins are named by function wherever the symbol offers one -- ``U1.VI`` rather than
``U1.3`` (DECISIONS D12). Pin *numbers* are what the known schematic-writer bugs corrupt,
so an assertion written against a number can be satisfied by the very defect it was meant
to catch.

    from kicad_netspec import Spec, forbid, mirrors, net, polarity, through

    board = Spec(
        source="hardware/board.kicad_sch",
        rules=[
            net("VIN",  ["J1.1", "C1.1", "U1.VI"]),
            net("+3V3", ["U1.VO", "C2.1"]),
            polarity("C1", plus="VIN", minus="GND"),
            forbid("VIN", "GND"),
            through("GND", "NT1", "GNDPWR"),   # the only path between two grounds
            mirrors("U2", "U3"),               # two channels, wired the same
        ],
    )
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

__all__ = [
    "Forbid",
    "Mirrors",
    "Net",
    "Through",
    "Unknown",
    "Polarity",
    "Rule",
    "Spec",
    "forbid",
    "mirrors",
    "net",
    "polarity",
]


class Rule:
    """One assertion in a contract.

    Rules are **pure data**. A rule declares what must be true and knows nothing about
    netlists or about how it is checked; adjudication lives in
    :mod:`kicad_netspec.check`, which registers one checker per rule type. That split is
    load-bearing: a contract module is user code that ``netspec check`` executes, and it
    has no business importing the machinery that judges it.

    Adding a primitive is a frozen dataclass inheriting this, a constructor function
    below, and a ``@checks`` handler in ``check.py``. Two tests in ``test_boundaries``
    fail if either half is missing.
    """

    kind: ClassVar[str] = ""
    """Stable slug naming this rule type in the machine-readable report.

    Deliberately not the class name: a report is a persisted artifact (D2) and a rename
    in here must not silently re-key someone's stored report.
    """

    def net_names(self) -> tuple[str, ...]:
        """Every net this rule names, in the words the contract used.

        Resolution (D19) uses this to canonicalise a hierarchical name and to refuse an
        ambiguous one *before* the rule runs. A rule that names nets and does not report
        them here opts out of both, silently, and goes back to comparing raw strings.
        Returning ``()`` is correct only for a rule that names no nets at all.
        """
        return ()

    def describe(self) -> dict[str, Any]:
        """This rule as JSON-safe data, for the report.

        Must include ``subject``: the one thing the rule is about, which together with
        ``kind`` identifies the assertion across runs. That identity is what lets two
        reports be aligned so a *removed* assertion is distinguishable from an edited
        one -- the objection D8 answers. Everything else in the returned mapping is the
        rule's strength, and is expected to change when someone weakens it.

        No tuples, no frozensets: this is serialised verbatim.
        """
        return {}


@dataclass(frozen=True)
class Net(Rule):
    """These pins, and by default *only* these pins, are on this net."""

    name: str
    pins: tuple[str, ...]
    exact: bool = True
    """When True, an unexpected extra pin on the net is a failure.

    Exact by default: a contract that only checks for presence cannot notice a stray
    connection, and a stray connection is a short.
    """

    kind: ClassVar[str] = "net"

    def __post_init__(self) -> None:
        # On the dataclass, not only in net(): Net is exported, so validating in the
        # helper alone left `Net(name="VIN", pins=())` as a way in.
        if not self.name:
            raise ValueError("a net rule needs a net name")
        if not self.pins:
            raise ValueError(f"net {self.name!r} lists no pins, so it asserts nothing")

    def net_names(self) -> tuple[str, ...]:
        return (self.name,)

    def describe(self) -> dict[str, Any]:
        return {"subject": self.name, "pins": list(self.pins), "exact": self.exact}

    def __str__(self) -> str:
        kind = "exactly" if self.exact else "at least"
        return f"net {self.name} carries {kind} {', '.join(self.pins)}"


@dataclass(frozen=True)
class Polarity(Rule):
    """A polarised part is the right way round.

    Reversing an electrolytic capacitor, a diode or an LED is *legal wiring*: ERC has no
    rule against it and the netlist looks healthy. This is the assertion that catches it.
    """

    ref: str
    plus: str
    minus: str
    plus_pin: str = "1"
    minus_pin: str = "2"
    """KiCad's convention for two-pin polarised symbols. Override for parts that differ."""

    kind: ClassVar[str] = "polarity"

    def net_names(self) -> tuple[str, ...]:
        return (self.plus, self.minus)

    def describe(self) -> dict[str, Any]:
        return {
            "subject": self.ref,
            "plus": self.plus,
            "minus": self.minus,
            "plus_pin": self.plus_pin,
            "minus_pin": self.minus_pin,
        }

    def __str__(self) -> str:
        return (
            f"{self.ref} pin {self.plus_pin} on {self.plus}, pin {self.minus_pin} on {self.minus}"
        )


@dataclass(frozen=True)
class Forbid(Rule):
    """These nets must never be the same net.

    The assertion for a short that would otherwise read as a perfectly ordinary net.
    """

    nets: tuple[str, ...]

    kind: ClassVar[str] = "forbid"

    def __post_init__(self) -> None:
        # On the dataclass for the reason Net's is: Forbid is exported, and the JSON
        # rebuild in isolate.py is a second constructor the forbid() helper never sees.
        # `Forbid(nets=())` adjudicated as pass while asserting nothing.
        if len(self.nets) < 2:
            raise ValueError("a forbid rule needs at least two nets to keep apart")

    def net_names(self) -> tuple[str, ...]:
        return self.nets

    def describe(self) -> dict[str, Any]:
        # Sorted throughout, not just in the subject: forbid(A, B) and forbid(B, A) are
        # one assertion. Canonicalising the key but leaving the body unsorted made the
        # two align and then report a change, which cancels the benefit exactly.
        # JSON-encoded rather than joined on a separator: KiCad accepts "|" (and ":")
        # inside a net name, so `forbid("A|B", "C")` and `forbid("A", "B|C")` joined to
        # the same string and keyed to one id -- two different assertions, one key.
        ordered = sorted(self.nets)
        return {"subject": json.dumps(ordered), "nets": ordered}

    def __str__(self) -> str:
        return f"{' and '.join(sorted(self.nets))} must stay separate"


@dataclass(frozen=True)
class Through(Rule):
    """Two nets are joined, and joined **through this part**.

    The assertion for a series element that is meant to be the only path between two
    nets: a fuse, a ferrite, a sense resistor, a net tie separating a logic ground from a
    power ground. Bypassing one, or paralleling it, leaves a netlist that reads as
    perfectly ordinary -- both nets exist, everything is connected, ERC is silent.

    **This asserts pin membership, not conduction.** A netlist says which pins are on
    which nets and nothing about what a part does between them, so a four-pin package
    with a pin on each net satisfies this whether it is a resistor or an optocoupler.
    Reading it as "current flows here" is the reader's inference, not netspec's claim.

    ``only=True`` by default, because that is what makes a star ground assertable: a
    second component bridging the same two nets is a ground loop, and it is legal wiring.
    Set it False where parts really do sit in parallel.

    **``only`` sees single-component bridges.** A path through two parts in series --
    ``GND -R8- MID -R9- GNDPWR`` -- is a ground loop it will not report. Searching
    further was tried and rejected: on a real board, a two-component search finds
    ``GND -U1- +12V -J1- GNDPWR`` and calls a correct design looped, because every rail
    reaches every other through the power tree. A check that fires on good boards is
    worse than one with a stated edge.
    """

    ref: str
    nets: tuple[str, str]
    only: bool = True

    kind: ClassVar[str] = "through"

    def __post_init__(self) -> None:
        # Arity on the dataclass, like Forbid's: it is exported and isolate.py rebuilds
        # it from JSON, so the helper is not the only way in. Unguarded, a three-net
        # tuple reached __str__ and crashed with a traceback under exit 1.
        if len(self.nets) != 2:
            raise ValueError(f"through() joins exactly two nets, got {len(self.nets)}")
        if self.nets[0] == self.nets[1]:
            raise ValueError(f"through() needs two different nets, got {self.nets[0]!r} twice")
        if not self.ref:
            raise ValueError("through() needs a part to bridge them")

    def net_names(self) -> tuple[str, ...]:
        return self.nets

    def describe(self) -> dict[str, Any]:
        # Sorted, like forbid: "R bridges A and B" and "R bridges B and A" are one
        # assertion and must not read as two when two reports are aligned.
        ordered = sorted(self.nets)
        return {
            "subject": json.dumps([self.ref, *ordered]),
            "part": self.ref,
            "nets": ordered,
            "only": self.only,
        }

    def __str__(self) -> str:
        a, b = self.nets
        sole = " and only through it" if self.only else ""
        return f"{a} reaches {b} through {self.ref}{sole}"


@dataclass(frozen=True)
class Mirrors(Rule):
    """Two parts are wired to the same shape.

    For a design built from a repeated block -- a dual driver, a per-phase leg, a bank of
    identical sensors -- this says the instances agree. It is the highest-leverage thing
    a contract can say per character, because one line covers a whole channel.

    Structural, not textual: the parts mirror when the pin-wise mapping between their
    nets is a **bijection**, and a net carried by *both* parts maps to itself. Pin 1 of
    each may sit on different nets -- that is the point of a channel -- so long as every
    pin agrees about which net of the other it corresponds to. That needs no channel
    index and no string surgery on net names, which the first design of this rule
    required.

    The shared-net clause is load-bearing and was missing at first. Without it the rule
    is graph isomorphism, which is invariant under relabelling, so **every** permutation
    of one part's pin-to-net map passed -- including a VCC/GND swap, the exact defect
    class this project exists to catch.

    A pin connected to nothing does not pair with a wired one either. KiCad names each
    floating pin uniquely (``unconnected-(U3-IN-Pad1)``), so to a bare bijection a
    dangling pin was just another distinct net and a part with every pin unwired mirrored
    a fully wired one.

    It compares two *parts*. A block is several rules, one per corresponding pair; it
    cannot see a change that leaves both instances equally wrong.

    **The shared-net clause is the only anchor there is, so where two instances share no
    net this rule is pure isomorphism and a permuted channel passes.** Every permutation
    of one part's pin-to-net map, at every pin count:

    ======  ==========================  ==========================
    pins    no net shared               two nets shared
    ======  ==========================  ==========================
    3       6/6 permutations pass       1/6 pass
    4       24/24 pass                  2/24 pass
    8       40320/40320 pass            720/40320 pass
    ======  ==========================  ==========================

    Per-channel supplies are not exotic -- an isolated gate driver, a bootstrap
    half-bridge leg, a per-phase floating rail -- and two such instances share nothing, so
    a VCC/GND swap between them mirrors happily. Nothing in a netlist can anchor them:
    the correspondence lives in a naming convention, and reading one would be the string
    surgery this rule exists to avoid. **Use it where instances share a rail**, which is
    the common case and the one the real board it was built against has.

    It also **pairs pins by number**, not by function -- the one rule in this vocabulary
    that does, against the advice at the top of this module. Two instances of the same
    die in differently numbered symbols mirror while their functions disagree.

    A cost worth knowing: two instances that touch each other cannot mirror, because the
    net linking them is shared and so must map to itself. Cascaded gain stages and
    resistor ladders are excluded by design.
    """

    parts: tuple[str, str]

    kind: ClassVar[str] = "mirrors"

    def __post_init__(self) -> None:
        if len(self.parts) != 2:
            raise ValueError(f"mirrors() compares exactly two parts, got {len(self.parts)}")
        if self.parts[0] == self.parts[1]:
            raise ValueError(f"{self.parts[0]!r} always mirrors itself")

    def net_names(self) -> tuple[str, ...]:
        return ()

    def describe(self) -> dict[str, Any]:
        return {"subject": json.dumps(sorted(self.parts)), "parts": sorted(self.parts)}

    def __str__(self) -> str:
        a, b = self.parts
        return f"{a} and {b} are wired to the same shape"


@dataclass(frozen=True)
class Unknown(Rule):
    """A rule this netspec has no vocabulary for, carried so it can be reported.

    Reached only through the isolated loader (D24): a contract may define its own
    ``Rule`` subclass, which the parent process has never imported. Before this, that
    raised and the CLI reported an *environment* fault -- exit 4, "this says nothing
    about your design" -- when the truth is a statement about the contract. It now
    adjudicates as a failure, which is what the in-process path always did.
    """

    declared: str

    kind: ClassVar[str] = "unknown"

    def net_names(self) -> tuple[str, ...]:
        return ()

    def describe(self) -> dict[str, Any]:
        return {"subject": self.declared}

    def __str__(self) -> str:
        return f"a {self.declared!r} rule, which this netspec does not understand"


def _merge(first: Rule, second: Rule) -> Rule | None:
    """Combine two rules about one subject, or None when they genuinely conflict.

    Two ``exact=False`` net rules compose by union -- "at least A" and "at least B" is
    "at least A, B" -- and refusing them would break a real shape: a contract assembled
    from per-subsystem rule lists, where two independently authored blocks each name
    their own pins on a shared rail and neither can know the other's. Anything else is a
    contradiction, and the id can only be a key if one subject means one assertion.
    """
    if isinstance(first, Net) and isinstance(second, Net) and not (first.exact or second.exact):
        return Net(
            name=first.name,
            pins=first.pins + tuple(p for p in second.pins if p not in first.pins),
            exact=False,
        )
    return None


@dataclass(frozen=True)
class Spec:
    """One design and everything asserted about it."""

    source: str
    """Path to the schematic, relative to the contract file or absolute."""

    rules: Sequence[Rule] = ()
    """Any sequence; normalised to a tuple so a Spec stays hashable.

    Declared as a Sequence because a contract is written by hand and a list literal is
    the natural way to write one. Requiring a trailing comma to make a tuple would be a
    tax on the thing this project most wants people to write.
    """

    name: str = ""
    variant: str | None = None

    require_no_floating_pins: bool = False
    """Fail if any pin is connected to nothing.

    Off by default: real designs legitimately leave pins unconnected, and KiCad's own
    no-connect flags are the right way to declare that. Turn it on for a board where you
    have marked every intentional one.
    """

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("a Spec needs a source schematic")
        seen: dict[tuple[str, str], Rule] = {}
        for rule in self.rules:
            if not isinstance(rule, Rule):
                raise TypeError(f"not a rule: {rule!r}")
            # The report keys a result by kind:subject so two runs can be aligned and a
            # deleted assertion told from a weakened one (D23). Two rules sharing that
            # key destroy the property -- and worse, they are how an agent could smuggle
            # a weak assertion in beside a strong one, since the obvious id-keyed
            # comparator keeps only the last. Refuse rather than let the guarantee rot.
            if rule.kind == "spec":
                raise ValueError(
                    f"{type(rule).__name__} claims kind 'spec', which netspec reserves "
                    "for its own Spec-level findings"
                )
            # Normalised exactly as the report normalises it, so "" and "unknown" cannot
            # be two keys here and one there.
            key = (rule.kind or "unknown", str(rule.describe().get("subject", "")))
            if key in seen:
                merged = _merge(seen[key], rule)
                if merged is None:
                    raise ValueError(
                        f"two {key[0]} rules about {key[1]!r}: {seen[key]} / {rule}. "
                        "A contract states one thing per subject."
                    )
                seen[key] = merged
                continue
            seen[key] = rule
        object.__setattr__(self, "rules", tuple(seen.values()))

    def __str__(self) -> str:
        return f"Spec({self.name or self.source}, {len(self.rules)} rules)"


# -- constructors, so a contract reads as a list rather than as class instantiation ----


def net(name: str, pins: Iterable[str], *, exact: bool = True) -> Net:
    """Assert which pins are on a net.

    ``exact=False`` asserts only that the listed pins are present, allowing others.
    """
    # "carries at least nothing" is true of every net and "carries exactly nothing" of
    # none, so a rule with no pins cannot discriminate; Net.__post_init__ refuses both.
    return Net(name=name, pins=tuple(pins), exact=exact)


def polarity(
    ref: str, *, plus: str, minus: str, plus_pin: str = "1", minus_pin: str = "2"
) -> Polarity:
    """Assert a polarised part is the right way round."""
    return Polarity(ref=ref, plus=plus, minus=minus, plus_pin=plus_pin, minus_pin=minus_pin)


def forbid(*nets: str) -> Forbid:
    """Assert two or more nets never merge."""
    return Forbid(nets=tuple(nets))


def through(a: str, ref: str, b: str, *, only: bool = True) -> Through:
    """Assert that ``a`` reaches ``b`` through ``ref``, and by default only through it."""
    return Through(ref=ref, nets=(a, b), only=only)


def mirrors(a: str, b: str) -> Mirrors:
    """Assert two parts are wired to the same shape."""
    return Mirrors(parts=(a, b))


def load(path: str, *, attribute: str | None = None) -> Spec:
    """Import a contract module and return the :class:`Spec` it defines.

    ``path`` may be ``contract.py`` or ``contract.py:name``. With no name, the module
    must define exactly one ``Spec`` at module level, so there is nothing to guess about.

    This executes the module. See the note at the top of this file.
    """
    import importlib.util
    from pathlib import Path

    target, _, named = path.partition(":")
    attribute = attribute or named or None

    file = Path(target).expanduser().resolve()
    if not file.is_file():
        raise FileNotFoundError(f"no contract at {file}")

    spec = importlib.util.spec_from_file_location(file.stem, file)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {file}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if attribute:
        found = getattr(module, attribute, None)
        if not isinstance(found, Spec):
            raise ValueError(f"{file}:{attribute} is not a Spec")
        return found

    specs = [v for k, v in vars(module).items() if isinstance(v, Spec) and not k.startswith("_")]
    if not specs:
        raise ValueError(f"{file} defines no Spec")
    if len(specs) > 1:
        raise ValueError(f"{file} defines {len(specs)} Specs; name one, e.g. {file.name}:board")
    return specs[0]


def resolve_source(spec: Spec, contract_path: str | None = None) -> str:
    """Turn a Spec's ``source`` into a usable path, relative to the contract file."""
    from pathlib import Path

    source = Path(spec.source).expanduser()
    if source.is_absolute() or contract_path is None:
        # Resolved like the relative branch. The report records the path it read, and a
        # contract can create a symlink, so an unresolved absolute path would name the
        # link while the design came from somewhere else (D24).
        return str(source.resolve())
    base = Path(contract_path.partition(":")[0]).expanduser().resolve().parent
    return str((base / source).resolve())
