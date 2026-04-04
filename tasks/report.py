#!/usr/bin/env python3
"""
Task 6: Generate Report
=======================
Produces an HTML dashboard consolidating all pipeline outputs:
- Fleet True Efficiency over time
- TE decomposition per device
- Device health scores
- Anomaly timeline (ground truth)
- Failure risk ranking + model metrics
- Controller actions and tier assignments
- Feature importance

Charts rendered via matplotlib → base64 PNG embedded in HTML.

Inputs:  kpi_timeseries.parquet, fleet_risk_scores.json, model_metrics.json,
         fleet_actions.json, fleet_metadata.json
Outputs: report.html
"""

import json
import base64
import io
import os
from datetime import datetime

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

TIER_COLORS = {
    "CRITICAL": "#F44336",
    "WARNING": "#FF9800",
    "DEGRADED": "#FFC107",
    "HEALTHY": "#4CAF50",
}


def fig_to_base64(fig) -> str:
    """Convert matplotlib figure to base64-encoded PNG."""
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode("utf-8")
    plt.close(fig)
    return b64


def plot_te_timeseries(df: pd.DataFrame) -> str:
    """Fleet True Efficiency over time, colored by device."""
    fig, ax = plt.subplots(figsize=(14, 5))
    for device_id, group in df.groupby("device_id"):
        g = group.set_index("timestamp")["true_efficiency"].dropna()
        g_hourly = g.resample("1h").mean()
        ax.plot(g_hourly.index, g_hourly.values, label=device_id, alpha=0.7, linewidth=0.8)
    ax.set_ylabel("True Efficiency (J/TH)")
    ax.set_title("Fleet True Efficiency Over Time")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.grid(True, alpha=0.3)
    return fig_to_base64(fig)


def plot_te_decomposition(df: pd.DataFrame) -> str:
    """Average TE decomposition factors per device (stacked bar)."""
    active = df[df["true_efficiency"].notna()].copy()
    decomp = active.groupby("device_id").agg(
        te_base=("te_base", "mean"),
        voltage_penalty=("voltage_penalty", "mean"),
        cooling_ratio=("cooling_ratio", "mean"),
    ).sort_values("te_base")

    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(decomp))
    labels = decomp.index

    ax.bar(x, decomp["te_base"], label="TE_base (naive J/TH)", color="#2196F3")
    ax.bar(x, decomp["te_base"] * (decomp["voltage_penalty"] - 1),
           bottom=decomp["te_base"], label="Voltage penalty", color="#FF9800")
    ax.bar(x, decomp["te_base"] * decomp["voltage_penalty"] * (decomp["cooling_ratio"] - 1),
           bottom=decomp["te_base"] * decomp["voltage_penalty"],
           label="Cooling overhead", color="#F44336")

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45)
    ax.set_ylabel("J/TH contribution")
    ax.set_title("TE Decomposition by Device")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    return fig_to_base64(fig)


def plot_health_scores(df: pd.DataFrame) -> str:
    """TE_score heatmap over time per device."""
    active = df[df["te_score"].notna()].copy()
    active["date"] = active["timestamp"].dt.date

    pivot = active.groupby(["device_id", "date"])["te_score"].mean().unstack(level=0)

    fig, ax = plt.subplots(figsize=(14, 5))
    im = ax.imshow(pivot.T.values, aspect="auto", cmap="RdYlGn", vmin=0.5, vmax=1.2)
    ax.set_yticks(range(len(pivot.columns)))
    ax.set_yticklabels(pivot.columns, fontsize=8)

    date_labels = [str(d) for d in pivot.index]
    tick_positions = list(range(0, len(date_labels), 5))
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([date_labels[i] for i in tick_positions], rotation=45, fontsize=7)

    ax.set_title("Device Health Score (TE_score) Over Time")
    fig.colorbar(im, ax=ax, label="TE_score (1.0 = nominal)")
    return fig_to_base64(fig)


def plot_anomaly_timeline(df: pd.DataFrame) -> str:
    """Ground-truth anomaly labels over time per device."""
    label_cols = {
        "label_thermal_deg": ("Thermal Degradation", "#F44336"),
        "label_psu_instability": ("PSU Instability", "#FF9800"),
        "label_hashrate_decay": ("Hashrate Decay", "#9C27B0"),
    }

    fig, axes = plt.subplots(len(label_cols), 1, figsize=(14, 8), sharex=True)

    for ax, (col, (title, color)) in zip(axes, label_cols.items()):
        for device_id, group in df.groupby("device_id"):
            g = group.set_index("timestamp")[col]
            if g.max() > 0:
                g_hourly = g.resample("1h").max()
                ax.fill_between(g_hourly.index, 0, g_hourly.values,
                                alpha=0.5, color=color)
                ax.plot(g_hourly.index, g_hourly.values, alpha=0.8,
                        linewidth=0.8, color=color)
                onset = g_hourly[g_hourly > 0].index.min()
                if onset is not None:
                    ax.annotate(device_id, xy=(onset, 0.5), fontsize=7,
                                color="#333", fontweight="bold")
        ax.set_ylabel(title, fontsize=9)
        ax.set_ylim(-0.05, 1.1)
        ax.grid(True, alpha=0.3)

    axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    axes[0].set_title("Ground-Truth Anomaly Timeline (Injected Patterns)")
    fig.tight_layout()
    return fig_to_base64(fig)


def plot_risk_ranking(risk_scores: dict) -> str:
    """Horizontal bar chart of device risk scores."""
    risks = risk_scores["device_risks"]
    devices = [r["device_id"] for r in risks]
    mean_risks = [r["mean_risk"] for r in risks]
    colors = ["#F44336" if r["flagged"] else "#4CAF50" for r in risks]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(devices, mean_risks, color=colors)
    ax.axvline(x=risk_scores["threshold"], color="gray", linestyle="--",
               label=f"Threshold ({risk_scores['threshold']})")
    ax.set_xlabel("Mean Anomaly Probability")
    ax.set_title("Device Failure Risk Ranking")
    ax.legend()
    ax.grid(True, alpha=0.3, axis="x")
    return fig_to_base64(fig)


