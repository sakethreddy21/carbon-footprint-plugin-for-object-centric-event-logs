"""The plugin as Ocelescope sees it, end to end on the sample log."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from ocel_carbon import CarbonFootprintPlugin
from ocel_carbon.constants import EVENT_CO2_ATTRIBUTE, OBJECT_CO2_ATTRIBUTE
from ocel_carbon.inputs import (
    AllocationConfig,
    ComparisonInput,
    EmissionFactor,
    FootprintInput,
)
from ocel_carbon.resources import (
    AllocationComparison,
    CarbonFootprint,
    EmissionFactorTemplate,
    ObjectFootprintTable,
)
from ocelescope import OCEL, Plotly, TableViz


def ocel_file_io_available() -> bool:
    """Whether the installed Ocelescope and r4pm agree on their bindings.

    ``ocelescope`` 0.13.0 reads and writes OCEL files through
    ``r4pm.bindings.stream_ocel_to_duckdb`` and
    ``read_consolidated_ocel_from_duckdb``, which ``r4pm-full`` 0.7.1 does not
    have. The plugin never touches files -- it is handed a log -- so the gap
    only costs this one test, which skips rather than failing on it.
    """
    try:
        import r4pm.bindings as bindings
    except ImportError:  # pragma: no cover - r4pm ships with ocelescope
        return False
    return hasattr(bindings, "read_consolidated_ocel_from_duckdb")

FACTORS = [
    EmissionFactor(
        activity="Transport",
        basis="per_item_quantity",
        factor=2.68,
        item_type="diesel_l",
        quantity_measure="decrease",
        note="diesel combustion",
    ),
    EmissionFactor(
        activity="Produce Item",
        basis="per_item_quantity",
        factor=0.38,
        item_type="electricity_kwh",
        quantity_measure="decrease",
        note="grid electricity",
    ),
    EmissionFactor(activity="Pack Pallet", basis="per_event", factor=0.4),
]


@pytest.fixture(scope="module")
def plugin() -> CarbonFootprintPlugin:
    return CarbonFootprintPlugin()


@pytest.fixture(scope="module")
def footprint(plugin, sample_ocel):
    return plugin.compute_footprint(
        sample_ocel,
        FootprintInput(
            factors=FACTORS,
            allocation=AllocationConfig(rule="equal"),
            variant_object_type="Order",
        ),
    )


def test_the_framework_can_describe_the_plugin():
    """What Ocelescope reads off the class to build the UI."""
    methods = CarbonFootprintPlugin.method_map()

    assert set(methods) == {"inspect_log", "compute_footprint", "compare_allocation"}

    compute = methods["compute_footprint"]
    assert compute.label == "Compute footprint"
    assert [io.io_type for io in compute.inputs] == ["ocel"]
    assert compute.inputs[0].label == "Event log"
    assert compute.configuration_input is FootprintInput
    assert [io.type for io in compute.outputs] == [
        OCEL,
        CarbonFootprint,
        ObjectFootprintTable,
    ]

    inspect = methods["inspect_log"]
    assert inspect.configuration_input is None

    assert set(CarbonFootprintPlugin.get_resources()) == {
        CarbonFootprint,
        ObjectFootprintTable,
        EmissionFactorTemplate,
        AllocationComparison,
    }


def test_every_resource_the_plugin_returns_can_be_visualized():
    for resource in CarbonFootprintPlugin.get_resources():
        assert resource.has_visualization(), resource.__name__


def test_inspect_log_describes_what_each_activity_offers(plugin, sample_ocel):
    template = plugin.inspect_log(sample_ocel)

    assert template.has_quantity_extension
    assert "diesel_l" in template.item_types
    assert "distance_km" in template.numeric_event_attributes
    assert "weight_kg" in template.numeric_object_attributes

    by_activity = {profile.activity: profile for profile in template.activities}
    transport = by_activity["Transport"]
    assert transport.suggested_basis == "per_item_quantity"
    assert transport.item_types == ["diesel_l"]
    assert "distance_km" in transport.numeric_event_attributes
    assert set(transport.object_types) == {"Truck", "Pallet", "Order"}

    # An activity with neither a reading nor a flow can only be charged per event.
    assert by_activity["Place Order"].suggested_basis == "per_event"

    rows = template.visualize().rows
    assert len(rows) == len(template.activities)


def test_compute_footprint_conserves_the_emissions_it_computed(footprint):
    _, report, _ = footprint

    assert report.total_co2e > 0
    assert report.allocated_co2e + report.unallocated_co2e == pytest.approx(
        report.total_co2e
    )
    assert report.diagnostics.mass_balance_error == pytest.approx(0.0, abs=1e-9)

    # Allocation partitions the emissions, so the object types sum to the total.
    assert sum(row.allocated_co2e for row in report.by_object_type) == pytest.approx(
        report.allocated_co2e
    )
    # And the activities sum to it too, from the other direction.
    assert sum(row.co2e for row in report.by_activity) == pytest.approx(
        report.total_co2e
    )


def test_compute_footprint_reports_the_flattened_reading_alongside(footprint):
    _, report, _ = footprint

    naive_total = sum(row.naive_co2e for row in report.by_object_type)
    assert naive_total > report.allocated_co2e

    shared = {row.object_type: row for row in report.by_object_type}
    # Every transport event is shared with a truck, so a truck-flattened log
    # charges the trucks far more than they can carry in a partition.
    assert shared["Truck"].naive_inflation is not None
    assert shared["Truck"].naive_inflation > 1.0


def test_compute_footprint_reports_coverage(footprint):
    _, report, _ = footprint

    assert set(report.diagnostics.activities_covered) == {
        "Transport",
        "Produce Item",
        "Pack Pallet",
    }
    # The log has activities no factor names, and the report says which.
    assert "Place Order" in report.diagnostics.activities_without_factor
    assert report.diagnostics.events_total > report.diagnostics.events_with_emissions


def test_compute_footprint_breaks_variants_down_when_asked(footprint):
    _, report, _ = footprint

    assert len(report.by_variant) >= 2, "the sample log has more than one order path"
    assert {row.object_type for row in report.by_variant} == {"Order"}
    for row in report.by_variant:
        assert row.objects >= 1
        assert row.mean_co2e == pytest.approx(row.total_co2e / row.objects)
    # A failed delivery means a second trip, which has to cost more.
    with_return = [row for row in report.by_variant if "Return Delivery" in row.activities]
    without = [row for row in report.by_variant if "Return Delivery" not in row.activities]
    assert with_return and without
    assert max(row.mean_co2e for row in with_return) > min(
        row.mean_co2e for row in without
    )


def test_compute_footprint_lists_the_heaviest_objects(footprint):
    _, report, hotspots = footprint

    assert hotspots.rows
    amounts = [row.allocated_co2e for row in hotspots.rows]
    assert amounts == sorted(amounts, reverse=True)
    assert all(row.object_type for row in hotspots.rows)
    assert isinstance(hotspots.visualize(), TableViz)


def test_compute_footprint_annotates_a_copy_of_the_log(footprint, sample_ocel):
    annotated, report, _ = footprint

    assert isinstance(annotated, OCEL)
    assert EVENT_CO2_ATTRIBUTE in annotated.events.df.columns
    assert OBJECT_CO2_ATTRIBUTE in annotated.objects.attribute_names

    assert annotated.events.df[EVENT_CO2_ATTRIBUTE].sum() == pytest.approx(
        report.total_co2e
    )
    assert annotated.objects.df[OBJECT_CO2_ATTRIBUTE].sum() == pytest.approx(
        report.allocated_co2e
    )

    # The input log is left as it was.
    assert EVENT_CO2_ATTRIBUTE not in sample_ocel.events.df.columns
    assert annotated.events.count == sample_ocel.events.count
    assert annotated.objects.count == sample_ocel.objects.count


def test_the_annotated_log_survives_a_round_trip_through_storage(footprint):
    annotated, report, _ = footprint

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "annotated.duckdb"
        annotated.to_duckdb(path)
        reloaded = OCEL.read_duckdb(path, read_only=True)

        assert reloaded.events.df[EVENT_CO2_ATTRIBUTE].astype(float).sum() == (
            pytest.approx(report.total_co2e)
        )
        assert reloaded.objects.df[OBJECT_CO2_ATTRIBUTE].astype(float).sum() == (
            pytest.approx(report.allocated_co2e)
        )
        # The quantity extension the emissions were derived from goes with it.
        assert reloaded.quantities.item_types == ["diesel_l", "electricity_kwh"]
        reloaded.close()


@pytest.mark.skipif(
    not ocel_file_io_available(),
    reason=(
        "the installed ocelescope calls r4pm bindings that the installed "
        "r4pm-full does not provide, so OCEL files cannot be written here"
    ),
)
def test_the_annotated_log_survives_a_round_trip_through_an_ocel_file(footprint):
    annotated, report, _ = footprint

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "annotated.jsonocel"
        annotated.write(path)
        reloaded = OCEL.read(path)

        assert reloaded.events.df[EVENT_CO2_ATTRIBUTE].astype(float).sum() == (
            pytest.approx(report.total_co2e)
        )
        assert reloaded.quantities.item_types == ["diesel_l", "electricity_kwh"]


def test_the_footprint_resource_survives_its_own_export(footprint):
    _, report, _ = footprint

    with tempfile.TemporaryDirectory() as directory:
        path = CarbonFootprint.write(report, Path(directory) / "footprint")
        reloaded = CarbonFootprint.read(path)

    assert reloaded.total_co2e == pytest.approx(report.total_co2e)
    assert len(reloaded.by_object_type) == len(report.by_object_type)
    assert isinstance(reloaded.visualize(), Plotly)


def test_the_footprint_figure_is_built_from_the_reported_numbers(footprint):
    _, report, _ = footprint
    figure = report.visualize().figure

    bar_traces = [trace for trace in figure.data if trace.type == "bar"]
    indicators = [trace for trace in figure.data if trace.type == "indicator"]
    assert bar_traces, "the report has emissions, so the figure has bars"
    assert indicators[0].value == pytest.approx(report.total_co2e)

    allocated = {row.object_type: row.allocated_co2e for row in report.by_object_type}
    series = next(trace for trace in bar_traces if trace.name == "Allocated (divided)")
    for object_type, value in zip(series.x, series.y, strict=True):
        assert value == pytest.approx(allocated[object_type])


def test_compare_allocation_isolates_the_modelling_choice(plugin, sample_ocel):
    comparison = plugin.compare_allocation(
        sample_ocel,
        ComparisonInput(
            factors=FACTORS,
            allocations=[
                AllocationConfig(rule="equal", label="equal split"),
                AllocationConfig(
                    rule="object_attribute",
                    weight_attribute="weight_kg",
                    label="by weight",
                ),
                AllocationConfig(
                    rule="item_quantity",
                    weight_item_type="diesel_l",
                    weight_measure="decrease",
                    label="by diesel",
                ),
            ],
        ),
    )

    assert comparison.rules == ["equal split", "by weight", "by diesel"]
    assert comparison.total_co2e > 0

    # Same emissions under every rule: only their distribution differs.
    for rule in comparison.rules:
        allocated = sum(
            cell.allocated_co2e for cell in comparison.cells if cell.rule == rule
        )
        assert allocated + comparison.unallocated_co2e[rule] == pytest.approx(
            comparison.total_co2e
        )

    trucks = {
        cell.rule: cell.allocated_co2e
        for cell in comparison.cells
        if cell.object_type == "Truck"
    }
    # Allocating by the diesel burned puts transport emissions on the vehicle
    # that burned it; an equal split spreads them over the cargo as well.
    assert trucks["by diesel"] > trucks["equal split"]

    # Pallets carry some of the footprint under every rule, by a factor that
    # says how much the rule alone moves them.
    assert comparison.spread["Pallet"] is not None
    assert comparison.spread["Pallet"] > 1.0
    # Trucks have no weight_kg in this log, so weighting by it gives them
    # nothing at all -- an unbounded difference, reported as no ratio.
    assert trucks["by weight"] == pytest.approx(0.0)
    assert comparison.spread["Truck"] is None

    assert isinstance(comparison.visualize(), Plotly)


def test_comparing_fewer_than_two_rules_is_refused(plugin, sample_ocel):
    with pytest.raises(ValueError, match="at least two"):
        plugin.compare_allocation(
            sample_ocel,
            ComparisonInput(
                factors=FACTORS, allocations=[AllocationConfig(rule="equal")]
            ),
        )


def test_a_footprint_without_any_factors_is_empty_but_valid(plugin, sample_ocel):
    _, report, hotspots = plugin.compute_footprint(
        sample_ocel, FootprintInput(factors=[])
    )

    assert report.total_co2e == 0.0
    assert report.allocated_co2e == 0.0
    assert hotspots.rows == []
    assert report.diagnostics.activities_without_factor
    assert isinstance(report.visualize(), Plotly)
