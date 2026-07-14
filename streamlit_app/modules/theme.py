"""
theme.py
=========
Consistent AIC-style visual theme: color palette and Streamlit page config
shared by every page.
"""

from __future__ import annotations

import streamlit as st

# AIC brand-neutral professional palette (navy/teal, single accent for growth,
# single accent for decline -- consistent across every chart in the app).
PRIMARY = "#0B3D59"       # deep navy
SECONDARY = "#1B8A8A"     # teal
ACCENT_POSITIVE = "#1E8E5A"   # growth / positive
ACCENT_NEGATIVE = "#B3452C"   # decline / negative
ACCENT_NEUTRAL = "#8A8F98"    # stable / N/A
BACKGROUND = "#F7F9FA"
CATEGORICAL_SEQUENCE = [
    "#0B3D59", "#1B8A8A", "#C98A2B", "#6C4F8C", "#B3452C",
    "#3E7CB1", "#4E9F3D", "#8A8F98", "#D1495B", "#2E4057",
]

PLOTLY_TEMPLATE = "plotly_white"


def apply_page_config(page_title: str, icon: str = "🎓") -> None:
    st.set_page_config(
        page_title=f"AIC Market Intelligence — {page_title}",
        page_icon=icon,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def inject_css() -> None:
    st.markdown(
        f"""
        <style>
        .stApp {{ background-color: {BACKGROUND}; }}
        h1, h2, h3 {{ color: {PRIMARY}; }}
        [data-testid="stMetricValue"] {{ color: {PRIMARY}; }}
        .aic-caption {{ color: {ACCENT_NEUTRAL}; font-size: 0.85rem; }}
        .aic-banner {{
            background-color: {PRIMARY}; color: white; padding: 0.6rem 1rem;
            border-radius: 6px; margin-bottom: 1rem; font-size: 0.9rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )
