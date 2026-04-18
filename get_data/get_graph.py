"""
get_data/plot_dt.py

get_graph(meter_id, name, out_path, nameplate_kva=None)
    Render a 3-panel PNG chart (voltage / current / consumption) for any meter.
    Works for DT, F33 feeder, or F11 feeder — all share the same meter schema.

get_graph_json(meter_id, name, nameplate_kva=None)
    Returns the Plotly figure as JSON (dict).

Requirements:
    pip install plotly kaleido pillow pandas numpy
    plotly_get_chrome          # one-time
"""

import json
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .fetch_feeder import get_feeder_readings_24h


# ─── config ──────────────────────────────────
SCALE = 100.0
V_MIN, V_MAX = 50.0, 300.0
I_MIN, I_MAX = 0.0, 2000.0
MAX_GAP_MINUTES = 120

IMAGE_WIDTH = 1200
IMAGE_HEIGHT = 850

COLOR_V = "#378ADD"  # voltage
COLOR_I = "#EF9F27"  # current
COLOR_E = "#1D9E75"  # energy


# ─── internal helpers ────────────────────────

def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["Ts"] = pd.to_datetime(d["Ts"])
    d = d.sort_values("Ts").reset_index(drop=True)

    for ph in ("a", "b", "c"):
        v = d[f"V_{ph}"].astype(float) / SCALE
        i = d[f"I_{ph}"].astype(float) / SCALE
        v = v.where((v >= V_MIN) & (v <= V_MAX))
        i = i.where((i >= I_MIN) & (i <= I_MAX))
        d[f"_v_{ph}"] = v
        d[f"_i_{ph}"] = i

    d["v_avg"] = d[["_v_a", "_v_b", "_v_c"]].mean(axis=1, skipna=True)

    all_nan_i = d[["_i_a", "_i_b", "_i_c"]].isna().all(axis=1)
    d["i_sum"] = d[["_i_a", "_i_b", "_i_c"]].sum(axis=1, skipna=True)
    d.loc[all_nan_i, "i_sum"] = np.nan

    d["kwh_raw"] = d["kwh"] if "kwh" in d.columns else np.nan
    d["kwh_delta"] = d["kwh_raw"].diff()

    # filter negative deltas (counter reset or invalid data)
    d.loc[d["kwh_delta"] < 0, "kwh_delta"] = np.nan

    gap = d["Ts"].diff().dt.total_seconds() / 60
    d["is_gap"] = (gap > MAX_GAP_MINUTES).fillna(False)

    return d[["Ts", "v_avg", "i_sum", "kwh_raw", "kwh_delta", "is_gap"]]


def _insert_gaps(d: pd.DataFrame, col: str):
    xs, ys = [], []
    for _, row in d.iterrows():
        if row["is_gap"]:
            xs.append(row["Ts"])
            ys.append(np.nan)
        xs.append(row["Ts"])
        ys.append(row[col])
    return xs, ys


def _data_quality(d: pd.DataFrame) -> dict:
    n = len(d)
    if n == 0:
        return {"total": 0, "hours": 0, "valid_v": 0, "valid_i": 0, "valid_e": 0}

    span = (d["Ts"].max() - d["Ts"].min()).total_seconds() / 3600 if n > 1 else 0

    return {
        "total": n,
        "hours": round(span, 1),
        "valid_v": int(d["v_avg"].notna().sum()),
        "valid_i": int(d["i_sum"].notna().sum()),
        "valid_e": int(d["kwh_delta"].notna().sum()),
    }


def _empty_figure(title: str, message: str) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.08,
        subplot_titles=("Voltage (V)", "Current (A)", "Consumption (kWh)"),
    )

    for row, label in [(1, "Voltage (V)"), (2, "Current (A)"), (3, "kWh")]:
        fig.add_trace(go.Scatter(x=[], y=[], showlegend=False), row=row, col=1)
        fig.update_yaxes(
            title_text=label,
            row=row,
            col=1,
            gridcolor="rgba(0,0,0,0.07)"
        )

    fig.update_xaxes(
        title_text="Time",
        row=3,
        col=1,
        gridcolor="rgba(0,0,0,0.07)"
    )

    fig.add_annotation(
        text=f"<b>{message}</b>",
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
        showarrow=False,
        font=dict(size=18, color="#888"),
        bgcolor="rgba(255,255,255,0.9)",
        borderpad=10,
    )

    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        width=IMAGE_WIDTH,
        height=IMAGE_HEIGHT,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=70, r=30, t=80, b=50),
    )

    return fig


