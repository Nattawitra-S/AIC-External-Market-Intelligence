"""
charts.py
==========
Reusable Plotly chart builders, all using the shared AIC theme (modules/theme.py).
Every function takes a tidy DataFrame and returns a plotly Figure -- no
Streamlit calls in here, so these are unit-testable in isolation.
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from modules.theme import (
    ACCENT_NEGATIVE,
    ACCENT_NEUTRAL,
    ACCENT_POSITIVE,
    CATEGORICAL_SEQUENCE,
    PLOTLY_TEMPLATE,
    PRIMARY,
    SECONDARY,
)


def line_trend(df: pd.DataFrame, x: str, y: str, color: str | None = None,
                title: str = "", y_title: str = "") -> go.Figure:
    fig = px.line(
        df, x=x, y=y, color=color, markers=True,
        template=PLOTLY_TEMPLATE, title=title,
        color_discrete_sequence=CATEGORICAL_SEQUENCE,
    )
    fig.update_layout(yaxis_title=y_title or y, xaxis_title="", hovermode="x unified")
    if color is None:
        fig.update_traces(line_color=PRIMARY)
    return fig


def ranked_bar(df: pd.DataFrame, x: str, y: str, title: str = "",
               color: str | None = None, orientation: str = "h") -> go.Figure:
    fig = px.bar(
        df, x=x if orientation == "h" else y, y=y if orientation == "h" else x,
        orientation=orientation, template=PLOTLY_TEMPLATE, title=title,
        color=color, color_discrete_sequence=CATEGORICAL_SEQUENCE,
    )
    if color is None:
        fig.update_traces(marker_color=SECONDARY)
    fig.update_layout(yaxis_title="", xaxis_title="")
    if orientation == "h":
        fig.update_layout(yaxis=dict(autorange="reversed"))
    return fig


def growth_decline_bar(df: pd.DataFrame, label_col: str, value_col: str, title: str = "") -> go.Figure:
    """Bar chart colored green for positive, red for negative values."""
    colors = [ACCENT_POSITIVE if v >= 0 else ACCENT_NEGATIVE for v in df[value_col]]
    fig = go.Figure(
        go.Bar(x=df[value_col], y=df[label_col], orientation="h", marker_color=colors)
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE, title=title, yaxis=dict(autorange="reversed"),
        xaxis_title="% change", yaxis_title="",
    )
    return fig


def stacked_bar(df: pd.DataFrame, x: str, y: str, color: str, title: str = "") -> go.Figure:
    fig = px.bar(
        df, x=x, y=y, color=color, template=PLOTLY_TEMPLATE, title=title,
        color_discrete_sequence=CATEGORICAL_SEQUENCE,
    )
    fig.update_layout(xaxis_title="", yaxis_title="")
    return fig


def dual_axis_trend(df: pd.DataFrame, x: str, y1: str, y2: str,
                     y1_name: str, y2_name: str, title: str = "") -> go.Figure:
    """Two indicators on independent y-axes against a shared x (year) axis --
    for visually comparing trends, never implying a shared scale or causation."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=df[x], y=df[y1], name=y1_name, mode="lines+markers",
                              line=dict(color=PRIMARY)))
    fig.add_trace(go.Scatter(x=df[x], y=df[y2], name=y2_name, mode="lines+markers",
                              line=dict(color=SECONDARY), yaxis="y2"))
    fig.update_layout(
        template=PLOTLY_TEMPLATE, title=title, hovermode="x unified",
        yaxis=dict(title=y1_name), yaxis2=dict(title=y2_name, overlaying="y", side="right"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    return fig


def empty_figure(message: str) -> go.Figure:
    """A blank figure carrying an explicit 'no data' annotation, so an empty
    chart never looks like a rendering bug -- it looks like a deliberate,
    labeled absence of data."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, font=dict(size=14, color=ACCENT_NEUTRAL),
        xref="paper", yref="paper", x=0.5, y=0.5,
    )
    fig.update_layout(
        template=PLOTLY_TEMPLATE,
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        height=250,
    )
    return fig
