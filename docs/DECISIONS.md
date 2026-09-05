# Decisions

Numbered, dated, and load-bearing. Each records what was decided and *why*, so it can be
revisited deliberately rather than re-litigated by accident. Follows the convention in
[`partspec`](https://github.com/CameronBrooks11/partspec).

---

## D1 — Scope: personal OSS, dogfooded

Public from the start, Apache-2.0, to the same house scaffold and standards as `partspec`,
`gerberdiff` and `scadman`. Dogfooded on real boards before it is promoted anywhere.

## D2 — Product boundary: the stateless gate, not the authoring loop

**netspec never writes to a design file.** Not schematics, not boards, not libraries.

Its product is a persisted, schema'd report and an exit code that gates CI. It holds no
session state and owns no edit loop. Adjacent projects own authoring — Konnect drives a
live KiCad, SKiDL and atopile generate from code, a human uses the GUI. netspec is the
gate their output must pass.

This is `partspec`'s D18, adopted verbatim in substance, and it is what keeps the project
small. Every silent-corruption failure in the survey behind this project lives in a
*writer*; declining to be one removes most of the risk surface and most of the work.

Consequence: an MCP server is in scope (D7) because the boundary is about state and loop
ownership, not transport. Its tools are stateless verbs — every call re-reads from disk.

## D3 — Never depend on SWIG `pcbnew`

`pcbnew/python/swig` exists on the KiCad 10.0 branch and is **already deleted** in master
— zero `.i` files, no `KICAD_SCRIPTING` cmake option. This is an absence, not a
deprecation warning.

Enforced twice: a ruff `banned-api` rule on the `pcbnew` import, and
`tests/test_boundaries.py::test_never_imports_pcbnew`.

Cost accepted: this rules out `kinet2pcb`, and therefore SKiDL's `generate_pcb()`, as
anything netspec depends on. Fine — netspec does not generate boards.

## D4 — KiCad only leaks in through the Oracle protocol

One `Protocol` with two implementations: `Cli10Backend` (subprocess `kicad-cli`, ships
first) and a later `Ipc11Backend`. No `kicad-cli` string and no `subprocess` import
outside `oracle/`, enforced by
`tests/test_boundaries.py::test_kicad_only_leaks_in_through_the_oracle`.

The protocol is deliberately shaped like KiCad 11's own commands
(`GetSchematicNetlist`, `RunSchematicJobExport*`, `ImportNetlist`) so the second backend
is a mapping, not a redesign.

## D5 — Ship on KiCad 10; treat KiCad 11 as a backend, never a prerequisite

Every capability in v0 is a `kicad-cli` subcommand that exists and has been measured:
`sch export netlist --format kicadxml`, `sch erc --format json`,
`pcb drc --format json --schematic-parity`. Netlist export measures 0.44–0.61 s, which is
what makes per-mutation verification affordable.

Nothing is deferred to a release that has not happened. KiCad 11's milestone is
Feb 2027–Feb 2028; that is treated as unknown timing, not a schedule.

## D6 — Plan only against KiCad facts verified in master, and re-verify in CI

KiCad's public roadmap wiki is **stale** — it still describes "a Python wrapper to hide
the SWIG generated API", superseded years ago. It must not be used for planning.

Every KiCad 11 fact this project leans on was read out of `master` by counting
`registerHandler<>` calls, and is pinned in `tests/test_kicad_assumptions.py`. A weekly CI
job re-runs it against KiCad master, so an upstream revert arrives as a red build rather
than a discovery in 2027. Verified to fail correctly against the 10.0 branch.

## D7 — CLI first, MCP later, as an optional extra

The CLI and its report are the product. The MCP server is an `mcp` extra whose tools are
stateless verbs over the same code path. `mcp` is pinned `>=1.27,<2`: nine of twenty-two
servers in the survey are dead on a fresh install because they pinned `mcp>=1.x` with no
upper bound and `mcp` 2.x renamed `FastMCP`.

Target: under 3K tokens of `tools/list` schema, measured in CI, failing the build on
regression.

## D8 — Contract in Python, not sidecar YAML

Carried from `partspec` D6. More expressive, no schema to design or version, and it
avoids the policy-DSL trap. The "an agent can silently weaken an assertion" objection is
answered the same way it is in partspec — a semantic report diff that reports `removed:`
assertions — which a YAML schema would not do better.

Inherits partspec's "a contract is code" consequence: `netspec check` imports and executes
the module it is given. `diff` executes nothing.

## D9 — Four statuses, and no tolerance anywhere

`pass · fail · unsupported · skipped`. Only `pass` is green.

partspec has a fifth, `approximate`, for when a measured interval straddles a threshold.
**It is deliberately absent here.** Connectivity is discrete: two pins are connected or
they are not. There is no error interval, so the status would be unreachable, and
importing partspec's interval epistemics into an exact domain would add concepts without
adding truth.

Guarded by `tests/test_boundaries.py::test_report_carries_no_tolerance`. If the words
*tolerance*, *approximate* or *interval* ever appear in the model, something has gone
wrong.

`unsupported` survives, for a check that needs a capability the active backend lacks.

## D10 — An environment fault is never a failing board

Adopted from partspec, and the most valuable thing taken from it.

No `kicad-cli` on `PATH`, a KiCad that will not start, a source file that is not there —
these are `verdict: "error"`, a distinct exit code, every check `skipped`. They are **not**
statements about the design. A CI run on a machine without KiCad must never report a board
as disproven.

Carried in the report as a field a consumer can branch on, not as prose in a message.

## D11 — Nets are compared structurally, not by name

KiCad auto-names unlabelled nets after their own contents (`Net-(C1-Pad1)`), so changing
connectivity also changes the name. A name-keyed diff would report noise and miss the real
edit.

Nets are classified **named** (a label a human wrote) or **anonymous** (auto-generated).
Named nets diff by name; anonymous nets diff by node-set identity. A pure rename with no
structural change is reported as benign; a structural change under a stable name is
reported loudly.

### D11.1 — A one-node net is a floating pin, not a net (amendment, Phase 2)

KiCad reports every unconnected pin as a net of one node. Diffing those as nets turns a
single edit into a removal plus an addition: moving one connection between two pins of a
part produced *three* reported changes, which is precisely the noise D11 exists to
prevent.

Single-node nets are therefore excluded from net matching and reported as a separate
floating-pin delta -- ``now_floating`` and ``no_longer_floating``. One edit reads as one
change, and "7 pins came free" is more legible than seven phantom net additions.

## D12 — Intent is expressed against pin *functions* where possible

KiCad's `kicadxml` netlist carries `pinfunction` and `pintype` per node. A contract says
`U1.VI`, not `U1.3`.

This matters because pin *numbers* are exactly what the surveyed bugs corrupt: a
sign-flipped pin-position helper wires pin 2 where pin 1 was asked for, silently reversing
a polarised capacitor. An assertion written against the function survives that, and
survives a library renumbering.

## D13 — Rule checks run at `--severity-all` by default

`kicad-cli pcb drc --schematic-parity` at default severity reports **0 violations and 0
parity issues on a board carrying 147**, because KiCad defaults
`footprint_symbol_mismatch` to `warning` (`board_design_settings.cpp`) and tooling that
fails only on errors goes green.

This was found in a real repo where it hid a schematic/PCB mismatch for months with green
CI, and reproduced here on KiCad's own demo board — so it is a property of KiCad's
defaults, not of any one project.

Therefore: netspec passes `--severity-all`, reports severity per finding, and lets the
caller decide what fails — explicitly and in the report. It never infers "clean" from an
exit code; it parses the JSON and counts. A `doctor` check flags any `.kicad_pro` relying
on KiCad's defaults for the parity rules.

### D4.1 — The Oracle boundary bans *KiCad*, not `subprocess` (amendment, Phase 3)

``tests/test_boundaries.py`` originally banned the string ``subprocess`` anywhere outside
``oracle/``. That is the right rule for the wrong reason: what must not leak is *KiCad*,
so that the IPC backend stays one file.

``guard`` has to run an arbitrary command the user names -- an agent, a build script, a
generator -- and that is not KiCad leaking in. The rule is narrowed to name explicit
exemptions, which may spawn a process but must never invoke a KiCad binary. Widening the
list is a design change and is argued for here:

* ``ops/run.py`` — runs the command ``guard`` was pointed at.
* ``mcp.py`` — runs the netspec CLI, one process per call (D18). It does not reach the
  oracle directly and names no KiCad binary; KiCad is still reached only through
  ``oracle/``, one process further down. The test that caught this addition is the
  reason the exemption is a list rather than a blanket allowance.

## D15 — A pin swap's significance comes from the part, not the topology

**Measured, and it overturned the first design.**

A reversed polarised capacitor and a deliberately re-pinned connector produce an
*identical* diff: two pins of one component trading nets. The first implementation
flagged any 1↔2 move as a probable defect. A read-only dry run over a real board's
history — 22 net changes across a genuine re-pinning commit — produced **14 pin swaps,
none of them defects**. A tool that warns on all fourteen teaches its reader to ignore
the warning.

The obvious refinement, "an unpaired swap is a defect, a paired one is an exchange",
was tested and **rejected**: the reversed capacitor pairs up exactly the same way. There
is no topological difference to find.

What separates them is the component. `PinSwap.significance`:

| | meaning |
|---|---|
| `polarity` | terminals are not interchangeable (`C_Polarized`, `CP`, `D*`, `LED`, `Battery`, or pins named `A`/`K`/`+`/`-`). The swap reverses the part, and ERC has no rule against it. |
| `none` | a symmetric two-terminal passive (`R`, `C`, `L`). Swapping its pins changes nothing; reporting it is noise. |
| `repin` | anything else — a connector, an IC. A real change, usually deliberate. Reported without a warning. |

`NetlistDiff.suspicious` returns only the `polarity` swaps, and only those fail a
`guard` run or raise a warning in a pull-request comment.

Two bugs of my own were found while validating this, both now regression-tested:
`str.rstrip("_0123456789")` reduced an Arduino's pin `A6_25` to `"a"` and called it an
anode; and a bare `"d"` symbol prefix matched `Device`, `Driver` and `DIP`. Both
heuristics are now anchored.

**The lesson worth keeping:** this class of judgement cannot be designed from first
principles, only measured against designs you did not write.

## D16 — The Action bootstraps pip, because KiCad CI images do not ship it

`ghcr.io/inti-cmnb/kicad10_auto` — the natural place to run this, and the family KiBot
users already run — ships Python 3.13 with **no pip, no pip3, no pipx and no uv**, and
no `curl` or `wget` to fetch one. `ensurepip` is absent too, so `python3 -m venv` cannot
bootstrap. Probed directly against the image rather than inferred.

The image is Debian, so the action installs `python3-pip` from apt (~20 s) when pip is
missing. The distro Python is additionally marked externally managed (PEP 668), so a
plain install refuses; adding one dependency-free package to a throwaway CI container is
safe, and the install falls back to `--break-system-packages` rather than failing.

The action also marks the workspace a safe directory: reading the base revision uses a
git worktree, and a checkout made by a different uid inside a container trips git's
ownership check.

This is the kind of thing that is only discoverable by running it. Verified end to end
inside the real image, which is also the first confirmation that netspec works against a
**native** `kicad-cli` rather than a Flatpak-wrapped one.

## D17 — Releases publish from a tag on main, and what that does not yet prove

`release.yml` runs **no tests**. It verifies the artifact — builds, metadata is valid,
the wheel installs cold into an empty venv, `--version` agrees with the tag, and nothing
but `kicad-netspec` lands in that venv (the zero-dependency claim, met against the built
wheel rather than the source tree). Correctness is the `Check` gate's job on main.

Two gates make that argument hold rather than merely stating it:

* `scripts/assert_tag_on_main.sh` — refuses a tag that is not an ancestor of
  `origin/main`, and refuses when it cannot resolve `origin/main` at all rather than
  assuming. A script, not inline YAML, so the test suite can exercise it against
  throwaway repositories; an inline gate can only be grepped, and dropping one `!` would
  leave it green and inert.
* The tag must equal `project.version`, or the release would publish a version whose
  name lies about its content.

**The gap, now closed.** Being on main proves the commit is on main; it proves the gate
*passed* only if main requires `Check`. It now does:

| setting | value |
|---|---|
| required status checks | `ok` (the bare context, never individual job names) |
| require branches up to date | yes |
| require a pull request | yes, 0 approvals |
| do not allow bypassing | **yes** |
| linear history | yes |
| force pushes / deletions | no |
| conversation resolution | required |

Taken from the house security baseline. The `ok` gate is a single aggregating job every
other job feeds into, so adding or renaming CI jobs never means touching branch
protection.

One divergence from `partspec`, deliberately: it runs with admin bypass *enabled*, and
netspec does not. Verified rather than assumed — with bypass on, a direct push to main
returned `Bypassed rule violations` and **landed anyway**; with it off the same push is
`remote rejected`. Since this workflow's entire safety argument is "every commit on main
passed `Check`", a bypass an admin can take at 3am is the argument's weakest link, and
this is the repo where that argument gates a PyPI upload.

The cost is real: every change here now needs a pull request, including a one-line
docs fix, and there is no emergency escape hatch short of turning the setting off.

Publishing is PyPI Trusted Publishing (OIDC); no token exists in this repo. The
registration names `CameronBrooks11/netspec`, workflow `release.yml`, environment
`pypi`, project `kicad-netspec`. Renaming any of them breaks releases *silently* — the
workflow runs and the upload is rejected — so a test asserts the filename and the
environment.

Version note: tags `v0.2.0`–`v0.4.1` were cut while `pyproject` still said `0.0.1`, so
they claimed versions the package never declared. `0.5.0` is the first version where the
two agree, and the gate above now prevents a recurrence.

## D18 — The MCP server is stateless verbs, and does not expose `guard`

Six tools — ``doctor``, ``netlist``, ``snapshot``, ``diff``, ``check``, ``gate`` — each
a fresh read of a design on disk. Nothing is held between calls.

**A process per call, not an in-process call.** Two reasons, both load-bearing:

* ``check`` imports a contract module and ``sys.modules`` caches it, so a long-lived
  server re-checking an edited contract could adjudicate the *previous* version while
  reporting on the new one. A stale answer presented as fresh is the exact failure this
  project exists to catch; it would be indefensible to ship it in the tool that catches
  it.
* The exit code is part of the contract — ``0`` clean, ``1`` a finding about the design,
  ``4`` an environment fault. A subprocess returns the CLI's own, unlaundered, and each
  reply carries a plain-language ``meaning`` so an agent cannot mistake "I could not
  look" for "your board is broken".

**``guard`` is deliberately absent.** It runs an arbitrary command. An agent driving this
server can already run commands, so exposing it would add risk and no capability; the
agent should make its edit and then call ``diff``. A test asserts it stays absent, along
with the absence of any tool whose name implies writing to a design.

**The tool list has a budget: under 3,000 tokens, asserted in CI.** The survey behind this
project measured KiCad MCP servers from 2,574 to 48,627 tokens of schema — the largest
spending a quarter of a 200K window before the agent reads a file. netspec's six tools
measure ~834 tokens on the wire. Tool surface is a cost paid by every agent that
connects, and a number in CI is the only thing that keeps it from creeping.

## D19 — A contract's net names are resolved before any rule is evaluated

A contract is written by a person; a netlist is named by KiCad. On a hierarchical board
those disagree. KiCad calls a net ``/Channel1/OUT``, and a contract that says ``OUT`` is
abbreviated rather than wrong. Before this, every such rule failed with *"there is no
net called 'OUT' in this design"* — a message that is both untrue and unactionable, and
which made netspec unusable on any multi-sheet design.

Resolution is therefore its own phase, running over the whole contract before the first
rule is adjudicated, with three outcomes:

===========  ===========================================================================
exact        the design has a net by exactly that name; it wins outright
leaf         exactly one net's last path segment matches
ambiguous    more than one matches, and there is no correct guess
===========  ===========================================================================

**The third outcome is why this is a phase and not a lookup helper.** Any design built
from a repeated block has the same net name under several sheets — this was checked
against a real dual-channel board, where a naive leaf name matched both channels for
every net inside the repeated sheet. Choosing one would invent intent the contract never
expressed, so resolution refuses and names the candidates. That is reported as a defect
in the *contract* — the rule fails before it runs — rather than as a finding about the
board. ``tests/fixtures/hierarchy.kicad_sch`` holds the same shape in miniature.

**Ambiguity pre-empts a rule; absence does not.** They are different answers and
collapsing them would undo the fix that taught ``forbid`` to see a real short: a net
that has vanished is precisely how a short appears in a netlist, because KiCad merges
the two nets and keeps one name. ``forbid`` needs to see that absence rather than be
stopped by it. A rule that cannot pick out one
net cannot run at all; a rule whose net is missing usually just failed.

**Only net names are resolved.** Components and pins are named exactly, are unique across
a design, and have no abbreviated form. There is nothing to resolve, so nothing is
resolved, and `Resolution` carries no ``kind`` field it would never use.

**What this does not change.** ``skipped`` still means "a part this rule names is not in
the design", and is still not green. Whether that should instead be a failure — and
whether ``skipped`` survives at all once variant scoping exists — is a separate question
from name resolution and is deliberately left open here.

## D20 — A netlist partitions pins into nets, and lookups say so

A design review recommended making the pin index many-valued, so that a pin appearing on
two nets would not silently resolve to whichever net was inserted last. **That
recommendation was not taken**, because the input it defends against cannot occur, and
building for it would repeat the mistake it was meant to fix.

A netlist *is* a partition: every pin belongs to exactly one net. That was checked rather
than assumed — across every fixture in this repo, across a real 135-node board, and
against a **deliberate dead short** built by relabelling one net to another and asking
kicad-cli for the result. In every case no pin appeared on two nets, because KiCad
resolves a short by *merging* the nets and keeping one name.

That is the same premise ``forbid`` originally got wrong: it hunted for a pin on two
nets, a shape KiCad never emits, and so never fired on a real short. A many-valued index
would have been a second structure built to serve the same impossible input.

**So the invariant is enforced instead of accommodated.** ``build_netlist`` raises when a
pin appears twice, and ``parse`` re-raises that as ``ParseError`` so the oracle reports it
as an environment fault — "I could not read this" — rather than a traceback or, worse, a
confident answer derived from a coin flip (D10). The dead branch in ``_check_forbid`` that
searched for the impossible shape is gone, along with the test that constructed it.

**The defect next door was real, and is what actually got fixed.** ``net_of`` took a pin
identifier and silently returned ``None`` for a pin *function* name, because it read a
number-keyed dict directly. That is precisely what reported correctly wired diodes as
broken, and its signature invited every future rule to make the same mistake. ``net_of``
now resolves on the same terms as ``resolve``.

Doing that naively would have made every lookup a scan, because ``nodes_of`` walked all
nets. Nodes are now indexed by reference at construction. Measured over 200 lookups:

======  =====================  ====================
nodes   before                 after
======  =====================  ====================
200     3.2 ms                 0.12 ms
2,000   34.0 ms                0.11 ms
8,000   144.0 ms               0.12 ms
======  =====================  ====================

Today's boards are small enough that the old cost was tolerable. It was linear per
lookup, and the vocabulary being built on top of it — symmetry between repeated blocks,
reachability, coverage over every pin — asks the question thousands of times.

## D21 — Rule types register a checker; there is no dispatch chain

Adding one primitive used to mean editing seven places: the dataclass, the constructor,
the ``Rule`` union, an ``isinstance`` tuple inside ``Spec``, an import, an ``isinstance``
branch in ``check``, and — after D19 — a fourth pattern-match in ``resolve``. Forgetting
the ``isinstance`` tuple failed at import with ``not a rule``, naming nothing useful.
Six primitives on that plan is thirty-odd edits of boilerplate, and the vocabulary this
project is heading for has more than six.

**``Rule`` is now a base class rather than a union**, so validation names no types and
nothing has to be widened. **Checkers claim their rule type by decorator**, so dispatch is
a dict lookup rather than a chain. Adding a primitive is a frozen dataclass and a
``@checks`` function — verified by building one (a fanout limit) entirely *outside* the
package, changing no existing line, and having both adjudication and resolution pick it
up.

**A rule reports the nets it names, and nothing else about itself.** ``net_names()`` lives
on the rule because "which of my fields are net names" is a fact about the rule's shape,
not about adjudication. That keeps ``contract.py`` free of any import from ``model`` or
``check``, which matters more here than it usually would: a contract module is **user code
that ``netspec check`` executes**, and it has no business reaching the machinery that
judges it.

**An unregistered rule type fails; it is not ``unsupported``.** D9 defines ``unsupported``
as "this backend cannot evaluate this rule" — a statement about a *backend's* capability.
netspec knowing no way to check something is a defect in netspec, and reporting it as a
capability gap would launder a bug into a limitation. ``unsupported`` stays unreachable
until backend capabilities reach the check layer, which is honest about where it stands.

**Two boundary tests guard the halves**, because the failure mode is silence: a rule with
no checker, and a rule that does not declare ``net_names()`` — the second would not fail
loudly, it would skip hierarchical resolution for that rule and quietly compare raw
strings, which is the D19 bug returning by the back door. Both guards were confirmed to
actually fire against a deliberately broken rule rather than merely passing.

Also removed here: ``Spec.nets``, ``Spec.polarities``, ``Spec.forbidden`` and
``rules_of`` — four accessors with zero callers, each one an instance of the per-rule-type
pattern that does not survive a real vocabulary, and each one a template an implementer
would have copied.

## D22 — The parser carries a net's classes and a part's sheet, and persists neither

KiCad states both on every netlist it writes and netspec discarded both. They are inputs
the contract vocabulary needs: a net's classes are what a power-domain or
differential-pair rule is written against, and a component's sheet is what lets a rule
tell two instances of a repeated block apart. Neither had to be inferred.

Both turned out to be less simple than they look, and the shape of this decision is
mostly the ways they are not.

**A net has classes, plural, and one of them is always ``Default``.** KiCad joins them
with commas:

    <net code="2" name="GND"  class="Power,Default">
    <net code="3" name="VIN"  class="Power, Fast,Default">

Modelled first as a single ``netclass: str``, which is wrong in the way that matters:
the field exists so a rule can ask "is this net in the Power domain", and against
``"Power,Default"`` an equality test says no. It is now ``netclasses: tuple[str, ...]``,
a shape that makes membership the obvious operation and equality the awkward one.

**The joining is not escaped, and that loss is KiCad's, not recoverable here.** The
second line above comes from a project that really does declare a class named ``Power,
Fast``; it is indistinguishable from three classes. netspec splits without stripping, so
the stray leading space in ``" Fast"`` survives as the only evidence something was lost.
``tests/fixtures/netclasses`` is a real KiCad project carrying exactly this case.

**A component's sheet is one value even when its units are on several**, and the field is
called ``attributed_sheet`` for that reason — ``sheet`` read as authoritative at every
call site, and a docstring cannot un-say a name. KiCad writes a
single ``<sheetpath>`` per component and chooses it by sheet traversal order, so a dual
op-amp straddling two channels is attributed wholly to one of them — and reordering the
pages, an edit with no electrical meaning, can change which. It is honest as "a sheet
this part appears on" and false as "the sheet this part is on".

**So neither is persisted, and the snapshot schema did not change.** A snapshot holds
what the diff compares, and the diff compares membership. Persisting a fact the diff
ignores would give a repo that commits snapshots a red ``git diff`` beside a green
``netspec diff`` with nothing naming the cause — precisely the spurious diff
``snapshot.py`` opens by promising cannot happen. Sheet fails a second test besides: it
is not stable enough to commit at all. Keeping both out of the file also means no schema
bump, so no older netspec is locked out of a snapshot it could have read.

The fields are still available where they are needed. ``check`` reads a design through
the oracle on every run, so a rule sees them fresh; only the persisted artifact omits
them.

**Two neighbours are left behind entirely.** ``<net code>`` is KiCad's net *number* and
ordinary edits renumber it; ``<sheetpath tstamps>`` is the same path in UUIDs. D11 keeps
unstable identifiers out of this model, and a contract is a durable statement about a
design. The tests asserting this check *parsed values* — that no field holds a UUID or a
net code — rather than attribute names: an earlier version checked ``hasattr(Net,
"code")`` and passed happily while a scratch build carried both under other spellings.

## D23 — The report is data; its ids are keys; and it is only as good as the contract

D2 states the product as "a persisted, schema'd report and an exit code that gates CI".
Only the exit code existed: a result's rule was a rendered English sentence, no command
emitted JSON, and the MCP ``check`` tool handed an agent the printed text. Three
primitives of prose can be read; nine cannot. ``netspec check --format json`` now emits a
self-describing document and the MCP tool returns it parsed, with the sentence surviving
as ``text`` beside the fields a machine wants.

### The trust boundary, stated plainly

**A contract is code, and the report is exactly as trustworthy as the contract.**

D8 chose that: ``netspec check`` imports and executes the module it is given. The
consequence was not written down until three review passes made it concrete. A contract
executes *before* the design is read, so it can do anything this process can — including
replacing the oracle:

    Cli10Backend.netlist = _returns_a_board_I_made_up

after which netspec emits a **genuine** report — its own ``check_report``, its own ids, a
real ``kicad_version`` — about a file whose entire contents are ``this is not a schematic
at all``. No validation of the report's shape can detect that, because netspec really did
produce it. It defeats the text format identically, so this is not a JSON problem.

An earlier draft of this entry claimed "a contract cannot write its own report". That was
false, and the guards behind it were defeated five ways: ``os.write(1, …)``, rebinding
``sys.__stdout__``, ``os.dup2``, a subprocess inheriting fd 1, and the oracle patch above.
Claiming a property netspec cannot deliver is worse than having no property at all, so
the claim is withdrawn rather than patched.

**What the guards actually do** is stop a contract corrupting the report *by accident*: a
stray ``print`` goes to stderr, and the MCP layer requires the document to identify itself
rather than reading whatever JSON appeared on stdout. Both are worth keeping and neither
is a security boundary. The real answer is process isolation — execute the contract in a
child that returns serialised rules rather than a live ``Spec``, and read the netlist
before executing anything. **That is not built.** Until it is, treat a contract as you
would any executable you are about to run.

### The id is a key, enforced rather than hoped for

``kind:subject``. D8's "semantic report diff that reports removed assertions" needs two
reports to align:

===============================  ==========================================
same rule, run again             same id
same rule, quietly weakened      **same id**, different body
rule about a different subject   different id
rule deleted                     id absent
===============================  ==========================================

That is only true if nothing shares a key, so ``Spec`` enforces it. Two rules with one
kind and subject would otherwise let an agent smuggle a weak assertion in beside a strong
one — the obvious id-keyed comparator, which every reviewer independently wrote, keeps the
last and the strong rule vanishes from the comparison while still being enforced. ``kind``
comes from a ``ClassVar`` that ``describe()`` cannot override, since the two diverging
gave one key to ``Spec`` and another to the report. ``spec:`` is reserved for netspec's own
Spec-level findings. Validation lives on the ``Net`` dataclass, not in the ``net()`` helper,
because the dataclass is exported and the helper was walk-aroundable.

**Two ``at least`` rules about one net compose rather than conflict.** "At least A" and "at
least B" is "at least A, B", and refusing them broke a real shape: a contract assembled
from per-subsystem rule lists, where two independently authored blocks each name their own
pins on a shared rail and neither can know the other's. Anything else about one subject is
a contradiction and is refused.

``forbid``'s subject is **JSON-encoded, not joined on a separator**. KiCad accepts ``|``,
``"``, ``\`` and unicode escapes inside a net name — verified by round-tripping each
through real kicad-cli — so ``forbid("A|B", "C")`` and ``forbid("A", "B|C")`` keyed
identically. Its body is sorted with its subject; canonicalising the key alone made two
identical contracts align and then report a change.

**A known limit.** Dropping a net from a ``forbid`` changes its subject, so it reads as a
deletion plus an addition rather than a weakening — though the assertion now permits a
short it previously banned. A set-valued assertion has no stable key smaller than the set;
the report-diff tool will have to notice overlapping subjects.

### D8 is still not closed

The report makes the diff possible and the tests show a removal is detectable, but the
tool that performs the comparison does not exist. Every review pass wrote its own
comparator to test this, which is the evidence that it is missing.

### Smaller things the reviews earned

``verdict_reason``, because ``verdict: fail`` beside ``fail: 0`` was solvable only by
already knowing D9. ``contract`` and ``name``, because two entirely different contracts
otherwise produced byte-identical reports. Synthetic results are ``kind: "spec"`` with real
subjects, not a ``none`` that read as *absence* where it meant *not-a-rule*. Exit 4 carries
no report, because an environment fault is not a verdict. And ``snapshot.loads`` requires a
``nets`` key rather than blacklisting one intruder: both a report and a snapshot are JSON
with a ``schema``, and ``netspec diff`` on two reports used to answer "no change in
connectivity" with exit 0 — green, confident, and about a different question.

## D24 — The contract runs in a child, which raises the bar and does not close the door

D8 makes a contract *code*, and that is not reversed. What review established is the
consequence D8 never wrote down: a contract runs **before** the design is read, so
in-process it could replace the oracle and netspec would emit a genuine passing report
about a file containing ``this is not a schematic at all``.

The contract now executes in a child process and hands back only declarations; the parent
reads the netlist itself. **State plainly what that buys, because an earlier draft of this
entry did not and was wrong.**

### What it closes

Every route by which a contract could speak as netspec **through its own descriptors**.
The result travels through a file rather than stdout, so ``print``, ``os.write(1, …)``,
rebinding ``sys.__stdout__``, ``os.dup2`` and a subprocess inheriting the descriptor are
closed by not using that channel — not by guarding it, which is what the previous attempt
did and why it failed.

An earlier draft of this said "every route", full stop. That is wrong: a contract can open
``/proc/<ppid>/fd/1`` and write onto the parent's *actual* stdout. What that buys is
bounded and was measured — it can **prepend** text, so a naive line-reading consumer of
the text format can be shown a fake ``PASS``; it cannot suppress netspec's own report,
cannot change the exit code, and against the structured consumer it only corrupts the JSON
into ``report_unavailable``. A denial of report, not a false verdict. Closing it properly
means a sandboxed child, which is the same answer as the swap below.
The child's own output goes to a file too, because a detached grandchild inheriting the
parent's *pipes* held ``communicate()`` open and could deterministically rewrite the
result after the child had finished. The child ends with ``os._exit`` so that an
``atexit`` handler or a non-daemon thread gets no turn after the write. It also cannot
mutate the parent's module state, and a syntax error in a contract now reports as an
environment fault instead of crashing with a traceback and exit 1 — a D10 violation that
predated this.

### What it does not close, verified

**A contract can swap the schematic on disk.** It runs as the same user on the same
filesystem before the parent reads the design, so it can put a different board at the path
it named and have a detached grandchild put the original back afterwards. Reproduced: a
board that honestly fails five rules reports ``PASS`` with no trace. Isolation moved the
contract into another *process*, not another *filesystem view*, and the design is read
from the filesystem they share.

So the report records a **``design_digest``** — a SHA-256 over the canonical snapshot of
what netspec actually read. Taken over the snapshot rather than the file because it is
stable run-to-run by construction and covers every sheet of a hierarchical design. That
makes a swap **detectable**: recompute it from the committed files and compare. It is
detection, not prevention, and the difference matters.

**Prevention needs a sandbox** — a child with no write access outside a scratch directory.
That is OS-specific, a hard dependency on the host, and is **not built**.

**A contract can still declare whatever it likes**, including substituting its own result
for the one its readable text would produce. That residue is deliberate: declaring
assertions is what a contract is *for*, no isolation could distinguish a weak declaration
from an honest one, and the rules adjudicated are recorded in the report, so the divergence
is auditable. A weakened assertion is what D8's report diff exists to surface.

**A contract can deny service** — kill the parent, or exhaust it. Neither produces a false
pass.

### The division of labour

Isolation reduces what a contract can lie about. The digest makes the remaining lie
detectable. The report diff catches lying about the assertions. **None of the three closes
D8 alone, and the first two were claimed to do more than they do until an adversarial pass
proved otherwise.**

### Consumers

``check``, ``guard`` **and ``ci``** use the isolated path. ``ci`` is the one that posts a
verdict as a pull-request comment and gates merges, and it was left on the in-process path
when the other two were converted — found by review, not by me. ``contract.load`` stays
in-process for the pytest plugin, where you are writing the test in your own process.

A rule kind the parent does not know is carried as an ``Unknown`` finding rather than
raised, so a contract defining its own ``Rule`` subclass still reports exit 1 — a statement
about the contract — instead of exit 4, which would claim netspec could not look.

### D4.1 exception

netspec spawns processes only in the oracle, plus ``guard``'s command runner and the MCP
server. This is a fourth, argued here as D4.1 asks. The boundary test enforcing it caught
the change correctly — and turned out to have been rewritten, by me, into something
strictly *weaker* than the string check it replaced: an AST-only scan missed
``__import__("subprocess")``, which grep caught for free. It is now the union of both,
with docstrings stripped so prose explaining why a module is safe does not trip it.

## D25 — `through` and `mirrors`, and the two ways the first version was unsound

Both came out of running this project's analysis over a real dual-channel motor
controller, and neither exists in any of the 94 tools surveyed behind this project. Both
were also wrong on their first pass in ways that made a green report actively misleading,
so the record below is the corrected version.

**``through(a, ref, b)`` — a series part is the path, and by default the only path.** A
fuse, a ferrite, a sense resistor, a net tie separating logic ground from power ground.
Bypass one or parallel it and the netlist reads as ordinary: both nets exist, everything
is connected, ERC is silent.

Two limits are stated in the docstring rather than left to be inferred:

* **It asserts pin membership, not conduction.** A netlist reports which pins sit on which
  nets and nothing about what a part does between them, so a four-pin package with a pin
  on each net satisfies this whether it is a resistor or an optocoupler. Reading it as
  "current flows here" is the reader's inference, not netspec's claim, and a review
  demonstrated it certifying an isolation barrier.
* **``only`` sees single-component bridges.** ``GND -R8- MID -R9- GNDPWR`` is a ground
  loop it will not report. Searching further was tried and **rejected on evidence**: a
  two-component search over the real board finds ``GND -U1- +12V -J1- GNDPWR`` and calls a
  correct design looped, because every rail reaches every other through the power tree. A
  check that fires on good boards is worse than one with a stated edge.

**``mirrors(a, b)`` — two parts are wired to the same shape.** Structural, not textual:
the pin-wise mapping between their nets must be a bijection, **and a net carried by both
parts must map to itself**. No channel index, no string surgery on net names — the design
note behind this proposed both and neither is needed.

That second clause is load-bearing and was missing. Without it the rule is graph
isomorphism, which is invariant under relabelling, so **every permutation** of one part's
pin-to-net map passed — a VCC/GND swap included, which is the exact defect class this
project exists to catch. The docstring asserted the invariant while the code did not
enforce it, and no test noticed, because nothing tested the claim.

A pin connected to nothing also mirrored a wired one: KiCad names each floating pin
uniquely (``unconnected-(U3-IN-Pad1)``), so to a bare bijection it was just another
distinct net, and a part with every pin unwired mirrored a fully wired one. A one-node net
the *designer* named is a different thing — a deliberate label on a sheet pin — so
anonymity is part of the test.

**The shared-net clause is the only anchor, and where two instances share no net the rule
is pure isomorphism.** Every permutation passes then — 6/6 at three pins, 24/24 at four,
40320/40320 at eight — against 1/6, 2/24 and 720/40320 when two nets are shared. Per-channel
supplies are not exotic (an isolated gate driver, a bootstrap half-bridge leg, a per-phase
floating rail), so a VCC/GND swap between two such instances mirrors happily.

Nothing in a netlist can close that: the correspondence between ``/VDDA`` and ``/VDDB``
lives in a naming convention, and reading one would be the string surgery this rule was
designed to avoid. So it is a stated bound, not a defect to fix — use ``mirrors`` where
instances share a rail, which is the common case and the one the real board has.

An earlier version of this entry gave a table of "random rewiring" pass rates instead.
That measured the wrong axis: random rewiring mostly produces net *collisions*, which the
plain bijection already rejects, so the numbers flattered the fix and said nothing about
permutations — the actual defect class. Replaced with the permutation figures above.

Two smaller bounds: ``mirrors`` pairs pins **by number**, not by function, making it the
one rule here that leans on the identifier D12 calls untrustworthy; and two instances that
touch each other cannot mirror, since the net linking them is shared and must map to
itself, which excludes cascaded stages and resistor ladders by design.

**What ``mirrors`` cannot see**: it compares two parts, so a block is several rules, and a
change leaving both instances equally wrong is invisible. Comparing two sheet instances
wholesale needs a component correspondence between them, which this does not attempt.

Both are symmetric in their arguments and key accordingly (D23). Both refuse a wrong-arity
tuple on the dataclass, not only in the helper, because ``isolate.py`` rebuilds them from
JSON — the same reason ``Forbid`` carries that guard. An absent part is ``skipped``,
matching ``polarity`` and D9's taxonomy, which the first version diverged from silently.

## D14 — Name: `netspec`

CLI and import-facing name is `netspec`, in the family idiom: `partspec` for mechanical
CAD parts, `netspec` for PCB nets, `gerberdiff` for fabrication output.

The bare `netspec` name on PyPI is held by an abandoned package — last release
2023-09-04, GitHub repo deleted, summary still unedited cookiecutter boilerplate, ~9
downloads a month. Rather than block on a PEP 541 transfer, the distribution is
**`kicad-netspec`** and the import package is `kicad_netspec`, while the console script
stays `netspec`. A PEP 541 request for the bare name can run in the background; if it
succeeds, publish an alias distribution.
