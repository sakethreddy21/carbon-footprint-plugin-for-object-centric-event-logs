"""Turning the computed emissions and shares into the reported resources."""

from __future__ import annotations

import copy

import pandas as pd
from ocelescope import OCEL
from ocelescope.ocel.constants.executions import (
    EXECUTION_ACT_LIST_COL,
    EXECUTION_VARIANT_ID_COL,
)

from .allocation import CO2E, AllocationResult, naive_per_object_type
from .constants import (
    ACTIVITY,
    EID,
    EVENT_CO2_ATTRIBUTE,
    ITEM_TYPE,
    OBJECT_CO2_ATTRIBUTE,
    OID,
    OTYPE,
)
from .emissions import EmissionResult
from .inputs import AllocationConfig
from .resources import (
    ActivityFootprint,
    ActivityProfile,
    AllocationCell,
    AllocationComparison,
    CarbonFootprint,
    EmissionFactorTemplate,
    FootprintDiagnostics,
    ObjectFootprint,
    ObjectFootprintTable,
    ObjectTypeFootprint,
    VariantFootprint,
)


def _numeric_columns(frame: pd.DataFrame, skip: set[str]) -> list[str]:
    """The columns of ``frame`` that hold numbers, attribute columns only."""
    numeric: list[str] = []
    for column in frame.columns:
        if column in skip:
            continue
        values = frame[column].dropna()
        if values.empty:
            continue
        if pd.to_numeric(values, errors="coerce").notna().all():
            numeric.append(str(column))
    return numeric


def build_template(ocel: OCEL) -> EmissionFactorTemplate:
    """Describe what the log offers each activity to build a factor on."""
    events = ocel.events.df
    numeric_event_attributes = _numeric_columns(
        events, {EID, ACTIVITY, "ocel:timestamp"}
    )
    objects = ocel.objects.df
    numeric_object_attributes = _numeric_columns(objects, {OID, OTYPE})

    relations = ocel.e2o.df[[EID, ACTIVITY, OTYPE]].drop_duplicates()
    types_per_activity = (
        relations.groupby(ACTIVITY)[OTYPE].apply(lambda values: sorted(set(values)))
        if not relations.empty
        else pd.Series(dtype=object)
    )

    operations = ocel.quantities.qop
    items_per_activity: dict[str, list[str]] = {}
    if not operations.empty:
        activity_of = ocel.events.activity_by_id
        labelled = operations[[EID, ITEM_TYPE]].copy()
        labelled[ACTIVITY] = labelled[EID].map(activity_of)
        items_per_activity = {
            str(activity): sorted(set(group))
            for activity, group in labelled.dropna(subset=[ACTIVITY]).groupby(ACTIVITY)[
                ITEM_TYPE
            ]
        }

    profiles: list[ActivityProfile] = []
    for activity, count in ocel.events.activity_counts.items():
        activity = str(activity)
        of_activity = events[events[ACTIVITY] == activity]
        attributes = [
            column
            for column in numeric_event_attributes
            if of_activity[column].notna().any()
        ]
        items = items_per_activity.get(activity, [])
        if items:
            suggested = "per_item_quantity"
        elif attributes:
            suggested = "per_event_attribute"
        else:
            suggested = "per_event"
        profiles.append(
            ActivityProfile(
                activity=activity,
                events=int(count),
                object_types=list(types_per_activity.get(activity, [])),
                numeric_event_attributes=attributes,
                item_types=items,
                suggested_basis=suggested,
            )
        )

    profiles.sort(key=lambda profile: (-profile.events, profile.activity))

    return EmissionFactorTemplate(
        activities=profiles,
        object_types=ocel.objects.types,
        item_types=ocel.quantities.item_types,
        numeric_event_attributes=numeric_event_attributes,
        numeric_object_attributes=numeric_object_attributes,
        has_quantity_extension=not operations.empty,
    )


def _activity_rows(
    ocel: OCEL, emissions: EmissionResult, total: float
) -> list[ActivityFootprint]:
    events = ocel.events.df[[EID, ACTIVITY]].copy()
    events[CO2E] = events[EID].map(emissions.per_event).fillna(0.0)
    grouped = events.groupby(ACTIVITY)[CO2E].agg(["sum", "count", "mean"])

    rows = [
        ActivityFootprint(
            activity=str(activity),
            events=int(stats["count"]),
            co2e=float(stats["sum"]),
            share_of_total=float(stats["sum"] / total) if total else 0.0,
            mean_per_event=float(stats["mean"]),
            rules=emissions.diagnostics.covered_activities.get(str(activity), []),
        )
        for activity, stats in grouped.iterrows()
    ]
    rows.sort(key=lambda row: (-row.co2e, row.activity))
    return rows


