"""The configuration models, which are also the generated user interface."""

from __future__ import annotations

import pytest
from ocel_carbon.inputs import (
    AllocationConfig,
    ComparisonInput,
    EmissionFactor,
    FootprintInput,
)
from pydantic import ValidationError


def test_an_attribute_factor_needs_an_attribute():
    with pytest.raises(ValidationError, match="needs an event attribute"):
        EmissionFactor(
            activity="Transport", basis="per_event_attribute", factor=1.0
        )


def test_a_quantity_factor_needs_an_item_type():
    with pytest.raises(ValidationError, match="needs an item type"):
        EmissionFactor(activity="Transport", basis="per_item_quantity", factor=1.0)


def test_a_weighted_allocation_needs_something_to_weigh_by():
    with pytest.raises(ValidationError, match="weight attribute"):
        AllocationConfig(rule="object_attribute")
    with pytest.raises(ValidationError, match="weight item type"):
        AllocationConfig(rule="item_quantity")


def test_factors_describe_themselves_for_the_report():
    flat = EmissionFactor(activity="Pack", factor=0.4)
    metered = EmissionFactor(
        activity="Transport",
        basis="per_event_attribute",
        factor=0.12,
        event_attribute="distance_km",
        note="EN 16258",
    )
    flowed = EmissionFactor(
        activity="Transport",
        basis="per_item_quantity",
        factor=2.68,
        item_type="diesel_l",
        quantity_measure="decrease",
    )

    assert flat.describe() == "0.4 per event"
    assert metered.describe() == "0.12 per unit of distance_km — EN 16258"
    assert flowed.describe() == "2.68 per unit of diesel_l (decrease)"


def test_allocation_rules_describe_themselves_for_the_chart():
    assert AllocationConfig(rule="equal").describe() == "equal split"
    assert (
        AllocationConfig(rule="equal", object_types=["Order", "Item"]).describe()
        == "equal split over Item, Order"
    )
    assert (
        AllocationConfig(
            rule="object_attribute", weight_attribute="weight_kg"
        ).describe()
        == "by weight_kg"
    )
    assert AllocationConfig(rule="equal", label="my rule").describe() == "my rule"


def test_the_defaults_alone_make_a_runnable_configuration():
    config = FootprintInput()

    assert config.factors == []
    assert config.default_factor == 0.0
    assert config.allocation.rule == "equal"
    assert config.variant_object_type is None
    assert ComparisonInput().allocations[0].rule == "equal"


def test_ocel_backed_pickers_stay_optional_and_keep_their_ui_metadata():
    """The halves of ``ocel_select``: a default, and Ocelescope's own metadata."""
    schema = FootprintInput.model_json_schema()
    field = schema["properties"]["variant_object_type"]

    assert "variant_object_type" not in schema.get("required", [])
    assert field["x-ui-meta"]["type"] == "ocel"
    assert field["x-ui-meta"]["field_type"] == "object_type"
    assert field["x-ui-meta"]["ocel_id"] == "ocel"

    # And a required picker keeps its metadata too.
    factor_schema = EmissionFactor.model_json_schema()
    assert factor_schema["properties"]["activity"]["x-ui-meta"]["field_type"] == (
        "event_type"
    )
    assert "activity" in factor_schema.get("required", [])


def test_the_ocel_id_of_every_picker_matches_the_log_parameter():
    """``ocel_id`` has to name the method's OCEL parameter, which is ``ocel``."""
    from ocel_carbon.plugin import CarbonFootprintPlugin

    log_parameters = {
        io.name
        for method in CarbonFootprintPlugin.method_map().values()
        for io in method.inputs
        if io.io_type == "ocel"
    }
    assert log_parameters == {"ocel"}

    def pickers(schema: dict) -> list[dict]:
        found = []
        for field in schema.get("properties", {}).values():
            meta = field.get("x-ui-meta")
            if meta and meta.get("type") == "ocel":
                found.append(meta)
        for definition in schema.get("$defs", {}).values():
            found.extend(pickers(definition))
        return found

    for model in (FootprintInput, ComparisonInput):
        metas = pickers(model.model_json_schema())
        assert metas, f"{model.__name__} has OCEL-backed pickers"
        assert {meta["ocel_id"] for meta in metas} <= log_parameters
