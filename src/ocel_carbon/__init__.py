"""Carbon footprint analysis for object-centric event logs.

An Ocelescope plugin. ``CarbonFootprintPlugin`` is the single plugin class the
framework loads; the modules beside it are usable on their own against an
``ocelescope.OCEL``, which is what the tests do.
"""

from .plugin import CarbonFootprintPlugin

__all__ = ["CarbonFootprintPlugin"]
