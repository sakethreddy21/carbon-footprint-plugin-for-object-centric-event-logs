"""The results this plugin hands back.

Each one is an Ocelescope resource: a JSON-serialisable pydantic model with a
visualisation. That makes them exportable, re-importable and usable as the input
of another plugin -- the footprint is data, not only a picture.
"""

from __future__ import annotations

from typing import ClassVar

from ocelescope import Plotly, Resource, TableColumn, TableViz
from pydantic import BaseModel, Field

from .charts import comparison_figure, footprint_figure


class ActivityFootprint(BaseModel):
    """What one activity emitted across the log."""

    activity: str
    events: int
    co2e: float
    share_of_total: float
    mean_per_event: float
    rules: list[str] = Field(default_factory=list)


class ObjectTypeFootprint(BaseModel):
    """What one object type carries, next to what a flattened reading claims."""

    object_type: str
    objects_charged: int
    allocated_co2e: float
    share_of_allocated: float
    mean_per_object: float
    #: The number a log flattened to this object type reports: every object of
    #: the type is a case, so an event involving several of them is counted once
    #: per object instead of being divided.
    naive_co2e: float
    #: ``naive_co2e / allocated_co2e``: how much that reading overstates this
    #: type. ``None`` when nothing was allocated to the type.
    naive_inflation: float | None = None


class ObjectFootprint(BaseModel):
    """What one object carries."""

    object_id: str
    object_type: str
    allocated_co2e: float
    emitting_events: int


class VariantFootprint(BaseModel):
    """What one process variant of one object type emits, per object."""

    variant_id: str
    object_type: str
    activities: list[str]
    objects: int
    total_co2e: float
    mean_co2e: float


class FootprintDiagnostics(BaseModel):
    """Coverage and conservation, so the total can be trusted or questioned."""

    events_total: int
    events_with_emissions: int
    activities_covered: dict[str, list[str]] = Field(default_factory=dict)
    activities_without_factor: dict[str, int] = Field(default_factory=dict)
    missing_event_attributes: dict[str, int] = Field(default_factory=dict)
    non_numeric_attribute_values: dict[str, int] = Field(default_factory=dict)
    events_without_quantities: dict[str, int] = Field(default_factory=dict)
    events_without_eligible_object: int = 0
    events_with_zero_allocation_weight: int = 0
    zero_weight_handling: str = "equal"
    #: ``total - (allocated + unallocated)``. Allocation divides, so this is zero
    #: up to floating-point error; anything else is a bug in this plugin.
    mass_balance_error: float = 0.0


def _path_label(variant: VariantFootprint, limit: int = 7) -> str:
    """A variant's activity sequence, short enough for a tooltip."""
    activities = list(variant.activities)
    shown = activities[:limit]
    path = " → ".join(shown)
    if len(activities) > limit:
        path += f" → … (+{len(activities) - limit})"
    return f"{variant.objects}× {variant.object_type}<br>{path}"


class CarbonFootprint(Resource):
    """The carbon footprint of an object-centric event log.

    Emissions are computed per event from the configured factors, then divided
    among the objects each event involves. Dividing rather than copying is what
    makes the per-object-type figures add up to the log's total.
    """

    label: ClassVar[str] = "Carbon Footprint"
    description: ClassVar[str] = (
        "Emissions per activity, per object type, per variant and in total, "
        "with the allocation rule and coverage they rest on."
    )

    unit: str
    allocation: str
    total_co2e: float
    allocated_co2e: float
    unallocated_co2e: float
    by_activity: list[ActivityFootprint] = Field(default_factory=list)
    by_object_type: list[ObjectTypeFootprint] = Field(default_factory=list)
    by_variant: list[VariantFootprint] = Field(default_factory=list)
    diagnostics: FootprintDiagnostics

    def visualize(self) -> Plotly:
        activities = [
            (item.activity, item.co2e)
            for item in sorted(self.by_activity, key=lambda item: item.co2e)
            if item.co2e
        ][-10:]
        object_types = [
            (item.object_type, item.allocated_co2e, item.naive_co2e)
            for item in sorted(
                self.by_object_type,
                key=lambda item: item.allocated_co2e,
                reverse=True,
            )
        ][:8]

        if self.by_variant:
            detail_title = (
                f"Heaviest variants of {self.by_variant[0].object_type} "
                "(mean per object)"
            )
            # A variant id says nothing to a reader, and its activity sequence
            # does not fit on an axis: the tick carries the size, the hover the
            # path itself.
            detail = [
                (
                    f"{item.objects}× · {len(item.activities)} steps",
                    item.mean_co2e,
                    _path_label(item),
                )
                for item in sorted(self.by_variant, key=lambda item: item.mean_co2e)
            ][-8:]
        else:
            detail_title = "Emissions by object type (allocated share)"
            detail = [
                (item.object_type, item.allocated_co2e, item.object_type)
                for item in sorted(
                    self.by_object_type, key=lambda item: item.allocated_co2e
                )
                if item.allocated_co2e
            ][-8:]

        return Plotly(
            figure=footprint_figure(
                unit=self.unit,
                total_co2e=self.total_co2e,
                allocated_co2e=self.allocated_co2e,
                unallocated_co2e=self.unallocated_co2e,
                activities=activities,
                object_types=object_types,
                detail_title=detail_title,
                detail=detail,
            )
        )


