from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from run_1000_world_1m_day import (  # noqa: E402
    build_summary,
    calibration_table,
    write_report,
)


def grouped_metrics(trades: pd.DataFrame, group: str, include_active: bool = False) -> pd.DataFrame:
    aggregations: dict[str, tuple[str, str]] = {
        "trades": ("pnl", "size"),
        "win_rate": ("profitable", "mean"),
        "net_pnl": ("pnl", "sum"),
        "average_pnl": ("pnl", "mean"),
        "average_edge_score": ("edge_score", "mean"),
    }
    if "max_loss" in trades.columns:
        aggregations["average_max_loss"] = ("max_loss", "mean")
    if include_active:
        aggregations["active_episode_entries"] = ("injected_edge_active", "sum")
    return (
        trades.groupby(group, observed=False)
        .agg(**aggregations)
        .reset_index()
        .sort_values("net_pnl", ascending=False)
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--seeds", nargs="+", type=int, required=True)
    args = parser.parse_args()

    batch_dirs = sorted(path for path in args.batch_root.iterdir() if path.is_dir())
    if len(batch_dirs) != len(args.seeds):
        raise ValueError(f"Found {len(batch_dirs)} batches but received {len(args.seeds)} seeds")

    worlds_parts: list[pd.DataFrame] = []
    trades_parts: list[pd.DataFrame] = []
    world_offset = 0
    for batch_number, (batch_dir, seed) in enumerate(zip(batch_dirs, args.seeds), start=1):
        worlds = pd.read_csv(batch_dir / "worlds.csv")
        trades_path = batch_dir / "trades.csv"
        trades = pd.read_csv(trades_path) if trades_path.stat().st_size > 1 else pd.DataFrame()
        worlds["local_world"] = worlds["world"]
        worlds["world"] = worlds["world"] + world_offset
        worlds["batch"] = batch_number
        worlds["seed"] = seed
        if not trades.empty:
            trades["local_world"] = trades["world"]
            trades["world"] = trades["world"] + world_offset
            trades["batch"] = batch_number
            trades["seed"] = seed
            trades_parts.append(trades)
        worlds_parts.append(worlds)
        world_offset += len(worlds)

    worlds_frame = pd.concat(worlds_parts, ignore_index=True)
    trades_frame = pd.concat(trades_parts, ignore_index=True) if trades_parts else pd.DataFrame()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    worlds_frame.to_csv(args.output_dir / "worlds.csv", index=False)
    trades_frame.to_csv(args.output_dir / "trades.csv", index=False)

    summary = build_summary(worlds_frame, trades_frame)
    summary["simulation"]["seed"] = None
    summary["simulation"]["batch_seeds"] = args.seeds
    summary["simulation"]["batches"] = len(batch_dirs)
    summary["simulation"]["policy_status"] = (
        "frozen after 200-world calibration sample with seed 20260805; "
        "evaluated on four independent 250-world batches"
    )
    if not trades_frame.empty:
        pnl_ranked = trades_frame["pnl"].sort_values(ascending=False)
        total_pnl = float(trades_frame["pnl"].sum())
        batch_metrics = (
            trades_frame.groupby("batch")
            .agg(trades=("pnl", "size"), net_pnl=("pnl", "sum"), win_rate=("profitable", "mean"))
            .reset_index()
            .to_dict(orient="records")
        )
        summary["robustness"] = {
            "positive_batches": int(sum(row["net_pnl"] > 0 for row in batch_metrics)),
            "total_batches": int(len(batch_metrics)),
            "batch_metrics": batch_metrics,
            "top_1_trade_pnl_share": float(pnl_ranked.head(1).sum() / total_pnl) if total_pnl else None,
            "top_5_trade_pnl_share": float(pnl_ranked.head(5).sum() / total_pnl) if total_pnl else None,
            "top_10_trade_pnl_share": float(pnl_ranked.head(10).sum() / total_pnl) if total_pnl else None,
            "net_pnl_excluding_top_5": float(total_pnl - pnl_ranked.head(5).sum()),
            "net_pnl_excluding_top_10": float(total_pnl - pnl_ranked.head(10).sum()),
            "unique_structures_traded": sorted(trades_frame["structure"].unique().tolist()),
        }
        summary["signal_diagnostics"] = {
            "spearman_predicted_probability_vs_pnl": float(
                trades_frame["predicted_probability_profit"].corr(trades_frame["pnl"], method="spearman")
            ),
            "spearman_predicted_probability_vs_outcome": float(
                trades_frame["predicted_probability_profit"].corr(
                    trades_frame["profitable"].astype(float), method="spearman"
                )
            ),
            "spearman_edge_score_vs_pnl": float(
                trades_frame["edge_score"].corr(trades_frame["pnl"], method="spearman")
            ),
            "active_episode_win_rate": float(
                trades_frame.loc[trades_frame["injected_edge_active"], "profitable"].mean()
            ) if trades_frame["injected_edge_active"].any() else None,
            "inactive_episode_win_rate": float(
                trades_frame.loc[~trades_frame["injected_edge_active"], "profitable"].mean()
            ),
        }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if trades_frame.empty:
        regime_metrics = pd.DataFrame()
        structure_metrics = pd.DataFrame()
        mispricing_metrics = pd.DataFrame()
    else:
        regime_metrics = grouped_metrics(trades_frame, "regime")
        structure_metrics = grouped_metrics(trades_frame, "structure")
        mispricing_metrics = grouped_metrics(trades_frame, "mispricing_type", include_active=True)

    regime_metrics.to_csv(args.output_dir / "regime_metrics.csv", index=False)
    structure_metrics.to_csv(args.output_dir / "structure_metrics.csv", index=False)
    mispricing_metrics.to_csv(args.output_dir / "mispricing_metrics.csv", index=False)
    calibration_table(trades_frame).to_csv(args.output_dir / "calibration.csv", index=False)
    write_report(args.output_dir, summary, regime_metrics, structure_metrics, mispricing_metrics)
    if "robustness" in summary:
        robust = summary["robustness"]
        signals = summary["signal_diagnostics"]
        with (args.output_dir / "REPORT.md").open("a", encoding="utf-8") as handle:
            handle.write("\n\n## Critical Robustness Diagnostics\n\n")
            handle.write(
                f"- Positive P&L batches: **{robust['positive_batches']} of {robust['total_batches']}**.\n"
            )
            handle.write(
                f"- Top 5 trades produced **{robust['top_5_trade_pnl_share']:.1%}** of total net P&L.\n"
            )
            handle.write(
                f"- Top 10 trades produced **{robust['top_10_trade_pnl_share']:.1%}** of total net P&L.\n"
            )
            handle.write(
                f"- Net P&L after removing the top 10 trades: **${robust['net_pnl_excluding_top_10']:,.2f}**.\n"
            )
            handle.write(
                f"- Structures actually traded: **{', '.join(robust['unique_structures_traded'])}**.\n"
            )
            handle.write(
                f"- Spearman correlation, predicted probability versus realized P&L: "
                f"**{signals['spearman_predicted_probability_vs_pnl']:.3f}**.\n"
            )
            handle.write(
                f"- Spearman correlation, predicted probability versus win/loss outcome: "
                f"**{signals['spearman_predicted_probability_vs_outcome']:.3f}**.\n"
            )
            handle.write("\nThe synthetic engine generated positive aggregate expectancy, but the ranking layer failed monotonicity: higher predicted probabilities did not correspond to better realized outcomes. P&L was also heavily concentrated in a small number of large straddle wins. These are blocking issues for live deployment.\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
