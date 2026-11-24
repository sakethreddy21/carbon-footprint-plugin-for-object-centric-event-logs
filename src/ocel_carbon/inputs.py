"""Plugin inputs.

Ocelescope generates the whole configuration form from these models, so the
field metadata here is the user interface: titles, descriptions, bounds and the
OCEL-aware pickers that offer the activities, attributes and object types of the
log the user selected.
"""

from typing import Any, Literal

from ocelescope import OCEL_FIELD, PluginInput
from pydantic import BaseModel, Field, model_validator

#: How the magnitude of a quantity change is read off a quantity operation.
#:
#: A quantity operation is signed: it is the change an event makes to an
#: object's item level. Emissions usually scale with the size of the flow
#: regardless of direction (``absolute``), but a log may record, say, fuel
#: drawn from a tank as a decrease, in which case only decreases count.
QuantityMeasure = Literal["absolute", "increase", "decrease", "signed"]

EmissionBasis = Literal["per_event", "per_event_attribute", "per_item_quantity"]
AllocationRule = Literal["equal", "object_attribute", "item_quantity"]


def ocel_select(
    *,
    field_type: str,
    ocel_id: str = "ocel",
    default: Any = ...,
    title: str | None = None,
    description: str | None = None,
) -> Any:
    """An OCEL-aware picker that may be optional.

    ``ocelescope.OCEL_FIELD`` always produces a required field, so a form built
    from it cannot be submitted with the picker left empty. This re-declares the
    field with a default while taking the UI metadata from ``OCEL_FIELD``
    itself, so the part that tells Ocelescope which pickers to render is the
    installed version's rather than a copy here that could drift from it.
    ``tests/test_inputs.py`` pins both halves of that.
    """
    template = OCEL_FIELD(
        field_type=field_type,  # type: ignore[arg-type]
        ocel_id=ocel_id,
        title=title,
        description=description,
    )
    return Field(
        default=default,
        title=title,
        description=description,
        json_schema_extra=template.json_schema_extra,
    )


class EmissionFactor(BaseModel):
    """One rule turning events of a single activity into emissions.

    Several rules may name the same activity; their contributions add up, so a
    transport activity can carry a fixed handling emission plus a per-kilometre
    one.
    """

    activity: str = OCEL_FIELD(
        field_type="event_type",
        ocel_id="ocel",
        title="Activity",
        description="The activity whose events this rule applies to.",
    )
    basis: EmissionBasis = Field(
        default="per_event",
        title="Basis",
        description=(
            "What the factor is multiplied by: the event itself (a flat amount "
            "per occurrence), a numeric event attribute (such as a distance or "
            "an energy reading), or the quantity an event moves of one item type "
            "in the log's quantity extension."
        ),
    )
    factor: float = Field(
        default=0.0,
        title="Emission factor",
        description="Emissions per unit of the chosen basis, in the report's unit.",
    )
    event_attribute: str | None = ocel_select(
        field_type="event_attribute",
        default=None,
        title="Event attribute",
        description="Required for the 'per_event_attribute' basis.",
    )
    item_type: str | None = Field(
        default=None,
        title="Item type",
        description=(
            "Required for the 'per_item_quantity' basis: the item type whose "
            "quantity operations drive the emissions."
        ),
    )
    quantity_measure: QuantityMeasure = Field(
        default="absolute",
        title="Quantity measure",
        description=(
            "How to read a signed quantity operation: its magnitude (absolute), "
            "only increases, only decreases, or the signed value itself."
        ),
    )
    note: str | None = Field(
        default=None,
        title="Note",
        description="Free text, carried into the report: a source or a unit.",
    )

    @model_validator(mode="after")
    def _check_basis(self) -> "EmissionFactor":
        if self.basis == "per_event_attribute" and not self.event_attribute:
            raise ValueError(
                f"the rule for '{self.activity}' uses the per_event_attribute "
                "basis, so it needs an event attribute"
            )
        if self.basis == "per_item_quantity" and not self.item_type:
            raise ValueError(
                f"the rule for '{self.activity}' uses the per_item_quantity "
                "basis, so it needs an item type"
            )
        return self

    def describe(self) -> str:
        """The rule in one line, as the report shows it."""
        if self.basis == "per_event":
            unit = "event"
        elif self.basis == "per_event_attribute":
            unit = f"unit of {self.event_attribute}"
        else:
            unit = f"unit of {self.item_type} ({self.quantity_measure})"
        text = f"{self.factor:g} per {unit}"
        return f"{text} — {self.note}" if self.note else text


