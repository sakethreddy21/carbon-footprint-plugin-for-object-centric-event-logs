"""A small OCEL 2.0 logistics log with the quantity extension, for demos and tests.

Nothing here is part of the plugin: it only produces a log whose shape exercises
every emission basis and every allocation rule --

* events shared by several objects of several types (so allocation matters),
* a numeric event attribute (``distance_km``) to multiply a factor by,
* quantity operations on fuel and electricity (so emissions can come from the
  material flow itself rather than from a per-event estimate),
* a numeric object attribute (``weight_kg``) to allocate proportionally to.
"""

from __future__ import annotations

import pandas as pd

OBJECT_TYPES = ("Order", "Item", "Pallet", "Truck", "Machine")

ITEM_TYPE_DIESEL = "diesel_l"
ITEM_TYPE_ELECTRICITY = "electricity_kwh"

START = pd.Timestamp("2026-03-02 06:00:00", tz="UTC")


def build_frames(
    orders: int = 24,
    items_per_order: int = 2,
    orders_per_pallet: int = 4,
    pallets_per_trip: int = 2,
) -> dict[str, pd.DataFrame]:
    """The event, object, relation and quantity tables of the sample log."""
    events: list[dict[str, object]] = []
    objects: list[dict[str, object]] = []
    relations: list[dict[str, object]] = []
    o2o: list[dict[str, object]] = []
    operations: list[dict[str, object]] = []

    clock = START
    event_number = 0

    def add_event(activity: str, **attributes: object) -> str:
        nonlocal clock, event_number
        event_number += 1
        event_id = f"e{event_number:04d}"
        clock = clock + pd.Timedelta(minutes=17)
        events.append(
            {
                "ocel:eid": event_id,
                "ocel:timestamp": clock,
                "ocel:activity": activity,
                **attributes,
            }
        )
        return event_id

    def relate(event_id: str, object_id: str, qualifier: str) -> None:
        relations.append(
            {
                "ocel:eid": event_id,
                "ocel:oid": object_id,
                "ocel:qualifier": qualifier,
            }
        )

    machines = [f"machine-{index + 1}" for index in range(2)]
    for machine in machines:
        objects.append({"ocel:oid": machine, "ocel:type": "Machine"})

    trucks = [f"truck-{index + 1}" for index in range(2)]
    for index, truck in enumerate(trucks):
        objects.append(
            {"ocel:oid": truck, "ocel:type": "Truck", "payload_capacity_kg": 12000}
        )

    pallet_count = 0
    pending_pallets: list[tuple[str, list[str], list[str]]] = []

    order_ids = [f"order-{index + 1:03d}" for index in range(orders)]
    batches = [
        order_ids[start : start + orders_per_pallet]
        for start in range(0, len(order_ids), orders_per_pallet)
    ]

    for batch_index, batch in enumerate(batches):
        batch_items: list[str] = []
        for order_index, order in enumerate(batch):
            objects.append(
                {"ocel:oid": order, "ocel:type": "Order", "priority": order_index + 1}
            )
            placed = add_event("Place Order")
            relate(placed, order, "order")

            for item_index in range(items_per_order):
                item = f"{order}-item-{item_index + 1}"
                # Heavier items in later batches, so a weight-based allocation
                # differs from an equal split in a way a test can check.
                weight = 4.0 + 2.0 * item_index + 3.0 * (batch_index % 3)
                objects.append(
                    {"ocel:oid": item, "ocel:type": "Item", "weight_kg": weight}
                )
                o2o.append(
                    {
                        "ocel:oid_1": order,
                        "ocel:oid_2": item,
                        "ocel:qualifier": "contains",
                    }
                )

                machine = machines[(batch_index + item_index) % len(machines)]
                produced = add_event("Produce Item")
                relate(produced, item, "produced")
                relate(produced, machine, "resource")
                relate(produced, order, "order")
                operations.append(
                    {
                        "ocel:eid": produced,
                        "ocel:oid": machine,
                        "qel:item_type": ITEM_TYPE_ELECTRICITY,
                        # Drawn from the grid: a decrease in what is available.
                        "qel:quantity": -(1.5 + 0.5 * item_index + weight * 0.1),
                    }
                )
                batch_items.append(item)

        pallet_count += 1
        pallet = f"pallet-{pallet_count:03d}"
        objects.append(
            {
                "ocel:oid": pallet,
                "ocel:type": "Pallet",
                "weight_kg": 25.0 + 2.5 * len(batch_items),
            }
        )
        packed = add_event("Pack Pallet")
        relate(packed, pallet, "pallet")
        for item in batch_items:
            relate(packed, item, "item")
            o2o.append(
                {"ocel:oid_1": pallet, "ocel:oid_2": item, "ocel:qualifier": "holds"}
            )
        for order in batch:
            relate(packed, order, "order")

        pending_pallets.append((pallet, batch, batch_items))

        if len(pending_pallets) == pallets_per_trip:
            truck = trucks[(batch_index // pallets_per_trip) % len(trucks)]
            distance = 120.0 + 45.0 * (batch_index % 4)

            for pallet_id, _, _ in pending_pallets:
                loaded = add_event("Load Truck")
                relate(loaded, pallet_id, "pallet")
                relate(loaded, truck, "truck")

            # One transport event for the whole trip: shared by the truck, both
            # pallets, every order on board and nothing else. This is the event
            # that has no single owner.
            transport = add_event("Transport", distance_km=distance)
            relate(transport, truck, "truck")
            for pallet_id, pallet_orders, _ in pending_pallets:
                relate(transport, pallet_id, "pallet")
                for order in pallet_orders:
                    relate(transport, order, "order")
            operations.append(
                {
                    "ocel:eid": transport,
                    "ocel:oid": truck,
                    "qel:item_type": ITEM_TYPE_DIESEL,
                    # Burned on the way: the tank level falls.
                    "qel:quantity": -(distance * 0.28),
                }
            )

            for pallet_id, pallet_orders, _ in pending_pallets:
                unloaded = add_event("Unload")
                relate(unloaded, pallet_id, "pallet")
                relate(unloaded, truck, "truck")
                for order_index, order in enumerate(pallet_orders):
                    delivered = add_event("Deliver")
                    relate(delivered, order, "order")
                    relate(delivered, pallet_id, "pallet")

                    # Some deliveries fail and go round again, so the orders of
                    # this log do not all follow one path: the variant
                    # breakdown has something to compare.
                    if (batch_index + order_index) % 5 == 0:
                        returned = add_event("Return Delivery", distance_km=18.0)
                        relate(returned, order, "order")
                        relate(returned, pallet_id, "pallet")
                        relate(returned, truck, "truck")
                        operations.append(
                            {
                                "ocel:eid": returned,
                                "ocel:oid": truck,
                                "qel:item_type": ITEM_TYPE_DIESEL,
                                "qel:quantity": -(18.0 * 0.28),
                            }
                        )
                        redelivered = add_event("Deliver")
                        relate(redelivered, order, "order")
                        relate(redelivered, pallet_id, "pallet")

            pending_pallets = []

    initial_quantities = [
        {"ocel:oid": truck, "qel:item_type": ITEM_TYPE_DIESEL, "qel:quantity": 300.0}
        for truck in trucks
    ] + [
        {
            "ocel:oid": machine,
            "qel:item_type": ITEM_TYPE_ELECTRICITY,
            "qel:quantity": 0.0,
        }
        for machine in machines
    ]

    event_frame = pd.DataFrame(events)
    relation_frame = pd.DataFrame(relations)
    # Ocelescope stores the activity and the object type on the relation table too.
    relation_frame = relation_frame.merge(
        event_frame[["ocel:eid", "ocel:activity"]], on="ocel:eid", how="left"
    ).merge(
        pd.DataFrame(objects)[["ocel:oid", "ocel:type"]], on="ocel:oid", how="left"
    )

    return {
        "events": event_frame,
        "objects": pd.DataFrame(objects),
        "relations": relation_frame,
        "o2o": pd.DataFrame(o2o),
        "oqty": pd.DataFrame(initial_quantities),
        "qop": pd.DataFrame(operations),
        "properties": pd.DataFrame(
            {"qel:item_type": [ITEM_TYPE_DIESEL, ITEM_TYPE_ELECTRICITY]}
        ),
    }


def build_ocel(**kwargs: int):
    """The sample log as an ``ocelescope.OCEL``, quantity extension included."""
    from ocelescope import OCEL

    frames = build_frames(**kwargs)
    return OCEL.from_frames(
        events=frames["events"],
        objects=frames["objects"],
        relations=frames["relations"],
        o2o=frames["o2o"],
        quantityExtension=(frames["oqty"], frames["qop"], frames["properties"]),
    )


def _attribute_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    return "string"


def _as_text(value: object) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)


EPOCH = "1970-01-01T00:00:00Z"


def write_ocel2_json(path, frames: dict[str, pd.DataFrame] | None = None) -> None:
    """Write the sample log as an OCEL 2.0 JSON file.

    Ocelescope's own exporter is not used here: the released ``ocelescope``
    calls r4pm bindings (``stream_ocel_to_duckdb``,
    ``read_consolidated_ocel_from_duckdb``) that the released ``r4pm-full``
    0.7.1 no longer has, so reading and writing OCEL *files* through the Python
    package raises ``AttributeError`` until those versions line up again. This
    writer only needs pandas, and the quantity tables go in as the top-level
    extras the current format expects -- keys and column names as Ocelescope
    stores them -- so the log imports with its quantities intact.
    """
    import json
    from pathlib import Path

    frames = frames if frames is not None else build_frames()
    events, objects = frames["events"], frames["objects"]
    relations, o2o = frames["relations"], frames["o2o"]

    event_attributes = [
        column
        for column in events.columns
        if column not in {"ocel:eid", "ocel:timestamp", "ocel:activity"}
    ]
    object_attributes = [
        column for column in objects.columns if column not in {"ocel:oid", "ocel:type"}
    ]

    def declared(frame: pd.DataFrame, group: str, columns: list[str]) -> list[dict]:
        declarations = []
        for name, rows in frame.groupby(group):
            present = [
                {
                    "name": column,
                    "type": _attribute_type(rows[column].dropna().iloc[0]),
                }
                for column in columns
                if rows[column].notna().any()
            ]
            declarations.append({"name": str(name), "attributes": present})
        return declarations

    relations_by_event: dict[str, list[dict]] = {}
    for event_id, object_id, qualifier in zip(
        relations["ocel:eid"],
        relations["ocel:oid"],
        relations["ocel:qualifier"],
        strict=True,
    ):
        relations_by_event.setdefault(event_id, []).append(
            {"objectId": object_id, "qualifier": qualifier}
        )

    relations_by_object: dict[str, list[dict]] = {}
    for source, target, qualifier in zip(
        o2o["ocel:oid_1"], o2o["ocel:oid_2"], o2o["ocel:qualifier"], strict=True
    ):
        relations_by_object.setdefault(source, []).append(
            {"objectId": target, "qualifier": qualifier}
        )

    log = {
        "objectTypes": declared(objects, "ocel:type", object_attributes),
        "eventTypes": declared(events, "ocel:activity", event_attributes),
        "objects": [
            {
                "id": row["ocel:oid"],
                "type": row["ocel:type"],
                "attributes": [
                    {
                        "name": column,
                        "time": EPOCH,
                        "value": _as_text(row[column]),
                    }
                    for column in object_attributes
                    if pd.notna(row[column])
                ],
                "relationships": relations_by_object.get(row["ocel:oid"], []),
            }
            for _, row in objects.iterrows()
        ],
        "events": [
            {
                "id": row["ocel:eid"],
                "type": row["ocel:activity"],
                "time": pd.Timestamp(row["ocel:timestamp"])
                .tz_convert("UTC")
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "attributes": [
                    {"name": column, "value": _as_text(row[column])}
                    for column in event_attributes
                    if pd.notna(row[column])
                ],
                "relationships": relations_by_event.get(row["ocel:eid"], []),
            }
            for _, row in events.iterrows()
        ],
        # The quantity extension, as Ocelescope's current format carries it.
        "quantities": frames["oqty"].to_dict(orient="records"),
        "quantity_operations": frames["qop"].to_dict(orient="records"),
        "quantity_item_properties": frames["properties"].to_dict(orient="records"),
    }

    Path(path).write_text(json.dumps(log, indent=1))
