"""Step two: dividing each event's emissions among its objects."""

from __future__ import annotations

import pandas as pd
import pytest
from ocel_carbon.allocation import allocate_emissions, naive_per_object_type
from ocel_carbon.constants import OID
from ocel_carbon.inputs import AllocationConfig


@pytest.fixture
def emissions() -> pd.Series:
    """30 on the shared transport event, 10 on the production event."""
    return pd.Series({"e1": 30.0, "e2": 10.0, "e3": 0.0})


def test_equal_rule_divides_an_event_among_its_objects(tiny_ocel, emissions):
    result = allocate_emissions(tiny_ocel, emissions, AllocationConfig(rule="equal"))

    per_object = result.per_object()
    # e1 is shared by a truck and two orders: 10 each, not 30 each.
    assert per_object["truck"] == pytest.approx(10.0)
    assert per_object["order-1"] == pytest.approx(10.0)
    assert per_object["order-2"] == pytest.approx(10.0)


def test_allocation_conserves_every_event_s_emissions(tiny_ocel, emissions):
    result = allocate_emissions(tiny_ocel, emissions, AllocationConfig(rule="equal"))

    per_event = result.shares.groupby("ocel:eid")["co2e"].sum()
    assert per_event["e1"] == pytest.approx(30.0)
    assert per_event["e2"] == pytest.approx(10.0)
    assert result.diagnostics.allocated_co2e == pytest.approx(emissions.sum())
    assert result.diagnostics.unallocated_co2e == 0.0


def test_object_attribute_rule_is_proportional_to_the_attribute(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel,
        emissions,
        AllocationConfig(rule="object_attribute", weight_attribute="weight_kg"),
    )

    per_object = result.per_object()
    # e2's 10 go to the two items by weight (3 kg and 1 kg); the machine was
    # eligible but has no weight, so it is reported carrying nothing.
    assert per_object["item-1"] == pytest.approx(7.5)
    assert per_object["item-2"] == pytest.approx(2.5)
    assert per_object["machine"] == pytest.approx(0.0)


def test_weightless_events_fall_back_to_an_equal_split(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel,
        emissions,
        AllocationConfig(rule="object_attribute", weight_attribute="weight_kg"),
    )

    # Nothing on e1 has a weight, so its 30 are split three ways rather than lost.
    per_object = result.per_object()
    assert per_object["truck"] == pytest.approx(10.0)
    assert result.diagnostics.events_with_zero_weight == 1
    assert result.diagnostics.allocated_co2e == pytest.approx(40.0)


def test_weightless_events_can_be_left_unallocated_instead(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel,
        emissions,
        AllocationConfig(
            rule="object_attribute",
            weight_attribute="weight_kg",
            on_zero_weight="unallocated",
        ),
    )

    assert result.diagnostics.allocated_co2e == pytest.approx(10.0)
    assert result.diagnostics.unallocated_co2e == pytest.approx(30.0)
    assert result.diagnostics.total_co2e == pytest.approx(40.0)
    assert "truck" not in result.per_object()


def test_item_quantity_rule_follows_the_material_flow(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel,
        emissions,
        AllocationConfig(
            rule="item_quantity",
            weight_item_type="diesel_l",
            weight_measure="decrease",
        ),
    )

    per_object = result.per_object()
    # Only the truck draws diesel on e1, so it carries all of that event and the
    # orders on board carry none of it.
    assert per_object["truck"] == pytest.approx(30.0)
    assert per_object["order-1"] == pytest.approx(0.0)
    # e2 moves no diesel at all, so it falls back to an equal split.
    assert per_object["machine"] == pytest.approx(10.0 / 3)


def test_object_types_restrict_who_can_carry_emissions(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel, emissions, AllocationConfig(rule="equal", object_types=["Order"])
    )

    per_object = result.per_object()
    assert per_object["order-1"] == pytest.approx(15.0)
    assert per_object["order-2"] == pytest.approx(15.0)
    # e2 involves no order, so its emissions are reported, not silently dropped.
    assert result.diagnostics.unallocated_co2e == pytest.approx(10.0)
    assert result.diagnostics.events_without_eligible_object == 1
    assert set(result.unallocated["reason"]) == {"no eligible object"}


