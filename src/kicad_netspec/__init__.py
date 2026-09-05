"""Verify PCB connectivity against declared engineering intent, using KiCad as the oracle."""

__all__ = ["__version__"]

__version__ = "0.6.0"

from kicad_netspec.contract import Spec, forbid, mirrors, net, polarity, through

__all__ += ["Spec", "forbid", "mirrors", "net", "polarity", "through"]