class AllocationConfig(BaseModel):
    """How an event's emissions are split across the objects it involves.

    This is the object-centric part of the computation, and the part that is a
    modelling choice rather than a fact: the same log and the same factors give
    a different footprint per object type under a different rule. ``compare_allocation``
    exists to show how much that choice moves the result.
    """

    rule: AllocationRule = Field(
        default="equal",
        title="Allocation rule",
        description=(
            "equal: every eligible object of an event receives the same share. "
            "object_attribute: shares are proportional to a numeric object "
            "attribute, such as a weight or a volume. item_quantity: shares are "
            "proportional to the quantity of one item type the event moves for "
            "each object."
        ),
    )
    object_types: list[str] = ocel_select(
        field_type="object_type",
        default=[],
        title="Eligible object types",
        description=(
            "Restrict allocation to these object types. Empty means every object "
            "type. Emissions of an event that involves none of them are reported "
            "as unallocated rather than dropped."
        ),
    )
    qualifiers: list[str] = ocel_select(
        field_type="e2o_qualifier",
        default=[],
        title="Eligible qualifiers",
        description=(
            "Restrict allocation to event-to-object relations with these "
            "qualifiers. Empty means every qualifier."
        ),
    )
    weight_attribute: str | None = ocel_select(
        field_type="object_attribute",
        default=None,
        title="Weight attribute",
        description="Required for the 'object_attribute' rule.",
    )
    weight_item_type: str | None = Field(
        default=None,
        title="Weight item type",
        description="Required for the 'item_quantity' rule.",
    )
    weight_measure: QuantityMeasure = Field(
        default="absolute",
        title="Weight measure",
        description="How to read the signed quantity operations used as weights.",
    )
    on_zero_weight: Literal["equal", "unallocated"] = Field(
        default="equal",
        title="When every weight is zero",
        description=(
            "An event whose eligible objects all carry weight zero — no recorded "
            "attribute, no quantity operation — is either split equally among "
            "them or left unallocated and reported as such."
        ),
    )
    label: str | None = Field(
        default=None,
        title="Label",
        description="Name for this rule in the comparison chart.",
    )

    @model_validator(mode="after")
    def _check_rule(self) -> "AllocationConfig":
        if self.rule == "object_attribute" and not self.weight_attribute:
            raise ValueError(
                "the object_attribute rule needs a weight attribute to be proportional to"
            )
        if self.rule == "item_quantity" and not self.weight_item_type:
            raise ValueError(
                "the item_quantity rule needs a weight item type to be proportional to"
            )
        return self

    def describe(self) -> str:
        """The rule in one line, as the report and the comparison chart show it."""
        if self.label:
            return self.label
        if self.rule == "equal":
            text = "equal split"
        elif self.rule == "object_attribute":
            text = f"by {self.weight_attribute}"
        else:
            text = f"by {self.weight_item_type} quantity"
        if self.object_types:
            text += f" over {', '.join(sorted(self.object_types))}"
        if self.qualifiers:
            text += f" via {', '.join(sorted(self.qualifiers))}"
        return text


class FactorConfig(PluginInput):
    """The emission factors, shared by every method that needs them."""

    factors: list[EmissionFactor] = Field(
        default_factory=list,
        title="Emission factors",
        description=(
            "One rule per activity, or several per activity to add contributions "
            "up. Run 'Inspect log' first: it lists every activity with the "
            "attributes and item types it offers."
        ),
    )
    default_factor: float = Field(
        default=0.0,
        ge=0.0,
        title="Default factor",
        description=(
            "Emissions charged to each event of an activity that no rule names. "
            "Zero leaves those activities out of the footprint; the report counts "
            "them either way."
        ),
    )
    unit: str = Field(
        default="kg CO2e",
        title="Unit",
        description="The unit the factors are given in. Labels only — nothing is converted.",
    )


class FootprintInput(FactorConfig):
    """Configuration of the main footprint computation."""

    allocation: AllocationConfig = Field(
        default_factory=AllocationConfig,
        title="Allocation",
        description="How each event's emissions are split across its objects.",
    )
    variant_object_type: str | None = ocel_select(
        field_type="object_type",
        default=None,
        title="Variant breakdown for object type",
        description=(
            "Also break the footprint down by the process variants of this object "
            "type — the distinct activity sequences its objects go through."
        ),
    )
    top_objects: int = Field(
        default=25,
        ge=0,
        le=1000,
        title="Objects in the detail table",
        description="How many of the heaviest objects to list. Zero lists none.",
    )
    max_variants: int = Field(
        default=20,
        ge=1,
        le=200,
        title="Variants in the breakdown",
        description="How many of the heaviest variants to report.",
    )


class ComparisonInput(FactorConfig):
    """Configuration of the allocation comparison."""

    allocations: list[AllocationConfig] = Field(
        default_factory=lambda: [
            AllocationConfig(label="equal split"),
        ],
        title="Allocation rules to compare",
        description=(
            "Two or more rules. Each is applied to the same emissions, so the "
            "chart shows only what the allocation choice itself changes."
        ),
    )