def test_qualifiers_restrict_who_can_carry_emissions(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel, emissions, AllocationConfig(rule="equal", qualifiers=["truck"])
    )

    assert result.per_object()["truck"] == pytest.approx(30.0)
    assert result.diagnostics.unallocated_co2e == pytest.approx(10.0)


def test_nothing_eligible_leaves_everything_unallocated(tiny_ocel, emissions):
    result = allocate_emissions(
        tiny_ocel, emissions, AllocationConfig(rule="equal", object_types=["Vessel"])
    )

    assert result.shares.empty
    assert result.diagnostics.unallocated_co2e == pytest.approx(40.0)
    assert result.per_object().empty


def test_zero_emission_events_are_not_allocated(tiny_ocel, emissions):
    result = allocate_emissions(tiny_ocel, emissions, AllocationConfig(rule="equal"))

    assert "e3" not in set(result.shares["ocel:eid"])


def test_no_emissions_at_all_is_an_empty_result(tiny_ocel):
    result = allocate_emissions(
        tiny_ocel,
        pd.Series({"e1": 0.0, "e2": 0.0, "e3": 0.0}),
        AllocationConfig(rule="equal"),
    )

    assert result.shares.empty
    assert result.diagnostics.total_co2e == 0.0


def test_an_object_related_twice_to_one_event_is_one_target():
    """Two qualifiers for the same object must not double its share."""
    from ocelescope import OCEL

    events = pd.DataFrame(
        [
            {
                "ocel:eid": "e1",
                "ocel:timestamp": pd.Timestamp("2026-01-01", tz="UTC"),
                "ocel:activity": "Transport",
            }
        ]
    )
    objects = pd.DataFrame(
        [
            {"ocel:oid": "truck", "ocel:type": "Truck"},
            {"ocel:oid": "order", "ocel:type": "Order"},
        ]
    )
    relations = pd.DataFrame(
        [
            {"ocel:eid": "e1", "ocel:oid": "truck", "ocel:qualifier": "truck"},
            {"ocel:eid": "e1", "ocel:oid": "truck", "ocel:qualifier": "vehicle"},
            {"ocel:eid": "e1", "ocel:oid": "order", "ocel:qualifier": "order"},
        ]
    ).assign(**{"ocel:activity": "Transport"})
    relations = relations.merge(objects, on="ocel:oid", how="left")
    log = OCEL.from_frames(events=events, objects=objects, relations=relations)

    result = allocate_emissions(
        log, pd.Series({"e1": 10.0}), AllocationConfig(rule="equal")
    )

    per_object = result.per_object()
    assert per_object["truck"] == pytest.approx(5.0)
    assert per_object["order"] == pytest.approx(5.0)


def test_an_unknown_weight_attribute_names_the_ones_the_log_has(tiny_ocel, emissions):
    with pytest.raises(ValueError, match="weight_kg"):
        allocate_emissions(
            tiny_ocel,
            emissions,
            AllocationConfig(rule="object_attribute", weight_attribute="volume_m3"),
        )


def test_counting_shared_events_whole_overstates_every_type(tiny_ocel, emissions):
    """The comparison the report makes: flattening double counts, allocation does not."""
    naive = naive_per_object_type(tiny_ocel, emissions)
    allocated = allocate_emissions(
        tiny_ocel, emissions, AllocationConfig(rule="equal")
    ).per_object_type()

    # Flattened to Order, e1 is a case for each of its two orders, so its 30 are
    # charged twice; flattened to Truck, once.
    assert naive["Truck"] == pytest.approx(30.0)
    assert naive["Order"] == pytest.approx(60.0)
    assert naive["Item"] == pytest.approx(20.0)  # e2 has two items, e3 emits nothing
    assert naive["Machine"] == pytest.approx(10.0)
    assert naive.sum() == pytest.approx(120.0)  # against 40 actually emitted
    assert allocated.sum() == pytest.approx(40.0)
    # No object type can be allocated more than a flattened reading gives it.
    assert (naive >= allocated.reindex(naive.index).fillna(0.0) - 1e-9).all()


def test_shares_name_the_object_type_they_belong_to(tiny_ocel, emissions):
    result = allocate_emissions(tiny_ocel, emissions, AllocationConfig(rule="equal"))

    by_type = result.per_object_type()
    assert by_type["Order"] == pytest.approx(20.0)
    assert set(result.shares.columns) == {"ocel:eid", OID, "ocel:type", "co2e"}