def _object_type_rows(
    ocel: OCEL, emissions: EmissionResult, allocation: AllocationResult
) -> list[ObjectTypeFootprint]:
    allocated = allocation.per_object_type()
    naive = naive_per_object_type(ocel, emissions.per_event)
    allocated_total = float(allocated.sum())

    charged_objects = (
        allocation.shares[allocation.shares[CO2E] != 0]
        .groupby(OTYPE)[OID]
        .nunique()
        if not allocation.shares.empty
        else pd.Series(dtype=int)
    )
    object_counts = ocel.objects.counts

    rows: list[ObjectTypeFootprint] = []
    for object_type in sorted(set(allocated.index) | set(naive.index)):
        allocated_amount = float(allocated.get(object_type, 0.0))
        naive_amount = float(naive.get(object_type, 0.0))
        objects_charged = int(charged_objects.get(object_type, 0))
        rows.append(
            ObjectTypeFootprint(
                object_type=str(object_type),
                objects_charged=objects_charged,
                allocated_co2e=allocated_amount,
                share_of_allocated=(
                    allocated_amount / allocated_total if allocated_total else 0.0
                ),
                mean_per_object=(
                    allocated_amount / int(object_counts.get(object_type, 0))
                    if int(object_counts.get(object_type, 0))
                    else 0.0
                ),
                naive_co2e=naive_amount,
                naive_inflation=(
                    naive_amount / allocated_amount if allocated_amount else None
                ),
            )
        )
    rows.sort(key=lambda row: (-row.allocated_co2e, row.object_type))
    return rows


def _variant_rows(
    ocel: OCEL,
    allocation: AllocationResult,
    object_type: str,
    limit: int,
) -> list[VariantFootprint]:
    """Emissions per process variant of one object type.

    A variant is a distinct activity sequence; its objects' allocated shares are
    summed, and the mean per object is what makes two variants comparable when
    different numbers of objects follow them.
    """
    executions = ocel.executions.get_object_executions(object_types=[object_type])
    if executions.empty:
        return []

    per_object = allocation.per_object()
    executions = executions.copy()
    executions[CO2E] = (
        pd.Series(executions.index, index=executions.index).map(per_object).fillna(0.0)
    )

    rows: list[VariantFootprint] = []
    for variant_id, group in executions.groupby(EXECUTION_VARIANT_ID_COL):
        total = float(group[CO2E].sum())
        rows.append(
            VariantFootprint(
                variant_id=str(variant_id),
                object_type=object_type,
                activities=list(group[EXECUTION_ACT_LIST_COL].iloc[0]),
                objects=int(len(group)),
                total_co2e=total,
                mean_co2e=total / len(group),
            )
        )
    rows.sort(key=lambda row: -row.total_co2e)
    return rows[:limit]


def _object_rows(
    ocel: OCEL, allocation: AllocationResult, limit: int
) -> list[ObjectFootprint]:
    if limit <= 0 or allocation.shares.empty:
        return []
    per_object = allocation.per_object().sort_values(ascending=False).head(limit)
    event_counts = allocation.events_per_object()
    type_of = ocel.objects.type_by_id
    return [
        ObjectFootprint(
            object_id=str(object_id),
            object_type=str(type_of.get(object_id, "")),
            allocated_co2e=float(amount),
            emitting_events=int(event_counts.get(object_id, 0)),
        )
        for object_id, amount in per_object.items()
    ]