def _build_figure(d: pd.DataFrame, name: str, nameplate_kva: int | None) -> go.Figure:
    q = _data_quality(d)

    banner = (
        f"{q['total']} readings · {q['hours']}h covered · "
        f"V:{q['valid_v']} I:{q['valid_i']} E:{q['valid_e']}"
    )

    title = f"<b>{name}</b> — last 24h<br><sup><i>{banner}</i></sup>"

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        subplot_titles=(
            "Average voltage across 3 phases (V)",
            "Total current through transformer (A)",
            "Consumption per interval (kWh)",
        ),
    )

    v_x, v_y = _insert_gaps(d, "v_avg")
    fig.add_trace(
        go.Scatter(
            x=v_x,
            y=v_y,
            mode="lines+markers",
            line=dict(color=COLOR_V, width=2.5),
            marker=dict(size=6, color=COLOR_V),
            connectgaps=False,
            showlegend=False,
        ),
        row=1,
        col=1
    )

    i_x, i_y = _insert_gaps(d, "i_sum")
    fig.add_trace(
        go.Scatter(
            x=i_x,
            y=i_y,
            mode="lines+markers",
            line=dict(color=COLOR_I, width=2.5),
            marker=dict(size=6, color=COLOR_I),
            connectgaps=False,
            showlegend=False,
        ),
        row=2,
        col=1
    )

    e_x, e_y = _insert_gaps(d, "kwh_delta")
    fig.add_trace(
        go.Scatter(
            x=e_x,
            y=e_y,
            mode="lines+markers",
            line=dict(color=COLOR_E, width=2.5),
            marker=dict(size=6, color=COLOR_E),
            connectgaps=False,
            fill="tozeroy",
            fillcolor="rgba(29, 158, 117, 0.12)",
            showlegend=False,
        ),
        row=3,
        col=1
    )

    if nameplate_kva:
        fig.add_hline(
            y=nameplate_kva,
            row=3,
            col=1,
            line=dict(color="rgba(180,30,30,0.5)", width=1.5, dash="dash"),
            annotation_text=f"Nameplate {nameplate_kva} kVA",
            annotation_position="top right",
        )

    grid = "rgba(0,0,0,0.07)"

    fig.update_yaxes(
        title_text="Voltage (V)",
        gridcolor=grid,
        range=[V_MIN, V_MAX],
        row=1,
        col=1
    )

    fig.update_yaxes(
        title_text="Current (A)",
        gridcolor=grid,
        rangemode="tozero",
        row=2,
        col=1
    )

    fig.update_yaxes(
        title_text="kWh",
        gridcolor=grid,
        rangemode="tozero",
        row=3,
        col=1
    )

    fig.update_xaxes(gridcolor=grid)
    fig.update_xaxes(title_text="Time", row=3, col=1)

    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        width=IMAGE_WIDTH,
        height=IMAGE_HEIGHT,
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=70, r=30, t=90, b=50),
        showlegend=False,
    )

    return fig


# ─── PUBLIC API ──────────────────────────────

def get_graph(meter_id: int, name: str, out_path: str,
              nameplate_kva: int | None = None) -> str:
    """
    Render a 3-panel PNG chart for any meter.

    Returns:
        Path to the saved PNG (same as out_path).
    """
    df = get_feeder_readings_24h(meter_id)

    if df is None or df.empty:
        fig = _empty_figure(
            f"{name} — last 24h",
            "No meter readings available"
        )
    else:
        d = _prepare(df)
        q = _data_quality(d)

        if q["valid_v"] == 0 and q["valid_i"] == 0 and q["valid_e"] == 0:
            fig = _empty_figure(
                f"{name} — last 24h",
                f"All {q['total']} readings filtered out (out of valid range)"
            )
        else:
            fig = _build_figure(d, name, nameplate_kva)

    fig.write_image(out_path, width=IMAGE_WIDTH, height=IMAGE_HEIGHT, scale=2)
    return out_path


def get_graph_json(meter_id: int, name: str,
                   nameplate_kva: int | None = None) -> dict:
    """
    Render a 3-panel Plotly chart for any meter and return it as JSON dict.
    (Fast: no PNG rendering)
    """
    df = get_feeder_readings_24h(meter_id)

    if df is None or df.empty:
        fig = _empty_figure(
            f"{name} — last 24h",
            "No meter readings available"
        )
    else:
        d = _prepare(df)
        q = _data_quality(d)

        if q["valid_v"] == 0 and q["valid_i"] == 0 and q["valid_e"] == 0:
            fig = _empty_figure(
                f"{name} — last 24h",
                f"All {q['total']} readings filtered out (out of valid range)"
            )
        else:
            fig = _build_figure(d, name, nameplate_kva)

    return fig.to_plotly_json()


def get_graph_json_string(meter_id: int, name: str,
                          nameplate_kva: int | None = None) -> str:
    """
    Same as get_graph_json(), but returns JSON string.
    """
    return json.dumps(get_graph_json(meter_id, name, nameplate_kva))