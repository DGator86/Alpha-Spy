from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from promote_full_strategy_tournament import (
    bucketize,
    portfolio_summary,
    select_promoted_portfolio,
)


def profit_factor(values: pd.Series) -> float:
    gains = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return gains / losses if losses > 0 else (math.inf if gains > 0 else 0.0)


def expected_calibration_error(frame: pd.DataFrame, bins: int = 10) -> tuple[float, pd.DataFrame]:
    if frame.empty:
        return 0.0, pd.DataFrame()
    work = frame[["predicted_probability_profit", "profitable"]].dropna().copy()
    edges = np.linspace(0.0, 1.0, bins + 1)
    work["bin"] = pd.cut(work["predicted_probability_profit"], edges, include_lowest=True)
    rows = []
    total = len(work)
    ece = 0.0
    for interval, group in work.groupby("bin", observed=False):
        if group.empty:
            continue
        pred = float(group["predicted_probability_profit"].mean())
        realized = float(group["profitable"].mean())
        weight = len(group) / total
        ece += weight * abs(pred - realized)
        rows.append({
            "probability_bin": str(interval),
            "trades": len(group),
            "mean_predicted_probability": pred,
            "realized_win_rate": realized,
            "absolute_gap": abs(pred - realized),
        })
    return float(ece), pd.DataFrame(rows)


