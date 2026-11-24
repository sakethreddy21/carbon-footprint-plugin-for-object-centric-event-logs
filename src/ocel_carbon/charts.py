"""Figures for the resources this plugin returns.

The palette is the validated categorical set (blue / orange / aqua / yellow),
assigned in fixed order and never cycled; magnitude-only charts use the single
blue. Backgrounds are transparent so the figures sit on whatever surface
Ocelescope renders them on, and text is one mid grey that stays legible on a
light and a dark surface alike -- the frontend's theme is not knowable from
here. Every bar carries its value as a direct label, which is also what the
light-surface contrast of the lighter hues requires.
"""

from __future__ import annotations

from collections.abc import Sequence

import plotly.graph_objects as go
from plotly.subplots import make_subplots

#: Categorical slots, in fixed order (validated for CVD separation).
SERIES = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300")
#: Single hue for charts whose job is magnitude rather than identity.
MAGNITUDE = "#2a78d6"
TEXT = "#6b7280"
GRID = "rgba(128,128,128,0.18)"
TRANSPARENT = "rgba(0,0,0,0)"


def format_amount(value: float) -> str:
    """A value as a short label: readable, never more precision than it has."""
    magnitude = abs(value)
    if magnitude >= 1000:
        return f"{value:,.0f}"
    if magnitude >= 10:
        return f"{value:,.1f}"
    if magnitude >= 0.01:
        return f"{value:,.2f}"
    if magnitude == 0:
        return "0"
    return f"{value:,.3g}"


