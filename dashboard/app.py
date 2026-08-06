#!/usr/bin/env python3
"""
Live Armband Dashboard – Streamlit
Run:  streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# Make src importable when launched directly
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.queries import load_recent, load_latest, count_readings

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Armband Live",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Mobile-friendly CSS tweaks
st.markdown("""
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    div[data-testid="stMetricLabel"] { font-size: 0.85rem; }
    /* tighter gaps on phone */
    [data-testid="stHorizontalBlock"] { gap: 0.5rem; }
</style>
""", unsafe_allow_html=True)


def get_db_path() -> str:
    cfg = load_config()
    path = cfg["database"]["path"]
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def metric_color(value, good_low=None, good_high=None):
    """Simple helper – returns None (default) for now; can expand later."""
    return None


def main():
    db_path = get_db_path()

    st.title("Armband Live")

    # ---- Sidebar controls ----
    with st.sidebar:
        st.header("Controls")
        window_min = st.select_slider(
            "Time window (minutes)",
            options=[5, 15, 30, 60, 120, 360, 720, 1440],
            value=60,
        )
        auto_refresh = st.checkbox("Auto-refresh (10 s)", value=True)
        st.caption(f"DB: `{db_path}`")
        total = count_readings(db_path)
        st.caption(f"Total readings stored: **{total}**")

    # ---- Latest snapshot ----
    latest = load_latest(db_path)

    if latest is None:
        st.warning("No data yet. Start the MQTT logger and wait for the armband to publish.")
        if auto_refresh:
            st.rerun()  # will keep checking
        return

    # Top metrics row
    c1, c2, c3, c4, c5 = st.columns(5)

    bpm = latest.get("bpm")
    spo2 = latest.get("spo2")
    temp = latest.get("temp")
    filt940 = latest.get("filt940")
    batt = latest.get("batt")
    moving = latest.get("moving")
    received = latest.get("received_at", "?")

    c1.metric("BPM", f"{bpm}" if bpm is not None else "—")
    c2.metric("SpO₂", f"{spo2}%" if spo2 is not None and spo2 >= 0 else "—")
    c3.metric("Temp", f"{temp:.1f}°C" if temp is not None else "—")
    c4.metric("filt940", f"{filt940:.0f}" if filt940 is not None else "—")
    c5.metric("Battery", f"{batt:.2f} V" if batt is not None else "—")

    status = "🟢 Moving" if moving else "⚪ Still"
    st.caption(f"Last update: `{received}` · {status} · boot #{latest.get('boot', '?')}")

    # ---- Load history ----
    df = load_recent(db_path, minutes=window_min)

    if df.empty:
        st.info(f"No readings in the last {window_min} minutes.")
        return

    # ---- Charts ----
    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=("filt940 (940 nm reflectance)", "Heart Rate (BPM)", "SpO₂ %", "Battery (V)"),
        row_heights=[0.35, 0.25, 0.20, 0.20],
    )

    # 940 nm – primary experimental channel
    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["filt940"],
            mode="lines",
            name="filt940",
            line=dict(width=2, color="#e74c3c"),
        ),
        row=1, col=1,
    )
    if "raw940" in df.columns:
        fig.add_trace(
            go.Scatter(
                x=df["received_at"],
                y=df["raw940"],
                mode="lines",
                name="raw940",
                line=dict(width=1, color="#e74c3c", dash="dot"),
                opacity=0.4,
            ),
            row=1, col=1,
        )

    # BPM
    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["bpm"],
            mode="lines+markers",
            name="BPM",
            line=dict(width=2, color="#3498db"),
            marker=dict(size=4),
        ),
        row=2, col=1,
    )

    # SpO2 (hide invalid -1)
    spo2_plot = df["spo2"].where(df["spo2"] >= 0)
    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=spo2_plot,
            mode="lines+markers",
            name="SpO₂",
            line=dict(width=2, color="#2ecc71"),
            marker=dict(size=4),
        ),
        row=3, col=1,
    )

    # Battery
    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["batt"],
            mode="lines",
            name="Battery",
            line=dict(width=2, color="#f39c12"),
        ),
        row=4, col=1,
    )

    fig.update_layout(
        height=780,
        margin=dict(l=40, r=20, t=40, b=30),
        showlegend=False,
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.2)")
    fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.2)")

    st.plotly_chart(fig, use_container_width=True)

    # ---- Motion & extras ----
    with st.expander("Motion & details"):
        m1, m2 = st.columns(2)
        with m1:
            st.subheader("Motion magnitude")
            fig_m = go.Figure()
            fig_m.add_trace(
                go.Scatter(
                    x=df["received_at"],
                    y=df["motion"],
                    mode="lines",
                    line=dict(color="#9b59b6", width=2),
                    name="motion",
                )
            )
            # highlight moving periods
            if "moving" in df.columns:
                moving_mask = df["moving"] == 1
                if moving_mask.any():
                    fig_m.add_trace(
                        go.Scatter(
                            x=df.loc[moving_mask, "received_at"],
                            y=df.loc[moving_mask, "motion"],
                            mode="markers",
                            marker=dict(color="#e74c3c", size=6),
                            name="moving",
                        )
                    )
            fig_m.update_layout(
                height=280,
                margin=dict(l=20, r=20, t=20, b=20),
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
            )
            st.plotly_chart(fig_m, use_container_width=True)

        with m2:
            st.subheader("Recent transitions")
            if "trans" in df.columns:
                transitions = df[df["trans"].isin(["still_to_moving", "moving_to_still"])]
                if not transitions.empty:
                    st.dataframe(
                        transitions[["received_at", "trans", "motion", "bpm"]].tail(15),
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No motion transitions in this window.")

    # ---- Raw table ----
    with st.expander("Raw data (last 50)"):
        show_cols = [
            c for c in [
                "received_at", "bpm", "spo2", "temp", "filt940", "raw940",
                "batt", "motion", "moving", "trans", "boot", "conn_ms"
            ] if c in df.columns
        ]
        st.dataframe(
            df[show_cols].tail(50).iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

    # ---- Auto refresh ----
    if auto_refresh:
        import time
        time.sleep(10)
        st.rerun()


if __name__ == "__main__":
    main()