def bootstrap_average_pnl(frame: pd.DataFrame, rng: np.random.Generator, draws: int = 10000) -> tuple[float, float]:
    if frame.empty:
        return 0.0, 0.0
    pnl = frame["pnl"].to_numpy(float)
    samples = rng.choice(pnl, size=(draws, len(pnl)), replace=True).mean(axis=1)
    return float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def group_metrics(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    rows = []
    for keys, group in frame.groupby(cols, observed=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(cols, keys, strict=True))
        row.update({
            "trades": len(group),
            "net_pnl": float(group["pnl"].sum()),
            "average_pnl": float(group["pnl"].mean()),
            "median_pnl": float(group["pnl"].median()),
            "win_rate": float(group["profitable"].mean()),
            "profit_factor": profit_factor(group["pnl"]),
            "average_max_loss": float(group["max_loss"].mean()),
            "average_return_on_risk": float((group["pnl"] / group["max_loss"].clip(lower=1.0)).mean()),
        })
        rows.append(row)
    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the frozen champion policy on entirely new worlds.")
    parser.add_argument(
        "--batch-root",
        type=Path,
        default=ROOT / "reports" / "champion_new_1000_batches",
    )
    parser.add_argument(
        "--promotions",
        type=Path,
        default=ROOT / "reports" / "champion_configuration_1000_replay" / "promotion_table.csv",
    )
    parser.add_argument(
        "--prior-summary",
        type=Path,
        default=ROOT / "reports" / "champion_configuration_1000_replay" / "summary.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "champion_new_1000_worlds",
    )
    parser.add_argument("--risk-cap", type=float, default=900.0)
    args = parser.parse_args()

    trade_frames = []
    world_frames = []
    for batch_dir in sorted(args.batch_root.glob("batch_*")):
        trade_frames.append(pd.read_csv(batch_dir / "trades.csv"))
        world_frames.append(pd.read_csv(batch_dir / "worlds.csv"))
    trades = pd.concat(trade_frames, ignore_index=True)
    worlds = pd.concat(world_frames, ignore_index=True).sort_values("world").reset_index(drop=True)
    promotions = pd.read_csv(args.promotions)

    # Frozen policy: saved bucket definitions, saved promotion flags/scores, unchanged sleeve allocation.
    selected = select_promoted_portfolio(trades, promotions, risk_cap=args.risk_cap)
    selected["return_on_risk"] = selected["pnl"] / selected["max_loss"].clip(lower=1.0)
    summary, world_portfolio = portfolio_summary(selected, worlds)

    rng = np.random.default_rng(20260814)
    ci_low, ci_high = bootstrap_average_pnl(selected, rng)
    ece, calibration = expected_calibration_error(selected)

    summary.update({
        "evaluation_status": "fully out-of-sample new-world replay",
        "new_worlds": int(worlds["world"].nunique()),
        "world_id_min": int(worlds["world"].min()),
        "world_id_max": int(worlds["world"].max()),
        "seeds": [20260810, 20260811, 20260812, 20260813],
        "simulated_minutes": int(worlds["world"].nunique() * 390),
        "risk_cap": float(args.risk_cap),
        "mean_predicted_probability": float(selected["predicted_probability_profit"].mean()) if not selected.empty else 0.0,
        "expected_calibration_error": ece,
        "average_trade_pnl_bootstrap_95pct": [ci_low, ci_high],
        "injected_edge_active_trades": int(selected["injected_edge_active"].sum()) if not selected.empty else 0,
        "injected_edge_active_pnl": float(selected.loc[selected["injected_edge_active"], "pnl"].sum()) if not selected.empty else 0.0,
        "non_active_pnl": float(selected.loc[~selected["injected_edge_active"], "pnl"].sum()) if not selected.empty else 0.0,
    })

    ordered = selected.sort_values("pnl", ascending=False)
    total_pnl = float(selected["pnl"].sum())
    summary["top_5_trade_pnl_share"] = float(ordered.head(5)["pnl"].sum() / total_pnl) if total_pnl else 0.0
    summary["top_10_trade_pnl_share"] = float(ordered.head(10)["pnl"].sum() / total_pnl) if total_pnl else 0.0
    summary["pnl_excluding_top_10_trades"] = float(total_pnl - ordered.head(10)["pnl"].sum())

    batch_metrics = group_metrics(selected, ["batch_id"])
    strategy_metrics = group_metrics(selected, ["sleeve", "structure"])
    sleeve_metrics = group_metrics(selected, ["sleeve"])
    regime_metrics = group_metrics(selected, ["regime"])
    selected["bucket"] = bucketize(selected)
    bucket_metrics = group_metrics(selected, ["sleeve", "bucket"])
    mispricing_metrics = group_metrics(selected, ["mispricing_type"])

    prior = json.loads(args.prior_summary.read_text(encoding="utf-8"))
    prior_full = prior.get("results", {}).get("full_1000", prior.get("full_replay", prior))
    comparison = pd.DataFrame([
        {
            "sample": "prior_1000_world_replay",
            "worlds": 1000,
            "selected_trades": prior_full.get("selected_trades", 586),
            "net_pnl": prior_full.get("net_pnl", 37091.77),
            "average_pnl_per_trade": prior_full.get("average_pnl_per_trade"),
            "median_pnl_per_trade": prior_full.get("median_pnl_per_trade"),
            "trade_win_rate": prior_full.get("trade_win_rate"),
            "profit_factor": prior_full.get("profit_factor"),
            "maximum_drawdown": prior_full.get("maximum_drawdown"),
            "premium_selling_pnl": prior_full.get("premium_selling_pnl"),
            "convex_directional_pnl": prior_full.get("convex_directional_pnl"),
        },
        {
            "sample": "new_1000_world_out_of_sample",
            "worlds": 1000,
            "selected_trades": summary["selected_trades"],
            "net_pnl": summary["net_pnl"],
            "average_pnl_per_trade": summary["average_pnl_per_trade"],
            "median_pnl_per_trade": summary["median_pnl_per_trade"],
            "trade_win_rate": summary["trade_win_rate"],
            "profit_factor": summary["profit_factor"],
            "maximum_drawdown": summary["maximum_drawdown"],
            "premium_selling_pnl": summary["premium_selling_pnl"],
            "convex_directional_pnl": summary["convex_directional_pnl"],
        },
    ])

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "all_strategy_candidates.csv", index=False)
    worlds.to_csv(output / "worlds.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    world_portfolio.to_csv(output / "world_portfolio.csv", index=False)
    promotions.to_csv(output / "frozen_promotion_table.csv", index=False)
    batch_metrics.to_csv(output / "batch_metrics.csv", index=False)
    strategy_metrics.to_csv(output / "strategy_metrics.csv", index=False)
    sleeve_metrics.to_csv(output / "sleeve_metrics.csv", index=False)
    regime_metrics.to_csv(output / "regime_metrics.csv", index=False)
    bucket_metrics.to_csv(output / "bucket_metrics.csv", index=False)
    mispricing_metrics.to_csv(output / "mispricing_metrics.csv", index=False)
    calibration.to_csv(output / "calibration.csv", index=False)
    comparison.to_csv(output / "prior_vs_new_comparison.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Frozen Champion — 1,000 New-World Evaluation",
        "",
        "## Test Integrity",
        "",
        "- 1,000 entirely new one-minute market worlds",
        "- Seeds 20260810–20260813; world IDs 1000–1999",
        "- Frozen champion bucket definitions, promotion scores, entry gates, sleeve allocation, and $900 risk cap",
        "- No tuning or selection using these worlds",
        "- Forced same-day liquidation; no stock ownership, naked options, unlimited risk, or overnight positions",
        "",
        "## Result",
        "",
        f"- Selected trades: **{summary['selected_trades']}** across **{summary['worlds_traded']}** of 1,000 worlds",
        f"- Net P&L: **${summary['net_pnl']:,.2f}**",
        f"- Average / median trade: **${summary['average_pnl_per_trade']:,.2f} / ${summary['median_pnl_per_trade']:,.2f}**",
        f"- Trade win rate: **{summary['trade_win_rate']:.1%}**",
        f"- Positive-world rate: **{summary['positive_world_rate']:.1%}**",
        f"- Profit factor: **{summary['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${summary['maximum_drawdown']:,.2f}**",
        f"- Worst / best world: **${summary['worst_world']:,.2f} / ${summary['best_world']:,.2f}**",
        f"- Maximum observed aggregate risk: **${summary['maximum_observed_risk']:,.2f}**",
        f"- 95% bootstrap interval for mean trade P&L: **${ci_low:,.2f} to ${ci_high:,.2f}**",
        "",
        "## Sleeve Contribution",
        "",
        f"- Premium selling: **{summary['premium_selling_trades']} trades, ${summary['premium_selling_pnl']:,.2f}**",
        f"- Convex/directional: **{summary['convex_directional_trades']} trades, ${summary['convex_directional_pnl']:,.2f}**",
        "",
        sleeve_metrics.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## By Strategy",
        "",
        strategy_metrics.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Batch Stability",
        "",
        batch_metrics.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Prior vs New Sample",
        "",
        comparison.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Calibration and Concentration",
        "",
        f"- Mean predicted probability: **{summary['mean_predicted_probability']:.1%}**",
        f"- Realized win rate: **{summary['trade_win_rate']:.1%}**",
        f"- Expected calibration error: **{ece:.1%}**",
        f"- Top-five trades as share of net P&L: **{summary['top_5_trade_pnl_share']:.1%}**",
        f"- Top-ten trades as share of net P&L: **{summary['top_10_trade_pnl_share']:.1%}**",
        f"- Net P&L excluding top ten trades: **${summary['pnl_excluding_top_10_trades']:,.2f}**",
        "",
        "## Interpretation",
        "",
        "This is the first fully new-world evaluation of the frozen champion. It is materially stronger evidence than replaying the original 1,000 worlds, but it remains synthetic evidence. The result still depends on the simulator's constituent-to-SPY information transmission and volatility-update assumptions, which must be replaced by synchronized historical market data before live deployment.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print("\nSTRATEGY METRICS\n", strategy_metrics.to_string(index=False))
    print(f"Saved to {output}")


if __name__ == "__main__":
    main()