def plot_controller_tiers(actions_data: dict) -> str:
    """Tier assignment per device (color-coded bar) + pie chart."""
    actions = actions_data["actions"]
    tier_counts = actions_data["tier_counts"]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5),
                                    gridspec_kw={"width_ratios": [2, 1]})

    # Bar chart: per-device tier + risk score
    devices = [a["device_id"] for a in actions]
    risks = [a["risk_score"] for a in actions]
    colors = [TIER_COLORS.get(a["tier"], "#9E9E9E") for a in actions]

    ax1.barh(devices, risks, color=colors)
    for i, a in enumerate(actions):
        ax1.text(a["risk_score"] + 0.02, i, a["tier"], va="center",
                fontsize=8, fontweight="bold", color=TIER_COLORS.get(a["tier"], "#333"))
    ax1.set_xlabel("Risk Score")
    ax1.set_title("Controller Tier Assignments")
    ax1.set_xlim(0, 1.3)
    ax1.grid(True, alpha=0.3, axis="x")

    # Pie chart: fleet health distribution
    labels = list(tier_counts.keys())
    sizes = list(tier_counts.values())
    pie_colors = [TIER_COLORS.get(t, "#9E9E9E") for t in labels]

    ax2.pie(sizes, labels=labels, colors=pie_colors, autopct="%1.0f%%",
            startangle=90, textprops={"fontsize": 9})
    ax2.set_title("Fleet Health Distribution")

    fig.tight_layout()
    return fig_to_base64(fig)


def plot_feature_importance(metrics: dict) -> str:
    """Top features from XGBoost model. Returns empty string if no features available."""
    features = metrics.get("top_features", [])
    if not features:
        return ""

    names = [f["feature"] for f in features][::-1]
    values = [f["importance"] for f in features][::-1]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(names, values, color="#2196F3")
    ax.set_xlabel("Importance")
    ax.set_title("Top Predictive Features (XGBoost)")
    ax.grid(True, alpha=0.3, axis="x")
    return fig_to_base64(fig)


# ── Phase 5: Prediction charts ───────────────────────────────────────

def plot_prediction_fan_chart(risk_scores: dict) -> str:
    """Fan chart showing predicted TE_score trajectories for top-risk devices.

    X-axis = horizon (now → +1h → +6h → +24h → +7d). Y-axis = TE_score.
    p50 line with shaded p10–p90 uncertainty region. Horizontal threshold
    lines at TE=0.8 (DEGRADED) and TE=0.6 (CRITICAL).
    Shows top 3 highest-risk devices that have prediction data.
    """
    # Collect devices with predictions, sorted by risk
    devices_with_preds = [
        d for d in risk_scores["device_risks"] if "predictions" in d
    ][:3]

    if not devices_with_preds:
        return ""

    all_horizon_labels = ["now", "+1h", "+6h", "+24h", "+7d"]
    all_horizon_keys = ["te_score_1h", "te_score_6h", "te_score_24h", "te_score_7d"]

    # Only include horizons present in the first device's predictions
    first_preds = devices_with_preds[0]["predictions"]
    available = [(lbl, key) for lbl, key in zip(all_horizon_labels[1:], all_horizon_keys)
                 if key in first_preds]
    horizon_labels = ["now"] + [lbl for lbl, _ in available]
    horizon_keys = [key for _, key in available]
    colors = ["#F44336", "#FF9800", "#2196F3"]

    fig, ax = plt.subplots(figsize=(10, 5))

    for i, device in enumerate(devices_with_preds):
        preds = device["predictions"]
        current_te = device["latest_snapshot"]["te_score"]

        # Build arrays: current value + available horizon predictions
        p50_vals = [current_te] + [preds[k]["p50"] for k in horizon_keys if k in preds]
        p10_vals = [current_te] + [preds[k]["p10"] for k in horizon_keys if k in preds]
        p90_vals = [current_te] + [preds[k]["p90"] for k in horizon_keys if k in preds]
        x = range(len(p50_vals))

        color = colors[i % len(colors)]
        ax.plot(x, p50_vals, marker="o", color=color, linewidth=2,
                label=f"{device['device_id']} (p50)", zorder=3)
        ax.fill_between(x, p10_vals, p90_vals, alpha=0.15, color=color)

    # Threshold lines
    ax.axhline(y=0.8, color="#FFC107", linestyle="--", linewidth=1.5,
               label="DEGRADED (0.8)", alpha=0.8)
    ax.axhline(y=0.6, color="#F44336", linestyle="--", linewidth=1.5,
               label="CRITICAL (0.6)", alpha=0.8)

    ax.set_xticks(range(len(horizon_labels)))
    ax.set_xticklabels(horizon_labels)
    ax.set_ylabel("Predicted TE_score")
    ax.set_title("Multi-Horizon TE_score Predictions (Top Risk Devices)")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=8)
    ax.set_ylim(0, 1.3)
    ax.grid(True, alpha=0.3)
    return fig_to_base64(fig)


def plot_calibration_diagram(metrics: dict) -> str:
    """Reliability diagram: predicted quantile vs observed frequency.

    One line per horizon. Perfect calibration = diagonal. Shows how well
    the model's uncertainty estimates match reality. Points above the
    diagonal mean the model is overconfident; below means underconfident.
    """
    regression = metrics.get("regression")
    if not regression:
        return ""

    # We plot the three quantile levels (p10, p50, p90) and their
    # calibration coverage. For a well-calibrated model:
    # - p10 should have ~10% of actuals below it
    # - p50 should have ~50% of actuals below it
    # - p90 should have ~90% of actuals below it
    # We only have the 80% interval coverage, so we plot that per horizon.
    fig, ax = plt.subplots(figsize=(7, 5))

    horizons = regression.get("horizons", [])
    per_horizon = regression.get("per_horizon", {})

    if not horizons or not per_horizon:
        return ""

    # Bar chart of calibration coverage per horizon
    coverage_vals = []
    labels = []
    for h in horizons:
        h_data = per_horizon.get(h, {})
        cov = h_data.get("calibration_80")
        if cov is not None:
            labels.append(h)
            coverage_vals.append(cov * 100)

    if not labels:
        return ""

    x = range(len(labels))
    bars = ax.bar(x, coverage_vals, color="#2196F3", alpha=0.8)

    # Target zone: 75-85%
    ax.axhspan(75, 85, color="#4CAF50", alpha=0.1, label="Target (75-85%)")
    ax.axhline(y=80, color="#4CAF50", linestyle="--", linewidth=1, alpha=0.6)

    # Color bars outside target zone
    for bar, val in zip(bars, coverage_vals):
        if val < 75 or val > 85:
            bar.set_color("#FF9800")

    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_ylabel("80% Interval Coverage (%)")
    ax.set_xlabel("Prediction Horizon")
    ax.set_title("Prediction Interval Calibration")
    ax.set_ylim(0, 100)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    return fig_to_base64(fig)


