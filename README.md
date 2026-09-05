# netspec

**Verify PCB connectivity against declared engineering intent, using KiCad as the oracle.**

> **Status: pre-alpha.** All six commands work and are dogfooded in this repo's own CI.
> The stable surface is the report and the exit codes; the Python API will move. The
> design lives in [`docs/DECISIONS.md`](docs/DECISIONS.md).

```sh
pip install kicad-netspec
```

Needs KiCad 8 or newer for `kicad-cli` — found on `PATH`, via Flatpak, in a macOS bundle
or a Windows install. `netspec doctor` says which. The core itself has **no runtime
dependencies**.

Every tool that edits a KiCad design reports on its own arithmetic. Only KiCad knows what
a design actually *is*. `netspec` asks it — after every change — and fails loudly when
intent and reality diverge.

```sh
netspec doctor                                     # find and probe a KiCad engine
netspec netlist board.kicad_sch                    # what KiCad says is connected
netspec snap board.kicad_sch -o before.json        # record connectivity, stably
netspec diff before.json board.kicad_sch           # what actually changed
netspec check contract.py                          # adjudicate declared intent
netspec gate board.kicad_pcb                       # ERC/DRC at every severity
netspec guard board.kicad_sch -- <any tool>        # snapshot, run, re-read, adjudicate
```

```
$ netspec netlist tests/fixtures/good_ldo.kicad_sch
  3 components, 3 nets
    +3V3  C2.1, U1.2
    GND   C1.2, C2.2, U1.1
    VIN   C1.1, U1.3
```

Finds `kicad-cli` on `PATH`, via Flatpak, in a macOS bundle or a Windows install --
`netspec doctor` says which. A missing engine is an environment fault (exit `4`), never a
finding about your design.

`netspec` **never writes to a design file.** It is the gate an editing tool's output has
to pass, not the tool.

## Why

A survey of ~90 KiCad AI/agent projects found three failure modes that ship today, and one
property of KiCad's own defaults, all of which a netlist check catches and nothing else
does:

- wires "added" that connect nothing, reported as success
- a pin-position helper off by a sign, silently wiring pin 2 where pin 1 was asked for —
  which reverses a polarised capacitor while passing ERC
- `pcb drc --schematic-parity` at default severity reporting **0 problems on a board with
  147**, because `footprint_symbol_mismatch` defaults to `warning`

Each is frozen as a regression test in [`tests/fixtures/`](tests/fixtures), paired with
the netlist KiCad itself derived from it.

Two of those fixtures are the same circuit, wired correctly and wired backwards. KiCad's
ERC reports **the same two violations for both** -- reversing a polarised capacitor is
legal wiring, so no rule fires. `netspec` names it:

```
$ netspec diff polarized_cap_correct.kicad_sch reversed_polarized_cap.kicad_sch
NET CHANGES
  ~ Net-(C1-Pad2)  +C1.2 -C1.1

FLOATING PINS
  ! C1.1  is no longer connected to anything

PIN SWAPS  (a connection moved between pins of one part)
  C1 on Net-(C1-Pad2): pin 1 -> pin 2  <- reverses a 2-pin part

WARNING: 1 pin swap(s) on a two-pin part. If any is polarised, it is now backwards,
and ERC will not tell you.
```

## Declaring intent

A contract is Python, not a data file -- more expressive, nothing to version, and no
policy DSL to invent. Pins are named by *function* where the symbol offers one, because
pin numbers are exactly what the known schematic-writer bugs corrupt.

```python
from kicad_netspec import Spec, forbid, mirrors, net, polarity, through

board = Spec(
    source="hardware/board.kicad_sch",
    rules=[
        net("VIN", ["J1.1", "C1.1", "U1.VI"]),
        net("+3V3", ["U1.VO", "C2.1"]),
        polarity("C1", plus="VIN", minus="GND"),
        forbid("VIN", "GND"),
        through("GND", "NT1", "GNDPWR"),  # the only path between two grounds
        mirrors("U2", "U3"),  # two channels, wired the same
    ],
)
```

`netspec check` imports and executes that module -- a contract is code. `netspec diff`
executes nothing.

## Guarding an edit

`guard` does not care what did the editing:

```
$ netspec guard board.kicad_sch --contract contract.py -- claude -p "add a decoupling cap"

NET CHANGES
  ~ GND  +C1.1 -C1.2
  ~ VIN  +C1.2 -C1.1

PIN SWAPS  (a connection moved between pins of one part)
  C1 on VIN: pin 1 -> pin 2  <- reverses a 2-pin part

CONTRACT
  FAIL  C1 polarity: pin 1->VIN, pin 2->GND
        pin 1 is on GND, expected VIN  -- C1 IS REVERSED. ERC does not check this.
```

Exit codes are the contract: `0` clean, `1` a violation of the design, `4` an
environment fault -- a missing engine is never reported as a broken board.

## In CI

A GitHub Action that comments on a pull request with the connectivity delta -- something
no PCB team has and most would want, agent or no agent:

```yaml
- uses: CameronBrooks11/netspec@v0
  with:
    schematic: hardware/board.kicad_sch
    contract: hardware/contract.py   # optional
```

It needs KiCad, so run the job in a KiCad container -- see
[`examples/workflows/connectivity.yml`](examples/workflows/connectivity.yml). The comment
is updated in place rather than stacked per push, and a missing engine reports that it
checked nothing rather than failing the board.

## In your test suite

Installing the package registers a pytest plugin; no conftest needed:

```python
from kicad_netspec.pytest_plugin import assert_net, assert_polarity


def test_the_bulk_cap_is_the_right_way_round(netlist):
    assert_polarity(netlist("hardware/board.kicad_sch"), "C1", plus="VIN", minus="GND")
```

Without KiCad on the machine, those tests **skip** rather than fail.

## For an agent

An MCP server ships in the `mcp` extra — six stateless verbs, one process per call:

```sh
pip install 'kicad-netspec[mcp]'
```

```json
{ "mcpServers": { "netspec": { "command": "netspec-mcp" } } }
```

`doctor` · `netlist` · `snapshot` · `diff` · `check` · `gate`. The whole tool list costs
**~834 tokens** of schema, and CI fails if it exceeds 3,000 — surveyed KiCad MCP servers
range from 2,574 to 48,627, the largest spending a quarter of a 200K window before the
agent reads a file.

Every reply carries the exit code and what it means, so an agent cannot confuse *"I could
not look"* with *"your board is broken"*. `guard` is deliberately **not** exposed: it runs
an arbitrary command, and an agent that can already run commands gains nothing from
being handed a shell by a verification tool.

## Family

- **[partspec](https://github.com/CameronBrooks11/partspec)** — mechanical CAD parts vs declared intent
- **netspec** — PCB nets vs declared intent
- **[gerberdiff](https://github.com/CameronBrooks11/gerberdiff)** — fabrication output geometry

## Not this

`netspec` does not do design review (see [kicad-happy](https://github.com/aklofas/kicad-happy)),
does not edit schematics or boards (see [Konnect](https://github.com/mixelpixx/Konnect)),
and does not place or route. It holds the ruler.

## License

Apache-2.0
