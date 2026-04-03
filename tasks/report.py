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
    """Top features from XGBoost model."""
    features = metrics["top_features"]
    names = [f["feature"] for f in features][::-1]
    values = [f["importance"] for f in features][::-1]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.barh(names, values, color="#2196F3")
    ax.set_xlabel("Importance")
    ax.set_title("Top Predictive Features (XGBoost)")
    ax.grid(True, alpha=0.3, axis="x")
    return fig_to_base64(fig)


def build_html(charts: dict, risk_scores: dict, metrics: dict,
               actions_data: dict, meta: dict, summary: dict) -> str:
    """Assemble HTML report."""

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
        Telemetry &rarr; Features &rarr; KPI &rarr; Model &rarr; Scoring &rarr; Controller &rarr; Commands
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

    <h2>Anomaly Timeline (Ground Truth)</h2>
    <p>Injected anomaly patterns showing onset and ramp-up. Used as training labels.</p>
    <div class="chart"><img src="data:image/png;base64,{charts['anomaly_timeline']}" /></div>

    <h2>Top Predictive Features</h2>
    <div class="chart"><img src="data:image/png;base64,{charts['feature_importance']}" /></div>

    <h2>Per-Anomaly-Type Detection</h2>
    <table>
        <tr><th>Anomaly Type</th><th>F1 Score</th><th>Accuracy</th><th>Test Positives</th><th>Top Feature</th></tr>
        {per_anomaly_html}
    </table>

    <div class="footer">
        Model: {metrics['model']} |
        Train: {metrics['train_samples']:,} samples |
        Test: {metrics['test_samples']:,} samples |
        Accuracy: {metrics['accuracy']:.1%} |
        F1: {metrics['f1_score']:.1%} |
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
    with open("model_metrics.json") as f:
        metrics = json.load(f)
    with open("fleet_actions.json") as f:
        actions_data = json.load(f)
    with open("fleet_metadata.json") as f:
        meta = json.load(f)

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

    # ── Build HTML ───────────────────────────────────────────────────────
    html = build_html(charts, risk_scores, metrics, actions_data, meta, summary)

    with open("report.html", "w") as f:
        f.write(html)

    print(f"Report generated: report.html ({len(html):,} bytes)")

    with open("_validance_vars.json", "w") as f:
        json.dump({}, f)


if __name__ == "__main__":
    main()