def plot_model_comparison(metrics: dict) -> str:
    """Bar chart: classifier F1 alongside regressor RMSE per horizon.

    Two-panel chart showing both model types side-by-side for a complete
    picture of model performance.
    """
    regression = metrics.get("regression")
    if not regression:
        return ""

    per_horizon = regression.get("per_horizon", {})
    horizons = regression.get("horizons", [])
    if not horizons or not per_horizon:
        return ""

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5),
                                    gridspec_kw={"width_ratios": [1, 2]})

    # Left panel: Classifier metrics
    clf_metrics = ["F1 Score", "Accuracy"]
    clf_values = [metrics["f1_score"] * 100, metrics["accuracy"] * 100]
    ax1.barh(clf_metrics, clf_values, color=["#2196F3", "#4CAF50"])
    ax1.set_xlim(0, 105)
    ax1.set_xlabel("Score (%)")
    ax1.set_title("Classifier Performance")
    for i, v in enumerate(clf_values):
        ax1.text(v + 1, i, f"{v:.1f}%", va="center", fontsize=10)
    ax1.grid(True, alpha=0.3, axis="x")

    # Right panel: Regressor RMSE per horizon
    rmse_vals = [per_horizon[h]["rmse_p50"] for h in horizons]
    x = range(len(horizons))
    bars = ax2.bar(x, rmse_vals, color="#FF9800", alpha=0.8)
    ax2.set_xticks(x)
    ax2.set_xticklabels(horizons)
    ax2.set_ylabel("RMSE (TE_score units)")
    ax2.set_xlabel("Prediction Horizon")
    ax2.set_title("Regressor RMSE (p50 median forecast)")
    for bar, v in zip(bars, rmse_vals):
        ax2.text(bar.get_x() + bar.get_width() / 2, v + 0.001,
                f"{v:.4f}", ha="center", fontsize=9)
    ax2.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    return fig_to_base64(fig)


def _build_predictions_table(risk_scores: dict) -> str:
    """Build HTML table of per-device multi-horizon predictions."""
    devices_with_preds = [
        d for d in risk_scores["device_risks"] if "predictions" in d
    ]
    if not devices_with_preds:
        return ""

    horizon_keys = ["te_score_1h", "te_score_6h", "te_score_24h", "te_score_7d"]
    horizon_labels = ["+1h", "+6h", "+24h", "+7d"]

    rows = ""
    for d in devices_with_preds:
        preds = d["predictions"]
        crossings = d.get("predicted_crossings", {})

        # Crossing summary
        crossing_parts = []
        for threshold_name, info in crossings.items():
            crossing_parts.append(
                f'{threshold_name} @ {info["horizon"]} ({info["confidence"]})'
            )
        crossing_str = ", ".join(crossing_parts) if crossing_parts else '<span style="color:#999">&mdash;</span>'

        # Quantile cells
        cells = ""
        for key in horizon_keys:
            p = preds.get(key, {})
            p50 = p.get("p50", 0)
            # Color based on TE thresholds
            if p50 < 0.6:
                color = "#F44336"
            elif p50 < 0.8:
                color = "#FF9800"
            else:
                color = "#4CAF50"
            cells += (
                f'<td style="text-align:center">'
                f'<span style="color:{color};font-weight:bold">{p50:.3f}</span>'
                f'<br><span style="font-size:10px;color:#999">'
                f'[{p.get("p10", 0):.2f}–{p.get("p90", 0):.2f}]</span></td>'
            )

        rows += f"""
        <tr>
            <td>{d['device_id']}</td>
            <td>{d['latest_snapshot']['te_score']:.3f}</td>
            {cells}
            <td style="font-size:11px">{crossing_str}</td>
        </tr>"""

    return f"""
    <table>
        <tr>
            <th>Device</th><th>Current TE</th>
            {''.join(f'<th style="text-align:center">{h}<br><span style="font-size:10px">[p10–p90]</span></th>' for h in horizon_labels)}
            <th>Predicted Crossings</th>
        </tr>
        {rows}
    </table>"""


# ── Phase 3: Trend Analysis charts ──────────────────────────────────

REGIME_COLORS = {
    "falling_fast": "#F44336",
    "declining": "#FF9800",
    "stable": "#2196F3",
    "recovering": "#4CAF50",
    "recovering_fast": "#00C853",
}


def plot_te_trajectory(df: pd.DataFrame, trend_data: dict) -> str:
    """7-day TE_score per device with regime-colored lines and 12h projections.

    Each device line is colored by its CUSUM regime classification.
    Dashed lines show 12-hour forward projections from the 24h linear trend.
    Horizontal thresholds at 0.8 (DEGRADED) and 0.6 (severe).
    """
    device_lookup = {d["device_id"]: d for d in trend_data.get("devices", [])}
    fig, ax = plt.subplots(figsize=(14, 6))

    for device_id, group in df.groupby("device_id"):
        g = group.set_index("timestamp")["te_score"].dropna()
        if len(g) == 0:
            continue
        t_end = g.index.max()
        t_start = t_end - pd.Timedelta(days=7)
        g_7d = g[g.index >= t_start]
        g_hourly = g_7d.resample("1h").mean().dropna()
        if len(g_hourly) == 0:
            continue

        dev_trend = device_lookup.get(device_id, {})
        direction = dev_trend.get("primary_direction", "stable")
        color = REGIME_COLORS.get(direction, "#2196F3")

        ax.plot(g_hourly.index, g_hourly.values, label=f"{device_id} ({direction})",
                alpha=0.8, linewidth=1.2, color=color)

        # 12h forward projection line
        slope = dev_trend.get("primary_slope_per_hour", 0.0)
        r2 = dev_trend.get("primary_r_squared", 0.0)
        if abs(slope) > 1e-6 and r2 >= 0.1:
            last_val = float(g_hourly.values[-1])
            last_time = g_hourly.index[-1]
            proj_hours = np.arange(0, 13)
            proj_vals = last_val + slope * proj_hours
            proj_times = [last_time + pd.Timedelta(hours=h) for h in proj_hours]
            ax.plot(proj_times, proj_vals, linestyle="--", alpha=0.4,
                    linewidth=1.0, color=color)

    ax.axhline(y=0.8, color="#FFC107", linestyle="--", linewidth=1.5,
               label="DEGRADED (0.8)", alpha=0.7)
    ax.axhline(y=0.6, color="#F44336", linestyle="--", linewidth=1.5,
               label="Severe (0.6)", alpha=0.7)

    ax.set_ylabel("TE_score")
    ax.set_title("TE_score Trajectory (7-day) with 12h Projections")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d %H:%M"))
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    return fig_to_base64(fig)


