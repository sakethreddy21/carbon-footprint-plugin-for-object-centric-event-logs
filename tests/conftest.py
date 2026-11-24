"""Test fixtures: the sample log, and a tiny hand-built log with known numbers."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest
from ocelescope import OCEL

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples"))

from sample_log import build_ocel  # noqa: E402


@pytest.fixture(scope="module")
def sample_ocel() -> OCEL:
    """The example logistics log, quantity extension included."""
    return build_ocel()


@pytest.fixture
def tiny_ocel() -> OCEL:
    """Three events whose emissions and shares can be worked out by hand.

    * ``e1`` (Transport, 100 km) involves a truck and two orders;
    * ``e2`` (Produce) involves one machine and one item, and draws 10 kWh;
    * ``e3`` (Inspect) involves one item only and no rule names it.

    The items carry a weight, the orders do not, which is what makes the
    attribute-weighted rule fall back on some events and not others.
    """
    events = pd.DataFrame(
        [
            {
                "ocel:eid": "e1",
                "ocel:timestamp": pd.Timestamp("2026-01-01 08:00", tz="UTC"),
                "ocel:activity": "Transport",
                "distance_km": 100.0,
            },
            {
                "ocel:eid": "e2",
                "ocel:timestamp": pd.Timestamp("2026-01-01 09:00", tz="UTC"),
                "ocel:activity": "Produce",
                "distance_km": None,
            },
            {
                "ocel:eid": "e3",
                "ocel:timestamp": pd.Timestamp("2026-01-01 10:00", tz="UTC"),
                "ocel:activity": "Inspect",
                "distance_km": None,
            },
        ]
    )
    objects = pd.DataFrame(
        [
            {"ocel:oid": "truck", "ocel:type": "Truck", "weight_kg": None},
            {"ocel:oid": "order-1", "ocel:type": "Order", "weight_kg": None},
            {"ocel:oid": "order-2", "ocel:type": "Order", "weight_kg": None},
            {"ocel:oid": "machine", "ocel:type": "Machine", "weight_kg": None},
            {"ocel:oid": "item-1", "ocel:type": "Item", "weight_kg": 3.0},
            {"ocel:oid": "item-2", "ocel:type": "Item", "weight_kg": 1.0},
        ]
    )
    relations = pd.DataFrame(
        [
            {"ocel:eid": "e1", "ocel:oid": "truck", "ocel:qualifier": "truck"},
            {"ocel:eid": "e1", "ocel:oid": "order-1", "ocel:qualifier": "order"},
            {"ocel:eid": "e1", "ocel:oid": "order-2", "ocel:qualifier": "order"},
            {"ocel:eid": "e2", "ocel:oid": "machine", "ocel:qualifier": "resource"},
            {"ocel:eid": "e2", "ocel:oid": "item-1", "ocel:qualifier": "produced"},
            {"ocel:eid": "e2", "ocel:oid": "item-2", "ocel:qualifier": "produced"},
            {"ocel:eid": "e3", "ocel:oid": "item-1", "ocel:qualifier": "inspected"},
        ]
    )
    relations = relations.merge(
        events[["ocel:eid", "ocel:activity"]], on="ocel:eid", how="left"
    ).merge(objects[["ocel:oid", "ocel:type"]], on="ocel:oid", how="left")

    quantities = (
        pd.DataFrame(
            [
                {
                    "ocel:oid": "truck",
                    "qel:item_type": "diesel_l",
                    "qel:quantity": 200.0,
                }
            ]
        ),
        pd.DataFrame(
            [
                {
                    "ocel:eid": "e1",
                    "ocel:oid": "truck",
                    "qel:item_type": "diesel_l",
                    "qel:quantity": -25.0,
                },
                {
                    "ocel:eid": "e2",
                    "ocel:oid": "machine",
                    "qel:item_type": "electricity_kwh",
                    "qel:quantity": -10.0,
                },
            ]
        ),
        pd.DataFrame({"qel:item_type": ["diesel_l", "electricity_kwh"]}),
    )

    return OCEL.from_frames(
        events=events,
        objects=objects,
        relations=relations,
        quantityExtension=quantities,
    )