class ObjectFootprintTable(Resource):
    """The objects carrying the most emissions, as a sortable table."""

    label: ClassVar[str] = "Carbon Hotspots"
    description: ClassVar[str] = (
        "The individual objects the emissions were allocated to, heaviest first."
    )

    unit: str
    allocation: str
    rows: list[ObjectFootprint] = Field(default_factory=list)

    def visualize(self) -> TableViz:
        return TableViz(
            columns=[
                TableColumn(id="object_id", label="Object"),
                TableColumn(id="object_type", label="Object type"),
                TableColumn(
                    id="allocated_co2e",
                    label=f"Allocated ({self.unit})",
                    data_type="number",
                ),
                TableColumn(
                    id="emitting_events", label="Emitting events", data_type="number"
                ),
            ],
            rows=[
                {
                    "object_id": row.object_id,
                    "object_type": row.object_type,
                    "allocated_co2e": round(row.allocated_co2e, 4),
                    "emitting_events": row.emitting_events,
                }
                for row in self.rows
            ],
        )


class ActivityProfile(BaseModel):
    """What one activity offers a factor rule to work with."""

    activity: str
    events: int
    object_types: list[str]
    numeric_event_attributes: list[str]
    item_types: list[str]
    suggested_basis: str


class EmissionFactorTemplate(Resource):
    """What the log offers each activity to compute emissions from.

    Run before configuring factors: it names, per activity, the numeric event
    attributes and the quantity item types available, so a rule can be written
    against something the log actually carries.
    """

    label: ClassVar[str] = "Emission Factor Template"
    description: ClassVar[str] = (
        "Per activity: how many events, which object types it involves, and "
        "which numeric attributes and item types a factor could use."
    )

    activities: list[ActivityProfile] = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    numeric_event_attributes: list[str] = Field(default_factory=list)
    numeric_object_attributes: list[str] = Field(default_factory=list)
    has_quantity_extension: bool = False

    def visualize(self) -> TableViz:
        return TableViz(
            columns=[
                TableColumn(id="activity", label="Activity"),
                TableColumn(id="events", label="Events", data_type="number"),
                TableColumn(id="suggested_basis", label="Suggested basis"),
                TableColumn(id="numeric_event_attributes", label="Numeric attributes"),
                TableColumn(id="item_types", label="Item types moved"),
                TableColumn(id="object_types", label="Object types involved"),
            ],
            rows=[
                {
                    "activity": profile.activity,
                    "events": profile.events,
                    "suggested_basis": profile.suggested_basis,
                    "numeric_event_attributes": ", ".join(
                        profile.numeric_event_attributes
                    )
                    or "—",
                    "item_types": ", ".join(profile.item_types) or "—",
                    "object_types": ", ".join(profile.object_types) or "—",
                }
                for profile in self.activities
            ],
        )


class AllocationCell(BaseModel):
    """One object type's share under one allocation rule."""

    rule: str
    object_type: str
    allocated_co2e: float
    share_of_allocated: float


class AllocationComparison(Resource):
    """The same emissions, allocated several ways.

    How much of a process's footprint an object type carries is partly a
    modelling decision. Running the rules side by side shows the size of that
    decision instead of leaving it implicit in a single number.
    """

    label: ClassVar[str] = "Allocation Comparison"
    description: ClassVar[str] = (
        "Allocated emissions per object type under each allocation rule, from "
        "one and the same set of event emissions."
    )

    unit: str
    total_co2e: float
    rules: list[str] = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=list)
    cells: list[AllocationCell] = Field(default_factory=list)
    unallocated_co2e: dict[str, float] = Field(default_factory=dict)
    #: Per object type, the largest share any rule gives it divided by the
    #: smallest -- how far the choice of rule can move that type's footprint.
    #: ``None`` where some rule allocates the type nothing at all, which is an
    #: unbounded difference rather than a large one.
    spread: dict[str, float | None] = Field(default_factory=dict)

    def visualize(self) -> Plotly:
        values: dict[str, dict[str, float]] = {}
        for cell in self.cells:
            values.setdefault(cell.rule, {})[cell.object_type] = cell.allocated_co2e
        return Plotly(
            figure=comparison_figure(
                unit=self.unit,
                object_types=self.object_types,
                rules=self.rules,
                values=values,
                unallocated=self.unallocated_co2e,
            )
        )
