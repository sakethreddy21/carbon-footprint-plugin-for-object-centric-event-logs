"""Step one: the emissions charged to each event."""

from __future__ import annotations

import pandas as pd
import pytest
from ocel_carbon.emissions import compute_event_emissions, measure_quantities
from ocel_carbon.inputs import EmissionFactor


def test_per_event_basis_charges_every_event_of_the_activity(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel, [EmissionFactor(activity="Transport", factor=5.0)]
    )

    assert result.per_event["e1"] == 5.0
    assert result.per_event["e2"] == 0.0
    assert result.total == 5.0


def test_attribute_basis_multiplies_the_attribute(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel,
        [
            EmissionFactor(
                activity="Transport",
                basis="per_event_attribute",
                factor=0.2,
                event_attribute="distance_km",
            )
        ],
    )

    assert result.per_event["e1"] == pytest.approx(20.0)


def test_quantity_basis_uses_the_item_the_event_moves(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel,
        [
            EmissionFactor(
                activity="Produce",
                basis="per_item_quantity",
                factor=0.4,
                item_type="electricity_kwh",
                quantity_measure="decrease",
            )
        ],
    )

    # e2 draws 10 kWh, recorded as a decrease of the available quantity.
    assert result.per_event["e2"] == pytest.approx(4.0)
    assert result.per_event["e1"] == 0.0


def test_several_rules_for_one_activity_add_up(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel,
        [
            EmissionFactor(activity="Transport", factor=2.0),
            EmissionFactor(
                activity="Transport",
                basis="per_event_attribute",
                factor=0.1,
                event_attribute="distance_km",
            ),
            EmissionFactor(
                activity="Transport",
                basis="per_item_quantity",
                factor=2.68,
                item_type="diesel_l",
                quantity_measure="decrease",
            ),
        ],
    )

    assert result.per_event["e1"] == pytest.approx(2.0 + 10.0 + 67.0)
    assert len(result.diagnostics.covered_activities["Transport"]) == 3


def test_activities_without_a_factor_are_reported_and_left_at_zero(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel, [EmissionFactor(activity="Transport", factor=1.0)]
    )

    assert result.diagnostics.uncovered_activities == {"Produce": 1, "Inspect": 1}
    assert result.diagnostics.uncovered_event_count == 2
    assert result.per_event["e3"] == 0.0


def test_default_factor_covers_the_remaining_activities(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel,
        [EmissionFactor(activity="Transport", factor=1.0)],
        default_factor=0.5,
    )

    assert result.per_event["e2"] == 0.5
    assert result.per_event["e3"] == 0.5
    # Still reported as uncovered: a default is an assumption, not a factor.
    assert "Inspect" in result.diagnostics.uncovered_activities


def test_a_missing_event_attribute_is_diagnosed_not_guessed(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel,
        [
            EmissionFactor(
                activity="Transport",
                basis="per_event_attribute",
                factor=1.0,
                event_attribute="payload_t",
            )
        ],
    )

    assert result.total == 0.0
    assert result.diagnostics.missing_attributes == {"payload_t": 1}
    assert "Transport" not in result.diagnostics.covered_activities


def test_a_quantity_factor_on_a_log_without_quantities_is_an_error():
    import pandas as pd
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
    objects = pd.DataFrame([{"ocel:oid": "t", "ocel:type": "Truck"}])
    relations = pd.DataFrame(
        [
            {
                "ocel:eid": "e1",
                "ocel:oid": "t",
                "ocel:qualifier": "truck",
                "ocel:activity": "Transport",
                "ocel:type": "Truck",
            }
        ]
    )
    log = OCEL.from_frames(events=events, objects=objects, relations=relations)

    with pytest.raises(ValueError, match="no quantity operations"):
        compute_event_emissions(
            log,
            [
                EmissionFactor(
                    activity="Transport",
                    basis="per_item_quantity",
                    factor=1.0,
                    item_type="diesel_l",
                )
            ],
        )


def test_an_unknown_item_type_names_the_ones_the_log_has(tiny_ocel):
    with pytest.raises(ValueError, match="diesel_l"):
        compute_event_emissions(
            tiny_ocel,
            [
                EmissionFactor(
                    activity="Transport",
                    basis="per_item_quantity",
                    factor=1.0,
                    item_type="kerosene_l",
                )
            ],
        )


def test_a_factor_for_an_absent_activity_changes_nothing(tiny_ocel):
    result = compute_event_emissions(
        tiny_ocel, [EmissionFactor(activity="Recycle", factor=99.0)]
    )

    assert result.total == 0.0
    assert "Recycle" not in result.diagnostics.covered_activities


def test_quantity_measures_read_the_sign_as_asked():
    operations = pd.Series([-4.0, 2.0, 0.0])

    assert list(measure_quantities(operations, "absolute")) == [4.0, 2.0, 0.0]
    assert list(measure_quantities(operations, "increase")) == [0.0, 2.0, 0.0]
    assert list(measure_quantities(operations, "decrease")) == [4.0, 0.0, 0.0]
    assert list(measure_quantities(operations, "signed")) == [-4.0, 2.0, 0.0]