def plot_trend_heatmap(trend_data: dict) -> str:
    """Devices x windows heatmap of TE_score slope (RdYlGn diverging)."""
    devices_data = trend_data.get("devices", [])
    if not devices_data:
        return ""

    windows = ["1h", "6h", "24h", "7d"]
    device_ids = [d["device_id"] for d in devices_data]
    matrix = np.zeros((len(device_ids), len(windows)))

    for i, dev in enumerate(devices_data):
        te_trends = dev.get("te_trends", {})
        for j, w in enumerate(windows):
            matrix[i, j] = te_trends.get(w, {}).get("slope_per_hour", 0.0)

    fig, ax = plt.subplots(figsize=(8, max(4, len(device_ids) * 0.5)))
    im = ax.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=-0.03, vmax=0.03)

    ax.set_xticks(range(len(windows)))
    ax.set_xticklabels(windows)
    ax.set_yticks(range(len(device_ids)))
    ax.set_yticklabels(device_ids, fontsize=8)
    ax.set_xlabel("Trend Window")
    ax.set_title("TE_score Trend Slope (per hour)")

    for i in range(len(device_ids)):
        for j in range(len(windows)):
            val = matrix[i, j]
            text_color = "white" if abs(val) > 0.015 else "black"
            ax.text(j, i, f"{val:.4f}", ha="center", va="center",
                    fontsize=7, color=text_color)

    fig.colorbar(im, ax=ax, label="Slope (TE_score/hour)")
    fig.tight_layout()
    return fig_to_base64(fig)


def build_trend_section(trend_data: dict, charts: dict) -> str:
    """Build the Trend Analysis HTML section."""
    if not trend_data:
        return ""

    devices = trend_data.get("devices", [])
    fleet = trend_data.get("fleet_summary", {})

    crossings_html = ""
    for dev in devices:
        projections = dev.get("projections", {})
        regime = dev.get("regime", {})

        cross_08 = projections.get("0.8", {})
        cross_06 = projections.get("0.6", {})

        hours_08 = f"{cross_08['hours_to_crossing']:.0f}h" if cross_08.get("will_cross") else "&mdash;"
        conf_08 = f"{cross_08.get('confidence', 0):.2f}" if cross_08.get("will_cross") else ""
        hours_06 = f"{cross_06['hours_to_crossing']:.0f}h" if cross_06.get("will_cross") else "&mdash;"
        conf_06 = f"{cross_06.get('confidence', 0):.2f}" if cross_06.get("will_cross") else ""

        direction = dev.get("primary_direction", "stable")
        dir_color = REGIME_COLORS.get(direction, "#666")
        dir_badge = f'<span style="color:{dir_color};font-weight:bold">{direction}</span>'

        regime_flag = ""
        if regime.get("change_detected"):
            regime_flag = (f'<span style="color:#F44336;font-weight:bold">'
                           f'REGIME CHANGE ({regime.get("direction", "")})</span>')

        crossings_html += f"""
        <tr>
            <td>{dev['device_id']}</td>
            <td>{dir_badge}</td>
            <td>{dev.get('primary_slope_per_hour', 0):.4f}</td>
            <td>{dev.get('primary_r_squared', 0):.3f}</td>
            <td>{hours_08}</td>
            <td>{conf_08}</td>
            <td>{hours_06}</td>
            <td>{conf_06}</td>
            <td>{regime_flag}</td>
        </tr>"""

    dir_dist = fleet.get("direction_distribution", {})
    dir_summary = ", ".join(f"{k}: {v}" for k, v in sorted(dir_dist.items()))

    trajectory_img = ""
    if charts.get("te_trajectory"):
        trajectory_img = f'<div class="chart"><img src="data:image/png;base64,{charts["te_trajectory"]}" /></div>'

    heatmap_img = ""
    if charts.get("trend_heatmap"):
        heatmap_img = f'<div class="chart"><img src="data:image/png;base64,{charts["trend_heatmap"]}" /></div>'

    return f"""
    <h2>Trend Analysis</h2>
    <p>Rolling window trend analysis with CUSUM regime change detection.
       Fleet direction distribution: {dir_summary}.
       Regime changes: {fleet.get('regime_changes', 0)}/{fleet.get('device_count', 0)} devices.</p>

    <h3>TE_score Trajectory with Projections</h3>
    {trajectory_img}

    <h3>Trend Slope Heatmap</h3>
    {heatmap_img}

    <h3>Projected Threshold Crossings</h3>
    <table>
        <tr>
            <th>Device</th><th>Direction</th><th>Slope/h</th><th>R&sup2;</th>
            <th>Hours to 0.8</th><th>Conf</th>
            <th>Hours to 0.6</th><th>Conf</th>
            <th>Regime</th>
        </tr>
        {crossings_html}
    </table>
    """


# ── Phase 4: Economic Analysis Charts ────────────���───────────────────────

