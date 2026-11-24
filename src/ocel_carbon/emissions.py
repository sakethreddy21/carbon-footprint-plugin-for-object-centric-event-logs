"""Step one: how much each event emits.

This step is case-centric in spirit — an event gets a number — and says nothing
about objects. Splitting that number across the objects an event involves is
``allocation.py``'s job, and the two are kept apart because only the second one
is a modelling choice.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
from ocelescope import OCEL

from .constants import ACTIVITY, EID, ITEM_TYPE, QUANTITY
from .inputs import EmissionFactor, QuantityMeasure


def measure_quantities(quantities: pd.Series, measure: QuantityMeasure) -> pd.Series:
    """Read signed quantity operations as a magnitude.

    A quantity operation is the change an event makes to an object's item level,
    so it is signed. ``absolute`` takes its magnitude, ``increase`` keeps only
    additions, ``decrease`` keeps only removals (as a positive number), and
    ``signed`` leaves it alone, which lets additions and removals cancel.
    """
    numbers = pd.to_numeric(quantities, errors="coerce").fillna(0.0)
    if measure == "absolute":
        return numbers.abs()
    if measure == "increase":
        return numbers.clip(lower=0.0)
    if measure == "decrease":
        return (-numbers).clip(lower=0.0)
    return numbers


@dataclass
class EmissionDiagnostics:
    """What the factors did and did not cover.

    Carried into the report because a footprint whose coverage is unknown is not
    a result: an activity nobody wrote a factor for is the usual reason a total
    looks too low.
    """

    events_total: int = 0
    events_with_emissions: int = 0
    covered_activities: dict[str, list[str]] = field(default_factory=dict)
    uncovered_activities: dict[str, int] = field(default_factory=dict)
    missing_attributes: dict[str, int] = field(default_factory=dict)
    non_numeric_attribute_values: dict[str, int] = field(default_factory=dict)
    events_without_quantities: dict[str, int] = field(default_factory=dict)

    @property
    def uncovered_event_count(self) -> int:
        return sum(self.uncovered_activities.values())


@dataclass
class EmissionResult:
    """Emissions per event, with the diagnostics of how they were derived."""

    per_event: pd.Series
    diagnostics: EmissionDiagnostics

    @property
    def total(self) -> float:
        return float(self.per_event.sum())


def _quantity_per_event(
    ocel: OCEL, item_type: str, measure: QuantityMeasure
) -> pd.Series:
    """How much of ``item_type`` each event moves, summed over its objects.

    An event may operate on the same item type for several objects — a transfer
    moving material out of one silo and into another — and each operation is a
    flow the event caused, so they add up.
    """
    operations = ocel.quantities.qop
    if operations.empty:
        raise ValueError(
            "this log carries no quantity operations, so a 'per_item_quantity' "
            "factor has nothing to multiply; use the 'per_event' or "
            "'per_event_attribute' basis, or load a log with the quantity extension"
        )

    selected = operations[operations[ITEM_TYPE] == item_type]
    if selected.empty:
        available = ", ".join(ocel.quantities.item_types) or "none"
        raise ValueError(
            f"no quantity operations for item type '{item_type}'; "
            f"this log has: {available}"
        )

    measured = measure_quantities(selected[QUANTITY], measure)
    return measured.groupby(selected[EID]).sum()


def compute_event_emissions(
    ocel: OCEL,
    factors: list[EmissionFactor],
    default_factor: float = 0.0,
) -> EmissionResult:
    """Charge each event the emissions its activity's factors come to.

    Rules naming the same activity add up. Events of an activity no rule names
    are charged ``default_factor`` each, which is zero unless the caller says
    otherwise, and are counted in the diagnostics either way.
    """
    events = ocel.events.df
    diagnostics = EmissionDiagnostics(events_total=len(events))

    working = events[[EID, ACTIVITY]].copy()
    working["co2e"] = 0.0

    for factor in factors:
        selected = working[ACTIVITY] == factor.activity
        if not selected.any():
            # A factor for an activity this log does not have is harmless, but
            # the report should not claim the activity was covered.
            continue

        if factor.basis == "per_event":
            values = pd.Series(factor.factor, index=working.index[selected])

        elif factor.basis == "per_event_attribute":
            attribute = factor.event_attribute
            assert attribute is not None  # guaranteed by EmissionFactor
            if attribute not in events.columns:
                diagnostics.missing_attributes[attribute] = int(selected.sum())
                continue
            raw = events.loc[selected, attribute]
            numbers = pd.to_numeric(raw, errors="coerce")
            unusable = int(numbers.isna().sum())
            if unusable:
                diagnostics.non_numeric_attribute_values[attribute] = (
                    diagnostics.non_numeric_attribute_values.get(attribute, 0)
                    + unusable
                )
            values = numbers.fillna(0.0) * factor.factor

        else:
            item_type = factor.item_type
            assert item_type is not None  # guaranteed by EmissionFactor
            per_event = _quantity_per_event(ocel, item_type, factor.quantity_measure)
            moved = working.loc[selected, EID].map(per_event)
            untouched = int(moved.isna().sum())
            if untouched:
                key = f"{factor.activity} / {item_type}"
                diagnostics.events_without_quantities[key] = untouched
            values = moved.fillna(0.0) * factor.factor

        working.loc[selected, "co2e"] += values
        diagnostics.covered_activities.setdefault(factor.activity, []).append(
            factor.describe()
        )

    covered = set(diagnostics.covered_activities)
    uncovered = working[~working[ACTIVITY].isin(covered)]
    if not uncovered.empty:
        diagnostics.uncovered_activities = {
            str(activity): int(count)
            for activity, count in uncovered[ACTIVITY].value_counts().items()
        }
        if default_factor:
            working.loc[uncovered.index, "co2e"] += default_factor

    per_event = working.set_index(EID)["co2e"].astype(float)
    diagnostics.events_with_emissions = int((per_event != 0).sum())

    return EmissionResult(per_event=per_event, diagnostics=diagnostics)
