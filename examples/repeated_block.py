"""A worked contract for the two primitives a repeated block needs.

The design is ``tests/fixtures/hierarchy.kicad_sch``: one sub-sheet instantiated twice as
``Channel1`` and ``Channel2``, plus an unrelated ``Aux`` sheet. Each channel is a single
series resistor between a shared input rail and its own output.

Run it:

    netspec check examples/repeated_block.py
"""

from kicad_netspec import Spec, mirrors, net, through

board = Spec(
    name="Repeated block",
    source="../tests/fixtures/hierarchy.kicad_sch",
    rules=[
        # VIN feeds both channels and nothing else.
        net("VIN", ["R1.1", "R2.1"]),
        # Each channel's resistor is the path from the shared rail to that channel's
        # output -- and the only path. Bypass one, or bridge the rail to an output some
        # other way, and the netlist still reads as perfectly ordinary: both nets exist,
        # everything is connected, ERC is silent.
        through("VIN", "R1", "/Channel1/OUT"),
        through("VIN", "R2", "/Channel2/OUT"),
        # And the two channels are wired to the same shape. One line covers the whole
        # correspondence, with no channel index and no assumption about how the board
        # names its nets: VIN is carried by both parts so it must line up, and each
        # channel's own output pairs with the other's.
        #
        # Note what this cannot see. The anchor is the net the two parts share, so two
        # instances with entirely separate supplies -- an isolated gate driver, a
        # bootstrap leg -- have nothing to line up against and a swapped pair would
        # still mirror. See D25; use it where instances share a rail.
        mirrors("R1", "R2"),
    ],
)