def build_footprint(
    ocel: OCEL,
    emissions: EmissionResult,
    allocation: AllocationResult,
    *,
    unit: str,
    allocation_label: str,
    variant_object_type: str | None = None,
    max_variants: int = 20,
    top_objects: int = 25,
) -> tuple[CarbonFootprint, ObjectFootprintTable]:
    """Assemble the reported footprint from the two computation steps."""
    total = emissions.total
    allocated = allocation.diagnostics.allocated_co2e
    unallocated = allocation.diagnostics.unallocated_co2e

    variants: list[VariantFootprint] = []
    if variant_object_type:
        variants = _variant_rows(
            ocel, allocation, variant_object_type, limit=max_variants
        )

    diagnostics = FootprintDiagnostics(
        events_total=emissions.diagnostics.events_total,
        events_with_emissions=emissions.diagnostics.events_with_emissions,
        activities_covered=emissions.diagnostics.covered_activities,
        activities_without_factor=emissions.diagnostics.uncovered_activities,
        missing_event_attributes=emissions.diagnostics.missing_attributes,
        non_numeric_attribute_values=emissions.diagnostics.non_numeric_attribute_values,
        events_without_quantities=emissions.diagnostics.events_without_quantities,
        events_without_eligible_object=allocation.diagnostics.events_without_eligible_object,
        events_with_zero_allocation_weight=allocation.diagnostics.events_with_zero_weight,
        zero_weight_handling=allocation.diagnostics.zero_weight_handling,
        mass_balance_error=total - (allocated + unallocated),
    )

    footprint = CarbonFootprint(
        unit=unit,
        allocation=allocation_label,
        total_co2e=total,
        allocated_co2e=allocated,
        unallocated_co2e=unallocated,
        by_activity=_activity_rows(ocel, emissions, total),
        by_object_type=_object_type_rows(ocel, emissions, allocation),
        by_variant=variants,
        diagnostics=diagnostics,
    )
    hotspots = ObjectFootprintTable(
        unit=unit,
        allocation=allocation_label,
        rows=_object_rows(ocel, allocation, top_objects),
    )
    return footprint, hotspots


def build_comparison(
    ocel: OCEL,
    emissions: EmissionResult,
    results: list[tuple[AllocationConfig, AllocationResult]],
    *,
    unit: str,
) -> AllocationComparison:
    """Put the per-object-type shares of several allocation rules side by side."""
    labels = [config.describe() for config, _ in results]
    object_types: set[str] = set()
    cells: list[AllocationCell] = []
    unallocated: dict[str, float] = {}

    for label, (_, allocation) in zip(labels, results, strict=True):
        per_type = allocation.per_object_type()
        allocated_total = float(per_type.sum())
        object_types.update(str(name) for name in per_type.index)
        for object_type, amount in per_type.items():
            cells.append(
                AllocationCell(
                    rule=label,
                    object_type=str(object_type),
                    allocated_co2e=float(amount),
                    share_of_allocated=(
                        float(amount) / allocated_total if allocated_total else 0.0
                    ),
                )
            )
        unallocated[label] = allocation.diagnostics.unallocated_co2e

    ordered_types = sorted(
        object_types,
        key=lambda name: -max(
            (cell.allocated_co2e for cell in cells if cell.object_type == name),
            default=0.0,
        ),
    )

    spread: dict[str, float | None] = {}
    for object_type in ordered_types:
        amounts = [
            next(
                (
                    cell.allocated_co2e
                    for cell in cells
                    if cell.rule == label and cell.object_type == object_type
                ),
                0.0,
            )
            for label in labels
        ]
        smallest, largest = min(amounts), max(amounts)
        # A rule that allocates this type nothing makes the ratio unbounded,
        # which is a fact about the rules, not a number to chart.
        spread[object_type] = largest / smallest if smallest > 0 else None

    return AllocationComparison(
        unit=unit,
        total_co2e=emissions.total,
        rules=labels,
        object_types=ordered_types,
        cells=cells,
        unallocated_co2e=unallocated,
        spread=spread,
    )


def annotate_log(
    ocel: OCEL,
    emissions: EmissionResult,
    allocation: AllocationResult,
) -> OCEL:
    """A copy of the log carrying the emissions as event and object attributes.

    The event attribute holds what the event emitted; the object attribute holds
    the share allocated to the object, so summing either over the whole log gives
    the same total. Both are ordinary OCEL attributes, which leaves them to be
    filtered on, charted or read by the next plugin.
    """
    annotated = copy.deepcopy(ocel)

    events = annotated.events.df
    events[EVENT_CO2_ATTRIBUTE] = events[EID].map(emissions.per_event).fillna(0.0)
    annotated.events.df = events

    objects = annotated.objects.df
    objects[OBJECT_CO2_ATTRIBUTE] = (
        objects[OID].map(allocation.per_object()).fillna(0.0)
    )
    annotated.objects.df = objects

    return annotated
