"""Step two: splitting an event's emissions across the objects it involves.

An event in an object-centric log belongs to no single case. A transport event
relates to a truck, a route, several orders and every item on board, so there is
no case to charge its emissions to — only a set of objects and a choice of how
to divide one number among them.

The choice matters, and this module keeps it explicit:

* every event's emissions are divided, never copied, so the shares of one event
  sum back to that event's emissions and a total over object types is a real
  total rather than the same emissions counted once per type;
* emissions that no eligible object can carry are reported as unallocated
  instead of being quietly spread over the rest or dropped.

Summing emissions per object type *without* dividing them is what flattening a
log to one object type and reading it case by case amounts to, and it
double-counts every shared event. ``aggregate.py`` reports that number too, next
to the allocated one, because the gap between them is the point.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from ocelescope import OCEL

from .constants import EID, OID, OTYPE, QUALIFIER
from .emissions import measure_quantities
from .constants import ITEM_TYPE, QUANTITY
from .inputs import AllocationConfig

#: Column holding an allocated share in the frames this module returns.
CO2E = "co2e"
#: Column holding an allocation weight.
WEIGHT = "weight"


@dataclass
class AllocationDiagnostics:
    """Where the emissions went, and where they could not go."""

    eligible_relations: int = 0
    allocated_co2e: float = 0.0
    unallocated_co2e: float = 0.0
    events_without_eligible_object: int = 0
    events_with_zero_weight: int = 0
    zero_weight_handling: str = "equal"

    @property
    def total_co2e(self) -> float:
        return self.allocated_co2e + self.unallocated_co2e


@dataclass
class AllocationResult:
    """One share per event and object, plus what could not be allocated."""

    #: Columns: ``ocel:eid``, ``ocel:oid``, ``ocel:type``, ``co2e``.
    shares: pd.DataFrame
    #: Columns: ``ocel:eid``, ``co2e``, ``reason``.
    unallocated: pd.DataFrame
    diagnostics: AllocationDiagnostics

    def per_object(self) -> pd.Series:
        """Emissions allocated to each object, summed over its events."""
        if self.shares.empty:
            return pd.Series(dtype=float, name=CO2E)
        return self.shares.groupby(OID)[CO2E].sum()

    def per_object_type(self) -> pd.Series:
        """Emissions allocated to each object type, summed over its objects."""
        if self.shares.empty:
            return pd.Series(dtype=float, name=CO2E)
        return self.shares.groupby(OTYPE)[CO2E].sum()

    def events_per_object(self) -> pd.Series:
        """How many emitting events each object took part in."""
        if self.shares.empty:
            return pd.Series(dtype=int)
        return self.shares.groupby(OID)[EID].nunique()


def _object_attribute_weights(ocel: OCEL, attribute: str) -> pd.Series:
    """A numeric object attribute, as a weight per object."""
    objects = ocel.objects.df
    if attribute not in objects.columns:
        available = ", ".join(ocel.objects.attribute_names) or "none"
        raise ValueError(
            f"no object attribute '{attribute}' in this log; available: {available}"
        )
    weights = pd.to_numeric(objects[attribute], errors="coerce").fillna(0.0)
    # A negative weight has no meaning as a share of emissions.
    return weights.clip(lower=0.0).groupby(objects[OID]).max()


def _item_quantity_weights(
    ocel: OCEL, item_type: str, measure: str
) -> pd.Series:
    """The quantity of one item type an event moves per object, as a weight.

    Indexed by ``(event, object)``: unlike an object attribute, this weight is a
    property of the event's involvement of the object, not of the object.
    """
    operations = ocel.quantities.qop
    if operations.empty:
        raise ValueError(
            "this log carries no quantity operations, so emissions cannot be "
            "allocated by quantity; use the 'equal' or 'object_attribute' rule"
        )
    selected = operations[operations[ITEM_TYPE] == item_type]
    if selected.empty:
        available = ", ".join(ocel.quantities.item_types) or "none"
        raise ValueError(
            f"no quantity operations for item type '{item_type}'; "
            f"this log has: {available}"
        )
    measured = measure_quantities(selected[QUANTITY], measure)  # type: ignore[arg-type]
    return measured.groupby([selected[EID], selected[OID]]).sum()


def allocate_emissions(
    ocel: OCEL,
    event_emissions: pd.Series,
    config: AllocationConfig,
) -> AllocationResult:
    """Divide each event's emissions among the objects the config makes eligible.

    Only events with non-zero emissions are allocated; an event relating to the
    same object under two qualifiers is one target, not two.
    """
    diagnostics = AllocationDiagnostics(zero_weight_handling=config.on_zero_weight)
    empty_shares = pd.DataFrame(columns=[EID, OID, OTYPE, CO2E])
    empty_unallocated = pd.DataFrame(columns=[EID, CO2E, "reason"])

    emitting = event_emissions[event_emissions != 0.0]
    if emitting.empty:
        return AllocationResult(empty_shares, empty_unallocated, diagnostics)

    relations = ocel.e2o.df
    relations = relations[relations[EID].isin(emitting.index)]

    eligible = relations
    if config.object_types:
        eligible = eligible[eligible[OTYPE].isin(config.object_types)]
    if config.qualifiers:
        eligible = eligible[eligible[QUALIFIER].isin(config.qualifiers)]
    eligible = eligible[[EID, OID, OTYPE]].drop_duplicates(subset=[EID, OID]).copy()
    diagnostics.eligible_relations = len(eligible)

    unallocated: list[pd.DataFrame] = []

    orphaned = emitting[~emitting.index.isin(set(eligible[EID]))]
    if not orphaned.empty:
        diagnostics.events_without_eligible_object = len(orphaned)
        unallocated.append(
            pd.DataFrame(
                {
                    EID: orphaned.index,
                    CO2E: orphaned.to_numpy(),
                    "reason": "no eligible object",
                }
            )
        )

    if eligible.empty:
        combined = (
            pd.concat(unallocated, ignore_index=True)
            if unallocated
            else empty_unallocated
        )
        diagnostics.unallocated_co2e = float(combined[CO2E].sum())
        return AllocationResult(empty_shares, combined, diagnostics)

    if config.rule == "equal":
        eligible[WEIGHT] = 1.0
    elif config.rule == "object_attribute":
        attribute = config.weight_attribute
        assert attribute is not None  # guaranteed by AllocationConfig
        weights = _object_attribute_weights(ocel, attribute)
        eligible[WEIGHT] = eligible[OID].map(weights).fillna(0.0)
    else:
        item_type = config.weight_item_type
        assert item_type is not None  # guaranteed by AllocationConfig
        weights = _item_quantity_weights(ocel, item_type, config.weight_measure)
        keys = pd.MultiIndex.from_arrays([eligible[EID], eligible[OID]])
        eligible[WEIGHT] = (
            pd.Series(keys.map(weights), index=eligible.index).astype(float).fillna(0.0)
        )

    weight_totals = eligible.groupby(EID)[WEIGHT].transform("sum")
    weightless = weight_totals <= 0.0
    weightless_events = eligible.loc[weightless, EID].nunique()
    diagnostics.events_with_zero_weight = int(weightless_events)

    if weightless.any():
        if config.on_zero_weight == "equal":
            # Nothing distinguishes the objects, so an equal split is the only
            # defensible division left -- and it keeps the emissions in the log.
            eligible.loc[weightless, WEIGHT] = 1.0
            weight_totals = eligible.groupby(EID)[WEIGHT].transform("sum")
        else:
            lost = (
                eligible.loc[weightless, [EID]]
                .drop_duplicates()
                .assign(**{CO2E: lambda frame: frame[EID].map(emitting)})
            )
            lost["reason"] = "every eligible object weighs zero"
            unallocated.append(lost[[EID, CO2E, "reason"]])
            eligible = eligible[~weightless]
            weight_totals = weight_totals[~weightless]

    shares = eligible[[EID, OID, OTYPE]].copy()
    if eligible.empty:
        shares[CO2E] = pd.Series(dtype=float)
    else:
        shares[CO2E] = (
            eligible[WEIGHT] / weight_totals * eligible[EID].map(emitting)
        ).astype(float)

    combined = (
        pd.concat(unallocated, ignore_index=True) if unallocated else empty_unallocated
    )
    combined[CO2E] = combined[CO2E].astype(float)

    diagnostics.allocated_co2e = float(shares[CO2E].sum())
    diagnostics.unallocated_co2e = float(combined[CO2E].sum())

    return AllocationResult(
        shares=shares.reset_index(drop=True),
        unallocated=combined,
        diagnostics=diagnostics,
    )


def naive_per_object_type(ocel: OCEL, event_emissions: pd.Series) -> pd.Series:
    """Each object type's emissions *without* dividing shared events.

    This is the number a log flattened to one object type reports. Flattening
    makes every object of that type a case, so an event involving ``k`` of them
    appears in ``k`` cases and is charged ``k`` times; summing the cases then
    charges the type ``k`` times the event's emissions.

    Over the object types it sums to more than the log emitted whenever an event
    is shared -- and the excess is exactly the double counting that allocation
    removes.
    """
    involvement = ocel.e2o.df[[EID, OTYPE, OID]].drop_duplicates()
    if involvement.empty:
        return pd.Series(dtype=float)
    counted = (
        involvement.groupby([EID, OTYPE])[OID].nunique().reset_index(name="objects")
    )
    counted[CO2E] = (
        counted[EID].map(event_emissions).fillna(0.0) * counted["objects"]
    )
    return counted.groupby(OTYPE)[CO2E].sum()
