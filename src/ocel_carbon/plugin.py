"""The Ocelescope plugin: carbon accounting over an object-centric event log."""

from __future__ import annotations

from typing import Annotated

from ocelescope import OCEL, OCELAnnotation, Plugin, plugin_method

from .aggregate import (
    annotate_log,
    build_comparison,
    build_footprint,
    build_template,
)
from .allocation import allocate_emissions
from .emissions import compute_event_emissions
from .inputs import AllocationConfig, ComparisonInput, FootprintInput
from .resources import (
    AllocationComparison,
    CarbonFootprint,
    EmissionFactorTemplate,
    ObjectFootprintTable,
)

EventLog = Annotated[
    OCEL,
    OCELAnnotation(
        label="Event log",
        description=(
            "An OCEL 2.0 log. Quantity operations, where the log carries them, "
            "can drive both the emissions and their allocation."
        ),
    ),
]


class CarbonFootprintPlugin(Plugin):
    """Carbon accounting for object-centric event logs.

    Emissions are computed in two steps that are deliberately separate. First
    every event is charged what its activity's emission factors come to -- a flat
    amount, a numeric event attribute such as a distance, or the quantity of an
    item type the event moves. Then each event's emissions are *divided* among
    the objects it involves.

    The second step is where an object-centric log stops behaving like a set of
    cases. A transport event belongs to a truck, a route and every order on
    board at once, so its emissions have no single owner; they have a set of
    candidates and a rule for splitting one number among them. Dividing keeps
    the per-object-type figures summing to the log's total, which charging each
    type the whole of every shared event does not: that reading -- the one a log
    flattened to a single object type gives -- is reported alongside, so the
    double counting it introduces is visible rather than inherited.
    """

    label = "Carbon Footprint"
    description = (
        "Compute emissions per event from emission factors, allocate them across "
        "the objects each event involves, and report the footprint per activity, "
        "object type, variant and object."
    )
    version = "0.1.0"

    @plugin_method(
        label="Inspect log",
        description=(
            "List every activity with the numeric attributes, item types and "
            "object types it offers, as a starting point for emission factors."
        ),
    )
    def inspect_log(self, ocel: EventLog) -> EmissionFactorTemplate:
        return build_template(ocel)

    @plugin_method(
        label="Compute footprint",
        description=(
            "Charge each event its activity's emission factors, allocate the "
            "result across the objects involved, and return the annotated log "
            "with the footprint and its hotspots."
        ),
    )
    def compute_footprint(
        self, ocel: EventLog, input: FootprintInput
    ) -> tuple[OCEL, CarbonFootprint, ObjectFootprintTable]:
        emissions = compute_event_emissions(
            ocel, input.factors, default_factor=input.default_factor
        )
        allocation = allocate_emissions(ocel, emissions.per_event, input.allocation)

        footprint, hotspots = build_footprint(
            ocel,
            emissions,
            allocation,
            unit=input.unit,
            allocation_label=input.allocation.describe(),
            variant_object_type=input.variant_object_type,
            max_variants=input.max_variants,
            top_objects=input.top_objects,
        )
        annotated = annotate_log(ocel, emissions, allocation)
        return annotated, footprint, hotspots

    @plugin_method(
        label="Compare allocation rules",
        description=(
            "Allocate one and the same set of event emissions several ways, to "
            "show how much of an object type's footprint is a modelling choice."
        ),
    )
    def compare_allocation(
        self, ocel: EventLog, input: ComparisonInput
    ) -> AllocationComparison:
        configurations: list[AllocationConfig] = list(input.allocations)
        if len(configurations) < 2:
            raise ValueError(
                "comparing allocation rules needs at least two of them; "
                "add a second rule, or run 'Compute footprint' for a single one"
            )

        emissions = compute_event_emissions(
            ocel, input.factors, default_factor=input.default_factor
        )
        results = [
            (config, allocate_emissions(ocel, emissions.per_event, config))
            for config in configurations
        ]
        return build_comparison(ocel, emissions, results, unit=input.unit)