def plot_economic_summary(cost_data: dict) -> str:
    """Fleet economic summary: stacked bar (revenue vs costs) + net line."""
    projections = cost_data.get("device_projections", [])
    if not projections:
        return ""

    total_revenue = 0.0
    total_energy = 0.0
    total_risk = 0.0
    total_maintenance = 0.0

    for dp in projections:
        action = dp.get("optimal", {}).get("recommended_action", "do_nothing")
        action_data = dp.get("projections", {}).get(action, {}).get("24h", {})
        total_revenue += action_data.get("revenue_usd", 0)
        total_energy += action_data.get("energy_cost_usd", 0)
        total_risk += action_data.get("risk_cost_usd", 0)
        total_maintenance += action_data.get("maintenance_cost_usd", 0)

    fig, ax = plt.subplots(figsize=(10, 5))
    categories = ["Revenue", "Energy Cost", "Risk Cost", "Maintenance"]
    values = [total_revenue, -total_energy, -total_risk, -total_maintenance]
    colors = ["#4CAF50", "#2196F3", "#FF9800", "#F44336"]
    bars = ax.bar(categories, values, color=colors, width=0.6)

    net = total_revenue - total_energy - total_risk - total_maintenance
    ax.axhline(y=net, color="#333", linestyle="--", linewidth=1.5, alpha=0.7)
    ax.annotate(f"Net: ${net:,.0f}", xy=(3.3, net), fontsize=10,
                fontweight="bold", color="#333")

    for bar, val in zip(bars, values):
        y_pos = bar.get_height()
        label = f"${abs(val):,.0f}" if val >= 0 else f"-${abs(val):,.0f}"
        offset = 5 if val >= 0 else -15
        ax.text(bar.get_x() + bar.get_width() / 2, y_pos + offset,
                label, ha="center", fontsize=9, fontweight="bold")

    ax.set_ylabel("USD (24h horizon)")
    ax.set_title("Fleet Economic Summary — 24h Horizon (Recommended Actions)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="#666", linewidth=0.5)
    return fig_to_base64(fig)


def plot_device_cost_breakdown(cost_data: dict) -> str:
    """Per-device cost breakdown: horizontal bar at 24h horizon sorted by net profit."""
    projections = cost_data.get("device_projections", [])
    if not projections:
        return ""

    device_data = []
    for dp in projections:
        action = dp.get("optimal", {}).get("recommended_action", "do_nothing")
        action_data = dp.get("projections", {}).get(action, {}).get("24h", {})
        device_data.append({
            "device_id": dp["device_id"],
            "revenue": action_data.get("revenue_usd", 0),
            "energy": action_data.get("energy_cost_usd", 0),
            "risk": action_data.get("risk_cost_usd", 0),
            "net": action_data.get("net_usd", 0),
            "action": action,
        })
    device_data.sort(key=lambda d: d["net"])

    fig, ax = plt.subplots(figsize=(12, 6))
    devices = [d["device_id"] for d in device_data]
    y = range(len(devices))
    revenues = [d["revenue"] for d in device_data]
    ax.barh(y, revenues, height=0.6, color="#4CAF50", alpha=0.8, label="Revenue")
    energy_costs = [-d["energy"] for d in device_data]
    ax.barh(y, energy_costs, height=0.6, color="#2196F3", alpha=0.8, label="Energy Cost")
    risk_costs = [-d["risk"] for d in device_data]
    ax.barh(y, risk_costs, height=0.6, left=energy_costs, color="#FF9800",
            alpha=0.8, label="Risk Cost")

    ACTION_LABELS = {
        "do_nothing": "HOLD", "shutdown": "STOP",
        "schedule_maintenance": "MAINT",
        "underclock_90pct": "UC90", "underclock_80pct": "UC80",
        "underclock_70pct": "UC70",
    }
    for i, d in enumerate(device_data):
        label = ACTION_LABELS.get(d["action"], d["action"])
        net_color = "#4CAF50" if d["net"] >= 0 else "#F44336"
        ax.text(max(revenues) + 10, i, f"{label} (${d['net']:+,.0f})",
                va="center", fontsize=8, color=net_color, fontweight="bold")

    ax.set_yticks(y)
    ax.set_yticklabels(devices, fontsize=9)
    ax.set_xlabel("USD (24h horizon)")
    ax.set_title("Per-Device Cost Breakdown — 24h Horizon (Recommended Action)")
    ax.axvline(x=0, color="#666", linewidth=0.5)
    ax.legend(loc="lower right", fontsize=8)
    ax.grid(True, alpha=0.3, axis="x")
    fig.tight_layout()
    return fig_to_base64(fig)


def plot_roi_projection(cost_data: dict) -> str:
    """ROI projection: multi-line chart across horizons per device."""
    projections = cost_data.get("device_projections", [])
    if not projections:
        return ""

    ACTION_COLORS = {
        "do_nothing": "#4CAF50", "underclock_90pct": "#8BC34A",
        "underclock_80pct": "#FFC107", "underclock_70pct": "#FF9800",
        "schedule_maintenance": "#2196F3", "shutdown": "#F44336",
    }

    fig, ax = plt.subplots(figsize=(12, 6))
    horizons = ["24h", "168h", "720h"]
    x = range(len(horizons))

    for dp in projections:
        action = dp.get("optimal", {}).get("recommended_action", "do_nothing")
        action_projections = dp.get("projections", {}).get(action, {})
        nets = [action_projections.get(h, {}).get("net_usd", 0) for h in horizons]
        color = ACTION_COLORS.get(action, "#9E9E9E")
        ax.plot(x, nets, marker="o", color=color, linewidth=1.5, alpha=0.8,
                label=f"{dp['device_id']} ({action})")

    ax.axhline(y=0, color="#F44336", linestyle="--", linewidth=1, alpha=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(horizons)
    ax.set_xlabel("Projection Horizon")
    ax.set_ylabel("Cumulative Net (USD)")
    ax.set_title("ROI Projection �� Net USD by Horizon per Device")
    ax.legend(bbox_to_anchor=(1.02, 1), loc="upper left", fontsize=7)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig_to_base64(fig)


def _build_economic_section(cost_data: dict, charts: dict) -> str:
    """Build the Economic Analysis HTML section. Returns empty string if no cost data."""
    if not cost_data:
        return ""

    projections = cost_data.get("device_projections", [])
    if not projections:
        return ""

    fleet_hourly = cost_data.get("fleet_hourly_profit_usd", 0)
    fleet_daily = cost_data.get("fleet_daily_profit_usd", 0)
    btc_price = cost_data.get("btc_price_usd", 0)
    rev_per_th = cost_data.get("revenue_per_th_hr_usd", 0)
    neg_profit = cost_data.get("devices_with_negative_profit", 0)
    trend_available = cost_data.get("trend_analysis_available", False)

    cost_table_rows = ""
    for dp in projections:
        action = dp.get("optimal", {}).get("recommended_action", "do_nothing")
        horizon = dp.get("optimal", {}).get("horizon", "?")
        net = dp.get("optimal", {}).get("net_usd", 0)
        hourly = dp.get("hourly_profit_usd", 0)
        p_fail = dp.get("optimal", {}).get("p_failure", 0)
        profit_color = "#4CAF50" if hourly >= 0 else "#F44336"
        net_color = "#4CAF50" if net >= 0 else "#F44336"
        action_display = action.replace("_", " ").title()
        cost_table_rows += f"""
        <tr>
            <td>{dp['device_id']}</td>
            <td>{dp.get('model', '')}</td>
            <td style="color:{profit_color};font-weight:bold">${hourly:.2f}</td>
            <td>{action_display}</td>
            <td>{horizon}</td>
            <td style="color:{net_color};font-weight:bold">${net:+,.0f}</td>
            <td>{p_fail:.1%}</td>
        </tr>"""

    econ_summary_chart = charts.get("economic_summary", "")
    device_breakdown_chart = charts.get("device_cost_breakdown", "")
    roi_chart = charts.get("roi_projection", "")
    trend_note = ("Failure probabilities adjusted by trend analysis."
                  if trend_available
                  else "Using risk-only Weibull failure model (trend analysis not available).")

    return f"""
    <h2>Economic Analysis</h2>
    <p>Cost-driven optimization evaluating 6 actions &times; 3 horizons per device.
       BTC price: ${btc_price:,.0f} | Revenue: ${rev_per_th:.6f}/TH/hr | {trend_note}</p>

    <div>
        <div class="metric">
            <div class="value">${fleet_hourly:,.2f}/hr</div>
            <div class="label">Fleet Hourly Profit</div>
        </div>
        <div class="metric">
            <div class="value">${fleet_daily:,.0f}/day</div>
            <div class="label">Fleet Daily Profit</div>
        </div>
        <div class="metric">
            <div class="value">${btc_price:,.0f}</div>
            <div class="label">BTC Price</div>
        </div>
        <div class="metric">
            <div class="value">${rev_per_th:.4f}</div>
            <div class="label">Revenue per TH/hr</div>
        </div>
        <div class="metric {"critical" if neg_profit > 0 else ""}">
            <div class="value">{neg_profit}</div>
            <div class="label">Unprofitable Devices</div>
        </div>
    </div>

    {"" if not econ_summary_chart else f'<div class="chart"><img src="data:image/png;base64,{econ_summary_chart}" /></div>'}

    {"" if not device_breakdown_chart else f'<div class="chart"><img src="data:image/png;base64,{device_breakdown_chart}" /></div>'}

    {"" if not roi_chart else f'<div class="chart"><img src="data:image/png;base64,{roi_chart}" /></div>'}

    <h3>Per-Device Cost Projections</h3>
    <table>
        <tr><th>Device</th><th>Model</th><th>Hourly Profit</th><th>Recommended Action</th><th>Horizon</th><th>Net USD</th><th>P(Failure)</th></tr>
        {cost_table_rows}
    </table>
    """


def build_agent_actions_html(agent_actions: list) -> str:
    """Build the Agent Action Log section HTML.

    Reads from agent_actions.json (append-only log written by control_action.py).
    Backward-compatible �� report works identically without agent involvement.
    """
    if not agent_actions:
        return """
    <h2>Agent Action Log</h2>
    <p style="color:#999; font-style:italic;">No agent actions recorded for this pipeline run.</p>
    """

    rows = ""
    for entry in agent_actions:
        params_str = ", ".join(f"{k}={v}" for k, v in entry.get("params", {}).items())
        impact = entry.get("fleet_impact", {})
        impact_str = ""
        if "post_hashrate_pct" in impact:
            impact_str = f"Fleet HR: {impact.get('pre_hashrate_pct', '?')}% &rarr; {impact['post_hashrate_pct']}%"
        elif "post_offline" in impact:
            impact_str = f"Offline: {impact.get('post_offline', '?')}/{impact.get('fleet_size', '?')}"

        result_color = "#4CAF50" if entry.get("result") == "executed" else "#F44336"
        rows += f"""
        <tr>
            <td style="font-size:11px">{entry.get('timestamp', '')[:19]}</td>
            <td><strong>{entry.get('action', '')}</strong></td>
            <td>{entry.get('device_id', '')}</td>
            <td style="font-size:11px">{params_str}</td>
            <td style="color:{result_color};font-weight:bold">{entry.get('result', '')}</td>
            <td style="font-size:11px">{impact_str}</td>
            <td style="font-size:11px">{entry.get('reason', '')}</td>
        </tr>"""

    return f"""
    <h2>Agent Action Log</h2>
    <p>Actions proposed by the AI agent through the validated execution pipeline.
       Each action passed catalog validation, rate limiting, learned policy checks,
       and approval gates before execution.</p>
    <table>
        <tr><th>Time</th><th>Action</th><th>Device</th><th>Parameters</th><th>Result</th><th>Fleet Impact</th><th>Reason</th></tr>
        {rows}
    </table>
    """


def build_html(charts: dict, risk_scores: dict, metrics: dict,
               actions_data: dict, meta: dict, summary: dict,
               agent_actions: list = None, cost_data: dict = None) -> str:
    """Assemble HTML report."""

    # Agent action log section (backward-compatible — empty if no agent actions)
    agent_log_html = build_agent_actions_html(agent_actions or [])

    # Economic analysis section (Phase 4 — conditional on cost_projections.json)
    economic_section_html = _build_economic_section(cost_data, charts) if cost_data else ""

    # Risk table
    risks_html = ""
    for r in risk_scores["device_risks"]:
        flag = '<span style="color:#F44336;font-weight:bold">FLAGGED</span>' if r["flagged"] else ""
        risks_html += f"""
        <tr>
            <td>{r['device_id']}</td>
            <td>{r.get('model', '')}</td>
            <td>{r['mean_risk']:.3f}</td>
            <td>{r['max_risk']:.3f}</td>
            <td>{r['pct_flagged']:.1%}</td>
            <td>{flag}</td>
        </tr>"""

    # Controller actions table — includes MOS alert codes (Gap 3)
    actions_html = ""
    for a in actions_data["actions"]:
        tier_color = TIER_COLORS.get(a["tier"], "#666")
        tier_badge = f'<span style="color:{tier_color};font-weight:bold">{a["tier"]}</span>'
        cmds = ", ".join(c["type"] for c in a["commands"])
        # MOS method annotations — show the MOS RPC method for each command
        mos_methods = set()
        for c in a["commands"]:
            m = c.get("mos_method")
            if m:
                mos_methods.add(m)
        mos_methods_str = ", ".join(sorted(mos_methods)) if mos_methods else '<span style="color:#999">—</span>'
        # MOS alert codes
        mos_codes = a.get("mos_alert_codes", [])
        mos_codes_str = ", ".join(mos_codes) if mos_codes else '<span style="color:#999">—</span>'
        rationale = "<br>".join(a["rationale"])
        actions_html += f"""
        <tr>
            <td>{a['device_id']}</td>
            <td>{a.get('model', '')}</td>
            <td>{tier_badge}</td>
            <td>{a['risk_score']:.3f}</td>
            <td>{a['te_score']:.3f}</td>
            <td style="font-size:11px">{cmds}</td>
            <td style="font-size:11px">{mos_methods_str}</td>
            <td style="font-size:11px">{mos_codes_str}</td>
            <td style="font-size:11px">{rationale}</td>
        </tr>"""

    # Per-anomaly-type table
    per_anomaly_html = ""
    per_anomaly = metrics.get("per_anomaly_type", {})
    for atype, info in per_anomaly.items():
        if info.get("skipped"):
            per_anomaly_html += f"<tr><td>{atype}</td><td colspan='4'>Skipped</td></tr>"
        else:
            top_feat = info["top_features"][0]["feature"] if info.get("top_features") else "N/A"
            per_anomaly_html += (
                f"<tr><td>{atype}</td><td>{info['f1_score']:.1%}</td>"
                f"<td>{info['accuracy']:.1%}</td>"
                f"<td>{info['test_positives']}</td>"
                f"<td>{top_feat}</td></tr>"
            )

    # Safety constraints
    safety_html = ", ".join(actions_data.get("safety_constraints_applied", [])) or "None triggered"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>MDK Fleet Intelligence Report</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               max-width: 1200px; margin: 0 auto; padding: 20px; background: #fafafa; }}
        h1 {{ color: #1a1a1a; border-bottom: 2px solid #2196F3; padding-bottom: 10px; }}
        h2 {{ color: #333; margin-top: 40px; }}
        .banner {{ background: #E3F2FD; border-left: 4px solid #2196F3; padding: 12px 16px;
                   margin: 15px 0; font-size: 13px; color: #1565C0; font-family: monospace; }}
        .metric {{ display: inline-block; background: white; padding: 15px 25px;
                   margin: 5px; border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
        .metric .value {{ font-size: 24px; font-weight: bold; color: #2196F3; }}
        .metric .label {{ font-size: 12px; color: #666; margin-top: 4px; }}
        .metric.critical .value {{ color: #F44336; }}
        .metric.warning .value {{ color: #FF9800; }}
        .chart {{ background: white; padding: 15px; border-radius: 8px;
                  box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 15px 0; }}
        .chart img {{ width: 100%; }}
        table {{ border-collapse: collapse; width: 100%; background: white;
                 border-radius: 8px; overflow: hidden;
                 box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin: 10px 0; }}
        th, td {{ padding: 10px 15px; text-align: left; border-bottom: 1px solid #eee; }}
        th {{ background: #2196F3; color: white; }}
        .footer {{ margin-top: 40px; padding: 15px; color: #999; font-size: 12px;
                   border-top: 1px solid #ddd; }}
    </style>
</head>
<body>
    <h1>MDK Fleet Intelligence Report</h1>
    <p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')} |
       Data window: {summary['data_start']} &mdash; {summary['data_end']}</p>

    <div class="banner">
        Telemetry &rarr; Features &rarr; KPI &rarr; Model &rarr; Scoring &rarr; Trends &rarr; Controller &rarr; Commands
    </div>

    <div>
        <div class="metric">
            <div class="value">{summary['mean_te']:.1f} J/TH</div>
            <div class="label">Mean True Efficiency</div>
        </div>
        <div class="metric">
            <div class="value">{summary['device_count']}</div>
            <div class="label">Active Devices</div>
        </div>
        <div class="metric">
            <div class="value">{metrics['f1_score']:.1%}</div>
            <div class="label">Anomaly Detection F1</div>
        </div>
        <div class="metric critical">
            <div class="value">{summary['tier_counts'].get('CRITICAL', 0)}</div>
            <div class="label">Critical Devices</div>
        </div>
        <div class="metric warning">
            <div class="value">{summary['tier_counts'].get('WARNING', 0)}</div>
            <div class="label">Warning Devices</div>
        </div>
        <div class="metric">
            <div class="value">{summary['worst_device']}</div>
            <div class="label">Worst Health Score</div>
        </div>
    </div>

    <h2>Controller Actions</h2>
    <p>Tier-based controller ({actions_data['controller_version']}).
       Safety constraints applied: {safety_html}.</p>
    <div class="chart"><img src="data:image/png;base64,{charts['controller_tiers']}" /></div>

    <table>
        <tr><th>Device</th><th>Model</th><th>Tier</th><th>Risk</th><th>TE Score</th><th>Commands</th><th>MOS Methods</th><th>MOS Codes</th><th>Rationale</th></tr>
        {actions_html}
    </table>

    <div style="background: #FFF3E0; border-left: 4px solid #FF9800; padding: 12px 16px;
                margin: 15px 0; font-size: 13px; color: #E65100;">
        <strong>Production Safety Note:</strong> In a live MOS deployment, controller commands pass through
        the orchestrator's <strong>multi-voter approval system</strong> before execution
        (<code>reqVotesPos: 2</code>, <code>reqVotesNeg: 1</code>). Two positive votes are required to approve
        any write operation; a single negative vote cancels it. The commands shown here represent
        <em>recommendations</em> that would enter this approval queue — they do not execute immediately.
    </div>

    <h3>MOS Alert Code Reference</h3>
    <table>
        <tr><th>Code</th><th>Description</th><th>Severity</th></tr>
        <tr><td>P:1</td><td>High temperature protection triggered</td><td>Critical</td></tr>
        <tr><td>P:2</td><td>Low temperature protection triggered</td><td>Critical</td></tr>
        <tr><td>R:1</td><td>Low hashrate</td><td>High</td></tr>
        <tr><td>V:1</td><td>Power initialization error</td><td>Critical</td></tr>
        <tr><td>V:2</td><td>PSU not calibrated</td><td>High</td></tr>
        <tr><td>J0:8</td><td>Insufficient hashboards</td><td>Critical</td></tr>
        <tr><td>L0:1</td><td>Voltage/frequency exceeds limit</td><td>Critical</td></tr>
        <tr><td>L0:2</td><td>Voltage/frequency mismatch</td><td>High</td></tr>
        <tr><td>J0:2</td><td>Chip insufficiency</td><td>High</td></tr>
        <tr><td>J0:6</td><td>Temperature sensor error</td><td>High</td></tr>
    </table>

    {agent_log_html}

    {economic_section_html}

    {charts.get('trend_section', '')}

    <h2>True Efficiency Over Time</h2>
    <div class="chart"><img src="data:image/png;base64,{charts['te_timeseries']}" /></div>

    <h2>TE Decomposition by Device</h2>
    <p>Breakdown into hardware baseline (TE_base), voltage penalty, and cooling overhead.</p>
    <div class="chart"><img src="data:image/png;base64,{charts['te_decomposition']}" /></div>

    <p style="font-size: 12px; color: #666; margin-top: 5px; font-style: italic;">
        <strong>Unit note:</strong> This report uses J/TH (joules per terahash). The MOS platform convention is
        W/TH/s (watts per terahash per second). These are <strong>equivalent units</strong>:
        1 J/TH = 1 W&middot;s/TH = 1 W/TH/s. For example, our 15 J/TH = MOS's 15 W/TH/s.
    </p>

    <h2>Device Health Score</h2>
    <p>TE_score over time. Green = nominal (1.0), red = degraded (&lt;0.8).</p>
    <div class="chart"><img src="data:image/png;base64,{charts['health_scores']}" /></div>

    <h2>Failure Risk Ranking</h2>
    <div class="chart"><img src="data:image/png;base64,{charts['risk_ranking']}" /></div>

    <table>
        <tr><th>Device</th><th>Model</th><th>Mean Risk</th><th>Max Risk</th><th>% Flagged</th><th>Status</th></tr>
        {risks_html}
    </table>

    {charts.get('prediction_section', '')}

    <h2>Anomaly Timeline (Ground Truth)</h2>
    <p>Injected anomaly patterns showing onset and ramp-up. Used as training labels.</p>
    <div class="chart"><img src="data:image/png;base64,{charts['anomaly_timeline']}" /></div>

    {"" if not charts.get('feature_importance') else f'''<h2>Top Predictive Features</h2>
    <div class="chart"><img src="data:image/png;base64,{charts['feature_importance']}" /></div>'''}

    {"" if not per_anomaly_html else f'''<h2>Per-Anomaly-Type Detection</h2>
    <table>
        <tr><th>Anomaly Type</th><th>F1 Score</th><th>Accuracy</th><th>Test Positives</th><th>Top Feature</th></tr>
        {per_anomaly_html}
    </table>'''}

    <div class="footer">
        Model: {metrics['model']}
        {f"| Train: {metrics['train_samples']:,} samples | Test: {metrics['test_samples']:,} samples" if metrics.get('train_samples') else ""}
        {f"| Accuracy: {metrics['accuracy']:.1%} | F1: {metrics['f1_score']:.1%}" if metrics.get('f1_score') else ""} |
        Controller: {actions_data['controller_version']} |
        Workflow: mdk.fleet_intelligence
    </div>
</body>
</html>"""


def main():
    # ── Load ─────────────────────────────────────────────────────────────
    df = pd.read_parquet("kpi_timeseries.parquet")
    with open("fleet_risk_scores.json") as f:
        risk_scores = json.load(f)
    # In inference mode, model_metrics.json may not exist (model was pre-trained).
    # Provide sensible defaults so the report renders without a training step.
    try:
        with open("model_metrics.json") as f:
            metrics = json.load(f)
    except FileNotFoundError:
        metrics = {
            "model": "XGBoost (pre-trained)",
            "accuracy": 0.0,
            "f1_score": 0.0,
            "train_samples": 0,
            "test_samples": 0,
            "top_features": [],
            "per_anomaly_type": {},
            "threshold": 0.5,
        }
    with open("fleet_actions.json") as f:
        actions_data = json.load(f)
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

    # Agent action log (Phase 6) — optional, backward-compatible
    agent_actions = []
    try:
        with open("agent_actions.json") as f:
            agent_actions = json.load(f)
    except FileNotFoundError:
        pass

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    # ── Summary ──────────────────────────────────────────────────────────
    active = df[df["true_efficiency"].notna()]
    summary = {
        "mean_te": float(active["true_efficiency"].mean()),
        "device_count": df["device_id"].nunique(),
        "worst_device": active.groupby("device_id")["te_score"].mean().idxmin(),
        "data_start": df["timestamp"].min().strftime("%Y-%m-%d"),
        "data_end": df["timestamp"].max().strftime("%Y-%m-%d"),
        "tier_counts": actions_data.get("tier_counts", {}),
    }

    # ── Generate charts ──────────────────────────────────────────────────
    print("Generating charts...")
    charts = {
        "te_timeseries": plot_te_timeseries(df),
        "te_decomposition": plot_te_decomposition(df),
        "health_scores": plot_health_scores(df),
        "anomaly_timeline": plot_anomaly_timeline(df),
        "risk_ranking": plot_risk_ranking(risk_scores),
        "controller_tiers": plot_controller_tiers(actions_data),
        "feature_importance": plot_feature_importance(metrics),
    }

    # ── Phase 5: Prediction charts (conditional) ─────────────────────────
    has_predictions = any(
        "predictions" in d for d in risk_scores.get("device_risks", [])
    )
    if has_predictions:
        print("Generating prediction charts (Phase 5)...")
        fan_chart = plot_prediction_fan_chart(risk_scores)
        calibration_chart = plot_calibration_diagram(metrics)
        comparison_chart = plot_model_comparison(metrics)

        # Build the prediction HTML section
        pred_html_parts = ['<h2>Predictive Model — Multi-Horizon TE Forecast</h2>']
        pred_html_parts.append(
            '<p>Quantile regression predicting TE_score at +1h, +6h, +24h, +7d '
            'with 80% prediction intervals (p10–p90). Shaded regions show '
            'uncertainty bounds.</p>'
        )

        if fan_chart:
            pred_html_parts.append(
                f'<div class="chart"><img src="data:image/png;base64,{fan_chart}" /></div>'
            )

        # Predictions table for top devices
        pred_table = _build_predictions_table(risk_scores)
        if pred_table:
            pred_html_parts.append(pred_table)

        if calibration_chart:
            pred_html_parts.append('<h3>Prediction Interval Calibration</h3>')
            pred_html_parts.append(
                '<p>80% interval coverage per horizon. Target: 75–85% of actuals '
                'fall within [p10, p90].</p>'
            )
            pred_html_parts.append(
                f'<div class="chart"><img src="data:image/png;base64,{calibration_chart}" /></div>'
            )

        if comparison_chart:
            pred_html_parts.append('<h3>Model Performance Comparison</h3>')
            pred_html_parts.append(
                f'<div class="chart"><img src="data:image/png;base64,{comparison_chart}" /></div>'
            )

        # Model version info
        model_versions = risk_scores.get("model_versions", {})
        if model_versions:
            reg_version = model_versions.get("regressor_version", "?")
            pred_html_parts.append(
                f'<p style="font-size: 12px; color: #666;">Regression model version: '
                f'v{reg_version}</p>'
            )

        charts["prediction_section"] = "\n    ".join(pred_html_parts)
    else:
        charts["prediction_section"] = (
            '<h2>Predictive Model</h2>'
            '<p style="color: #999;">Regression model not available. '
            'Run train_model.py to generate multi-horizon predictions.</p>'
        )

    # ── Phase 3: Trend analysis charts (conditional) ────────────────────
    try:
        with open("trend_analysis.json") as f:
            trend_data = json.load(f)
        print("Generating trend analysis charts (Phase 3)...")
        charts["te_trajectory"] = plot_te_trajectory(df, trend_data)
        charts["trend_heatmap"] = plot_trend_heatmap(trend_data)
        charts["trend_section"] = build_trend_section(trend_data, charts)
    except FileNotFoundError:
        print("No trend_analysis.json — skipping trend charts")

    # ── Phase 4: Economic analysis charts (conditional) ───────────────────
    cost_data = None
    if os.path.exists("cost_projections.json"):
        with open("cost_projections.json") as f:
            cost_data = json.load(f)
        print("Generating economic analysis charts (Phase 4)...")
        charts["economic_summary"] = plot_economic_summary(cost_data)
        charts["device_cost_breakdown"] = plot_device_cost_breakdown(cost_data)
        charts["roi_projection"] = plot_roi_projection(cost_data)
    else:
        print("No cost_projections.json — skipping economic charts")

    # ── Build HTML ───────────────────────────────────────────────────────
    html = build_html(charts, risk_scores, metrics, actions_data, meta, summary,
                      agent_actions=agent_actions, cost_data=cost_data)

    with open("report.html", "w") as f:
        f.write(html)

    print(f"Report generated: report.html ({len(html):,} bytes)")

    with open("_validance_vars.json", "w") as f:
        json.dump({}, f)


if __name__ == "__main__":
    main()
