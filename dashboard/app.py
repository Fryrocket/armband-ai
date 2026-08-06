#!/usr/bin/env python3
"""
Live Armband Dashboard – Streamlit
Run:  streamlit run dashboard/app.py --server.address 0.0.0.0 --server.port 8501
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# Make src importable when launched directly
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from armband_ai.calibration import build_calibration_pairs, fit_baseline, BaselineModel
from armband_ai.config import load_config, ROOT as PROJECT_ROOT
from armband_ai.db import init_db, insert_libre, delete_libre
from armband_ai.features import features_from_db
from armband_ai.hailo import HailoRunner, identify, try_import_hailort
from armband_ai.quality import score_from_db
from armband_ai.queries import (
    count_libre,
    count_readings,
    load_latest,
    load_libre,
    load_recent,
)

# ---------------------------------------------------------------------------
# Page config (must be first Streamlit call)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Armband Live",
    page_icon="❤️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container { padding-top: 1rem; padding-bottom: 1rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    div[data-testid="stMetricLabel"] { font-size: 0.85rem; }
    [data-testid="stHorizontalBlock"] { gap: 0.5rem; }
</style>
""",
    unsafe_allow_html=True,
)


def get_db_path() -> str:
    cfg = load_config()
    path = cfg["database"]["path"]
    p = Path(path)
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return str(p)


def get_cal_defaults() -> tuple[int, bool]:
    cfg = load_config()
    cal = cfg.get("calibration", {})
    return int(cal.get("window_seconds", 180)), bool(cal.get("prefer_still", True))


def get_feature_minutes() -> int:
    cfg = load_config()
    hailo = cfg.get("hailo", {})
    return int(hailo.get("feature_window_minutes", 5))


# ===========================================================================
# LIVE TAB
# ===========================================================================
def render_live(db_path: str) -> None:
    with st.sidebar:
        st.header("Live controls")
        window_min = st.select_slider(
            "Time window (minutes)",
            options=[5, 15, 30, 60, 120, 360, 720, 1440],
            value=60,
            key="live_window",
        )
        auto_refresh = st.checkbox("Auto-refresh (10 s)", value=True, key="live_refresh")
        st.caption(f"DB: `{db_path}`")
        st.caption(f"PPG readings: **{count_readings(db_path)}**")

    latest = load_latest(db_path)

    if latest is None:
        st.warning("No data yet. Start the MQTT logger and wait for the armband to publish.")
        if auto_refresh:
            time.sleep(10)
            st.rerun()
        return

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

    # Live quality + baseline estimate
    q_minutes = get_feature_minutes()
    quality = score_from_db(db_path, minutes=q_minutes)
    model_path = PROJECT_ROOT / "models" / "baseline.json"

    qcol, ecol = st.columns(2)
    with qcol:
        if quality is not None:
            st.metric(
                f"Signal quality ({q_minutes} min)",
                f"{quality.score:.0f}/100",
                delta=quality.label,
            )
            st.caption(" · ".join(quality.reasons[:3]))
        else:
            st.caption("Quality: no window data yet")

    with ecol:
        if model_path.exists() and filt940 is not None:
            try:
                model = BaselineModel.load(model_path)
                est = model.predict(float(filt940))
                note = ""
                if quality is not None and quality.score < 50:
                    note = " (low quality – treat estimate cautiously)"
                st.metric("Baseline glucose", f"{est:.0f} mg/dL")
                st.caption(f"R²={model.r2:.2f}, n={model.n_pairs}{note}")
            except Exception:
                st.caption("Baseline model present but failed to load")
        else:
            st.caption("No baseline model yet – use Calibration tab")

    df = load_recent(db_path, minutes=window_min)
    if df.empty:
        st.info(f"No readings in the last {window_min} minutes.")
        return

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        subplot_titles=(
            "filt940 (940 nm reflectance)",
            "Heart Rate (BPM)",
            "SpO₂ %",
            "Battery (V)",
        ),
        row_heights=[0.35, 0.25, 0.20, 0.20],
    )

    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["filt940"],
            mode="lines",
            name="filt940",
            line=dict(width=2, color="#e74c3c"),
        ),
        row=1,
        col=1,
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
            row=1,
            col=1,
        )

    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["bpm"],
            mode="lines+markers",
            name="BPM",
            line=dict(width=2, color="#3498db"),
            marker=dict(size=4),
        ),
        row=2,
        col=1,
    )

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
        row=3,
        col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=df["received_at"],
            y=df["batt"],
            mode="lines",
            name="Battery",
            line=dict(width=2, color="#f39c12"),
        ),
        row=4,
        col=1,
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

    with st.expander("Raw data (last 50)"):
        show_cols = [
            c
            for c in [
                "received_at",
                "bpm",
                "spo2",
                "temp",
                "filt940",
                "raw940",
                "batt",
                "motion",
                "moving",
                "trans",
                "boot",
                "conn_ms",
            ]
            if c in df.columns
        ]
        st.dataframe(
            df[show_cols].tail(50).iloc[::-1],
            use_container_width=True,
            hide_index=True,
        )

    if auto_refresh:
        time.sleep(10)
        st.rerun()