def truncate(text: str, limit: int = 34) -> str:
    """A label short enough for an axis tick."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def style(figure: go.Figure, height: int, margin_top: int = 48) -> go.Figure:
    """Shared chrome: transparent surface, recessive grid, one text colour."""
    figure.update_layout(
        height=height,
        paper_bgcolor=TRANSPARENT,
        plot_bgcolor=TRANSPARENT,
        font={"color": TEXT, "size": 12},
        margin={"l": 8, "r": 16, "t": margin_top, "b": 8},
        bargap=0.28,
        bargroupgap=0.12,
        hoverlabel={"font": {"size": 12}},
    )
    figure.update_xaxes(
        gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, title_font={"size": 11}
    )
    figure.update_yaxes(
        gridcolor=GRID, zerolinecolor=GRID, linecolor=GRID, title_font={"size": 11}
    )
    for annotation in figure.layout.annotations or ():
        # Subplot titles: a shade stronger than the body text, same hue.
        annotation.font = {"size": 13, "color": TEXT}
    return figure


def _horizontal_bars(
    labels: Sequence[str],
    values: Sequence[float],
    unit: str,
    name: str,
    hovers: Sequence[str] | None = None,
) -> go.Bar:
    """A magnitude bar chart, largest at the top, each bar labelled.

    ``hovers`` carries what the axis tick cannot: a variant's activity sequence
    behind a label that only has room for its size.
    """
    return go.Bar(
        x=list(values),
        y=[truncate(label) for label in labels],
        orientation="h",
        marker={"color": MAGNITUDE, "cornerradius": 4},
        text=[format_amount(value) for value in values],
        textposition="outside",
        textfont={"size": 11, "color": TEXT},
        cliponaxis=False,
        name=name,
        customdata=list(hovers if hovers is not None else labels),
        hovertemplate=f"%{{customdata}}<br>%{{x:,.3f}} {unit}<extra></extra>",
        showlegend=False,
    )


def footprint_figure(
    *,
    unit: str,
    total_co2e: float,
    allocated_co2e: float,
    unallocated_co2e: float,
    activities: Sequence[tuple[str, float]],
    object_types: Sequence[tuple[str, float, float]],
    detail_title: str,
    detail: Sequence[tuple[str, float, str]],
) -> go.Figure:
    """The footprint at a glance: where emissions arise, and who carries them.

    ``object_types`` carries the allocated and the naive amount per type, shown
    side by side because the gap between them is the double counting that a
    case-centric reading of the same log would have reported as fact.
    """
    figure = make_subplots(
        rows=2,
        cols=2,
        specs=[
            [{"type": "xy"}, {"type": "xy"}],
            [{"type": "xy"}, {"type": "indicator"}],
        ],
        subplot_titles=(
            "Emissions by activity",
            "Per object type: allocated vs. counted whole",
            detail_title,
            "",
        ),
        horizontal_spacing=0.16,
        vertical_spacing=0.18,
    )

    if activities:
        labels, values = zip(*activities, strict=True)
        figure.add_trace(
            _horizontal_bars(labels, values, unit, "Emissions"), row=1, col=1
        )
        figure.update_xaxes(title_text=unit, row=1, col=1)

    if object_types:
        types = [truncate(name) for name, _, _ in object_types]
        allocated = [value for _, value, _ in object_types]
        naive = [value for _, _, value in object_types]
        figure.add_trace(
            go.Bar(
                x=types,
                y=allocated,
                name="Allocated (divided)",
                marker={"color": SERIES[0], "cornerradius": 4},
                text=[format_amount(value) for value in allocated],
                textposition="outside",
                textfont={"size": 10, "color": TEXT},
                cliponaxis=False,
                hovertemplate=f"%{{x}}<br>allocated %{{y:,.3f}} {unit}<extra></extra>",
            ),
            row=1,
            col=2,
        )
        figure.add_trace(
            go.Bar(
                x=types,
                y=naive,
                name="Counted whole (double counts)",
                marker={"color": SERIES[1], "cornerradius": 4},
                text=[format_amount(value) for value in naive],
                textposition="outside",
                textfont={"size": 10, "color": TEXT},
                cliponaxis=False,
                hovertemplate=f"%{{x}}<br>counted whole %{{y:,.3f}} {unit}<extra></extra>",
            ),
            row=1,
            col=2,
        )
        figure.update_yaxes(title_text=unit, row=1, col=2)

    if detail:
        labels, values, hovers = zip(*detail, strict=True)
        figure.add_trace(
            _horizontal_bars(labels, values, unit, "Allocated", hovers),
            row=2,
            col=1,
        )
        figure.update_xaxes(title_text=unit, row=2, col=1)

    allocated_share = (allocated_co2e / total_co2e * 100) if total_co2e else 0.0
    figure.add_trace(
        go.Indicator(
            mode="number",
            value=total_co2e,
            number={
                "valueformat": ",.4~s",
                "suffix": f" {unit}",
                "font": {"size": 34, "color": TEXT},
            },
            title={
                "text": (
                    "Total emissions in the log<br>"
                    f"<span style='font-size:11px'>{allocated_share:.1f}% allocated"
                    + (
                        f" · {format_amount(unallocated_co2e)} {unit} unallocated"
                        if unallocated_co2e
                        else ""
                    )
                    + "</span>"
                ),
                "font": {"size": 13, "color": TEXT},
            },
        ),
        row=2,
        col=2,
    )

    figure.update_layout(
        barmode="group",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.06,
            "x": 0.55,
            "font": {"size": 11},
        },
    )
    return style(figure, height=720)


def comparison_figure(
    *,
    unit: str,
    object_types: Sequence[str],
    rules: Sequence[str],
    values: dict[str, dict[str, float]],
    unallocated: dict[str, float],
) -> go.Figure:
    """One group of bars per object type, one bar per allocation rule.

    The emissions are identical across the groups, so every difference on this
    chart is the allocation choice and nothing else.
    """
    figure = go.Figure()
    for index, rule in enumerate(rules):
        amounts = [values.get(rule, {}).get(otype, 0.0) for otype in object_types]
        figure.add_trace(
            go.Bar(
                x=[truncate(otype, 22) for otype in object_types],
                y=amounts,
                name=truncate(rule, 40),
                marker={"color": SERIES[index % len(SERIES)], "cornerradius": 4},
                text=[format_amount(amount) for amount in amounts],
                textposition="outside",
                textfont={"size": 10, "color": TEXT},
                cliponaxis=False,
                hovertemplate=(
                    f"%{{x}}<br>{truncate(rule, 40)}<br>"
                    f"%{{y:,.3f}} {unit}<extra></extra>"
                ),
            )
        )

    unallocated_note = ", ".join(
        f"{rule}: {format_amount(amount)}"
        for rule, amount in unallocated.items()
        if amount
    )
    figure.update_layout(
        barmode="group",
        title={
            "text": "Allocated emissions per object type, by allocation rule"
            + (
                f"<br><span style='font-size:11px'>unallocated — {unallocated_note}</span>"
                if unallocated_note
                else ""
            ),
            "font": {"size": 14, "color": TEXT},
            "x": 0.01,
        },
        yaxis_title=unit,
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "x": 0,
            "font": {"size": 11},
        },
    )
    return style(figure, height=480, margin_top=104)
