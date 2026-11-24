"""Render the plugin's figures to standalone HTML, without running Ocelescope.

    python examples/render_demo.py [out_dir]

Useful for a screenshot, and for checking the figures while iterating on them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ocel_carbon import CarbonFootprintPlugin  # noqa: E402
from ocel_carbon.inputs import (  # noqa: E402
    AllocationConfig,
    ComparisonInput,
    EmissionFactor,
    FootprintInput,
)
from sample_log import build_ocel  # noqa: E402

FACTORS = [
    EmissionFactor(
        activity="Transport",
        basis="per_item_quantity",
        factor=2.68,
        item_type="diesel_l",
        quantity_measure="decrease",
        note="diesel combustion, 2.68 kg CO2e/l",
    ),
    EmissionFactor(
        activity="Return Delivery",
        basis="per_item_quantity",
        factor=2.68,
        item_type="diesel_l",
        quantity_measure="decrease",
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


def main() -> None:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
    out_dir.mkdir(parents=True, exist_ok=True)

    ocel = build_ocel()
    plugin = CarbonFootprintPlugin()

    _, footprint, hotspots = plugin.compute_footprint(
        ocel,
        FootprintInput(
            factors=FACTORS,
            allocation=AllocationConfig(rule="equal"),
            variant_object_type="Order",
        ),
    )
    comparison = plugin.compare_allocation(
        ocel,
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
                    label="by diesel burned",
                ),
            ],
        ),
    )

    for name, resource in (
        ("footprint", footprint),
        ("allocation_comparison", comparison),
    ):
        path = out_dir / f"demo_{name}.html"
        resource.visualize().figure.write_html(path, include_plotlyjs="cdn")
        print(f"wrote {path}")

    print(
        f"\n{footprint.total_co2e:.1f} {footprint.unit} over "
        f"{footprint.diagnostics.events_total} events"
    )
    for row in footprint.by_object_type:
        inflation = (
            f"{row.naive_inflation:.1f}x" if row.naive_inflation else "—"
        )
        print(
            f"  {row.object_type:9} allocated {row.allocated_co2e:8.2f}"
            f"   flattened {row.naive_co2e:8.2f}   overstated {inflation}"
        )
    print(f"\n{len(hotspots.rows)} hotspot objects, heaviest:")
    for row in hotspots.rows[:3]:
        print(f"  {row.object_id:20} {row.allocated_co2e:7.2f} ({row.object_type})")


if __name__ == "__main__":
    main()