# ===========================================================================
# AI / FEATURES TAB
# ===========================================================================
def render_ai(db_path: str) -> None:
    st.subheader("Signal features & Hailo status")
    minutes = st.slider("Feature window (minutes)", 1, 30, get_feature_minutes(), key="ai_minutes")

    feats = features_from_db(db_path, minutes=minutes)
    quality = score_from_db(db_path, minutes=minutes)

    if feats is None:
        st.info("No PPG data in this window. Start the logger + armband.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        if quality is not None:
            c1.metric("Quality", f"{quality.score:.0f}/100", quality.label)
        c2.metric("Samples", f"{feats.n_samples}")
        c3.metric("Still %", f"{feats.still_fraction:.0%}")
        c4.metric("filt940 mean", f"{feats.filt940_mean:.1f}")

        if quality is not None:
            st.write("**Quality reasons**")
            for r in quality.reasons:
                st.write(f"- {r}")

        st.write("**Feature vector** (model input order)")
        vec = feats.to_vector()
        st.code(
            json.dumps(
                {
                    "vector": [round(float(x), 4) for x in vec.tolist()],
                    "keys": [
                        "filt940_mean",
                        "filt940_std",
                        "filt940_min",
                        "filt940_max",
                        "filt940_slope",
                        "raw940_mean",
                        "bpm_mean",
                        "bpm_std",
                        "spo2_mean",
                        "temp_mean",
                        "motion_mean",
                        "motion_max",
                        "still_fraction",
                        "moving_transitions",
                        "batt_mean",
                        "n_samples",
                        "duration_s",
                    ],
                },
                indent=2,
            )
        )

        with st.expander("All feature fields"):
            st.json(feats.to_dict())

    st.divider()
    st.subheader("Hailo device")

    device_json = PROJECT_ROOT / "models" / "hailo_device.json"
    cfg = load_config()
    hef_cfg = (cfg.get("hailo") or {}).get("hef_path") or ""

    if st.button("Probe Hailo now"):
        with st.spinner("Running hailortcli…"):
            info = identify()
            ok, msg = try_import_hailort()
            payload = info.to_dict()
            payload["bindings_ok"] = ok
            payload["bindings_msg"] = msg
            device_json.parent.mkdir(parents=True, exist_ok=True)
            device_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            st.success("Probe complete")

    if device_json.exists():
        try:
            saved = json.loads(device_json.read_text(encoding="utf-8"))
            st.json(saved)
        except Exception as e:
            st.warning(f"Could not read {device_json}: {e}")
    else:
        st.caption(
            "No saved device identity yet. On the Pi run: "
            "`python scripts/hailo_identify.py --extended --save models/hailo_device.json` "
            "or use **Probe Hailo now**."
        )

    runner = HailoRunner(hef_path=hef_cfg or None)
    st.write("**HailoRunner status**")
    st.json(runner.status())
    if not runner.ready:
        st.caption(
            "Inference not ready until HailoRT is installed, device is visible, "
            "and `hailo.hef_path` points to a compiled `.hef`."
        )


# ===========================================================================
# CALIBRATION TAB
# ===========================================================================
def render_calibration(db_path: str) -> None:
    init_db(db_path)
    default_window, default_prefer = get_cal_defaults()

    st.subheader("Log a Libre / reference reading")
    with st.form("log_glucose_form", clear_on_submit=True):
        col_a, col_b, col_c = st.columns([2, 2, 3])
        with col_a:
            glucose = st.number_input("Glucose (mg/dL)", min_value=20.0, max_value=600.0, value=120.0, step=1.0)
        with col_b:
            source = st.selectbox("Source", ["libre", "fingerstick", "other"])
        with col_c:
            notes = st.text_input("Notes (optional)", "")
        submitted = st.form_submit_button("Save reading")
        if submitted:
            row_id = insert_libre(db_path, glucose_mgdl=glucose, source=source, notes=notes or None)
            st.success(f"Saved id={row_id}: {glucose} mg/dL ({source})")
            st.rerun()

    st.divider()

    # Settings
    c1, c2 = st.columns(2)
    with c1:
        window = st.slider("Pairing window ± seconds", 30, 600, default_window, step=30)
    with c2:
        prefer_still = st.checkbox("Prefer still samples", value=default_prefer)

    libre_df = load_libre(db_path)
    st.caption(f"Libre readings stored: **{len(libre_df)}** · PPG readings: **{count_readings(db_path)}**")

    if not libre_df.empty:
        with st.expander("All Libre readings"):
            show = libre_df[["id", "recorded_at", "glucose_mgdl", "source", "notes"]].copy()
            st.dataframe(show.iloc[::-1], use_container_width=True, hide_index=True)
            del_id = st.number_input("Delete reading by id", min_value=0, value=0, step=1)
            if st.button("Delete") and del_id > 0:
                if delete_libre(db_path, int(del_id)):
                    st.success(f"Deleted id={del_id}")
                    st.rerun()
                else:
                    st.error("id not found")

    pairs = build_calibration_pairs(
        db_path,
        window_seconds=window,
        prefer_still=prefer_still,
    )

    st.subheader(f"Calibration pairs ({len(pairs)})")

    if pairs.empty:
        st.info(
            "No pairs yet. Log Libre readings while the armband is streaming, "
            "or widen the time window. Need data on both sides around the same time."
        )
        return

    st.dataframe(
        pairs[
            [
                "recorded_at",
                "glucose_mgdl",
                "filt940_mean",
                "n_samples",
                "still_fraction",
                "time_offset_s",
            ]
        ],
        use_container_width=True,
        hide_index=True,
    )

    model = fit_baseline(pairs, window_seconds=window, prefer_still=prefer_still)

    if model is None:
        st.warning("Need at least 2 pairs to fit a baseline model.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("R²", f"{model.r2:.3f}")
    m2.metric("MAE", f"{model.mae:.1f} mg/dL")
    m3.metric("RMSE", f"{model.rmse:.1f} mg/dL")
    m4.metric("Pairs", f"{model.n_pairs}")

    st.caption(f"glucose ≈ {model.slope:.6f} × filt940 + {model.intercept:.2f}")

    # Scatter + fit line
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=pairs["filt940_mean"],
            y=pairs["glucose_mgdl"],
            mode="markers",
            name="pairs",
            marker=dict(size=10, color="#e74c3c"),
            text=pairs["recorded_at"].astype(str),
            hovertemplate="filt940=%{x:.1f}<br>glucose=%{y:.0f}<br>%{text}<extra></extra>",
        )
    )

    x_line = np.linspace(pairs["filt940_mean"].min(), pairs["filt940_mean"].max(), 50)
    y_line = model.predict(x_line)
    fig.add_trace(
        go.Scatter(
            x=x_line,
            y=y_line,
            mode="lines",
            name="baseline fit",
            line=dict(color="#3498db", width=2),
        )
    )

    fig.update_layout(
        height=420,
        margin=dict(l=40, r=20, t=30, b=40),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis_title="filt940 (mean in window)",
        yaxis_title="Glucose (mg/dL)",
        showlegend=True,
    )
    st.plotly_chart(fig, use_container_width=True)

    if st.button("Save baseline model"):
        out = PROJECT_ROOT / "models" / "baseline.json"
        model.save(out)
        st.success(f"Saved → {out}")

    st.caption(
        "⚠️ Experimental only. Not a medical device. Do not use for treatment decisions."
    )


# ===========================================================================
# MAIN
# ===========================================================================
def main() -> None:
    db_path = get_db_path()
    st.title("Armband")

    tab_live, tab_ai, tab_cal = st.tabs(["Live", "AI / Features", "Calibration"])

    with tab_live:
        render_live(db_path)

    with tab_ai:
        render_ai(db_path)

    with tab_cal:
        render_calibration(db_path)


if __name__ == "__main__":
    main()
