from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def bucketize(df: pd.DataFrame) -> pd.Series:
    bucket = pd.Series("", index=df.index, dtype=object)

    bucket[
        (df["structure"] == "BEAR_CALL_CREDIT_SPREAD")
        & (
            ((df["regime"] == "bear") & df["mispricing_type"].isin(["none", "overpriced_variance"]))
            | ((df["regime"] == "high_vol") & (df["mispricing_type"] == "overpriced_variance"))
        )
    ] = "premium_bear_call"

    bucket[
        (df["structure"] == "BULL_PUT_CREDIT_SPREAD")
        & (df["regime"] == "bull")
        & df["mispricing_type"].isin(["none", "overpriced_variance"])
    ] = "premium_bull_put"

    bucket[
        df["structure"].isin(["IRON_CONDOR", "IRON_BUTTERFLY"])
        & df["regime"].isin(["range", "high_vol", "bull"])
        & (df["mispricing_type"] == "overpriced_variance")
    ] = "premium_short_range"

    bucket[
        df["structure"].isin(
            ["LONG_STRADDLE", "LONG_STRANGLE", "REVERSE_IRON_BUTTERFLY", "REVERSE_IRON_CONDOR"]
        )
        & df["regime"].isin(["jump", "correlation_shock", "high_vol"])
        & df["mispricing_type"].isin(["none", "underpriced_variance"])
    ] = "long_vol"

    bucket[
        df["structure"].isin(["LONG_PUT", "PUT_DEBIT_VERTICAL", "PUT_BROKEN_WING_BUTTERFLY"])
        & (
            ((df["regime"] == "bear") & df["mispricing_type"].isin(["none", "underpriced_variance", "overpriced_variance"]))
            | ((df["regime"] == "jump") & (df["mispricing_type"] == "none"))
            | ((df["regime"] == "correlation_shock") & df["mispricing_type"].isin(["none", "underpriced_variance"]))
            | ((df["regime"] == "high_vol") & (df["mispricing_type"] == "none"))
        )
    ] = "bearish_debit"

    bucket[
        df["structure"].isin(["LONG_CALL", "CALL_DEBIT_VERTICAL", "CALL_BROKEN_WING_BUTTERFLY"])
        & (
            ((df["regime"] == "bull") & df["mispricing_type"].isin(["none", "underpriced_variance"]))
            | ((df["regime"] == "correlation_shock") & (df["mispricing_type"] == "none"))
        )
    ] = "bullish_debit"

    bucket[
        ((df["structure"] == "CALL_BACKSPREAD") & (df["regime"] == "correlation_shock") & (df["mispricing_type"] == "none"))
        | (
            (df["structure"] == "PUT_BACKSPREAD")
            & (
                ((df["regime"] == "jump") & (df["mispricing_type"] == "none"))
                | ((df["regime"] == "correlation_shock") & (df["mispricing_type"] == "underpriced_variance"))
            )
        )
    ] = "tail_backspread"

    return bucket


def promotion_table(training: pd.DataFrame) -> pd.DataFrame:
    frame = training.copy()
    frame["bucket"] = bucketize(frame)
    frame = frame[frame["bucket"] != ""].copy()
    frame["return_on_risk"] = frame["pnl"] / frame["max_loss"].clip(lower=1.0)

    table = (
        frame.groupby("bucket", observed=False)
        .agg(
            trades=("pnl", "size"),
            net_pnl=("pnl", "sum"),
            average_pnl=("pnl", "mean"),
            win_rate=("profitable", "mean"),
            mean_return_on_risk=("return_on_risk", "mean"),
            median_return_on_risk=("return_on_risk", "median"),
            q10_return_on_risk=("return_on_risk", lambda x: x.quantile(0.10)),
        )
        .reset_index()
    )
    sample_weight = table["trades"] / (table["trades"] + 20.0)
    tail_penalty = 0.04 * np.maximum(-table["q10_return_on_risk"] - 1.0, 0.0)
    table["promotion_score"] = (
        sample_weight * table["mean_return_on_risk"]
        + 0.12 * table["median_return_on_risk"]
        - tail_penalty
    )
    table["promoted"] = (
        (table["trades"] >= 10)
        & (table["net_pnl"] > 0.0)
        & (table["average_pnl"] > 0.0)
        & (table["promotion_score"] > 0.0)
    )
    table["sleeve"] = np.where(
        table["bucket"].str.startswith("premium_"),
        "premium_selling",
        "convex_directional",
    )
    return table.sort_values("promotion_score", ascending=False).reset_index(drop=True)


def select_promoted_portfolio(
    candidates: pd.DataFrame,
    promotions: pd.DataFrame,
    *,
    risk_cap: float,
) -> pd.DataFrame:
    frame = candidates.copy()
    frame["bucket"] = bucketize(frame)
    promoted = promotions[promotions["promoted"]][["bucket", "promotion_score", "sleeve"]]
    frame = frame.merge(promoted, on="bucket", how="inner")
    if frame.empty:
        return frame

    frame["allocation_score"] = (
        frame["promotion_score"]
        + 0.035 * frame["selection_score"]
        + 0.020 * frame["predicted_probability_profit"]
        - 0.015 * (frame["max_loss"] / 600.0)
    )

    selected_rows: list[pd.Series] = []
    for _, world_candidates in frame.groupby("world", sort=True):
        picks: list[pd.Series] = []
        for sleeve in ("premium_selling", "convex_directional"):
            sleeve_candidates = world_candidates[world_candidates["sleeve"] == sleeve]
            if not sleeve_candidates.empty:
                picks.append(sleeve_candidates.sort_values("allocation_score", ascending=False).iloc[0])

        if len(picks) == 2 and sum(float(row["max_loss"]) for row in picks) > risk_cap:
            picks = [
                max(
                    picks,
                    key=lambda row: float(row["allocation_score"]) / max(float(row["max_loss"]), 1.0),
                )
            ]
        selected_rows.extend(picks)

    return pd.DataFrame(selected_rows).reset_index(drop=True)


def portfolio_summary(selected: pd.DataFrame, holdout_worlds: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    world_index = sorted(holdout_worlds["world"].unique())
    world_pnl = (
        selected.groupby("world")["pnl"].sum().reindex(world_index, fill_value=0.0)
        if not selected.empty
        else pd.Series(0.0, index=world_index)
    )
    equity = world_pnl.cumsum()
    drawdown = equity - equity.cummax()
    trade_pnl = selected["pnl"].to_numpy(dtype=float) if not selected.empty else np.array([])
    gross_profit = float(trade_pnl[trade_pnl > 0].sum()) if len(trade_pnl) else 0.0
    gross_loss = float(-trade_pnl[trade_pnl < 0].sum()) if len(trade_pnl) else 0.0

    world_frame = pd.DataFrame(
        {
            "world": world_index,
            "portfolio_pnl": world_pnl.to_numpy(dtype=float),
            "cumulative_pnl": equity.to_numpy(dtype=float),
            "drawdown": drawdown.to_numpy(dtype=float),
        }
    )
    summary = {
        "holdout_worlds": int(len(world_index)),
        "selected_trades": int(len(selected)),
        "worlds_traded": int(selected["world"].nunique()) if not selected.empty else 0,
        "trade_rate": float(selected["world"].nunique() / max(len(world_index), 1)) if not selected.empty else 0.0,
        "net_pnl": float(world_pnl.sum()),
        "average_pnl_per_trade": float(trade_pnl.mean()) if len(trade_pnl) else 0.0,
        "median_pnl_per_trade": float(np.median(trade_pnl)) if len(trade_pnl) else 0.0,
        "trade_win_rate": float((trade_pnl > 0).mean()) if len(trade_pnl) else 0.0,
        "positive_world_rate": float((world_pnl > 0).mean()),
        "profit_factor": gross_profit / gross_loss if gross_loss > 0 else (math.inf if gross_profit > 0 else 0.0),
        "maximum_drawdown": float(drawdown.min()),
        "worst_world": float(world_pnl.min()),
        "best_world": float(world_pnl.max()),
        "maximum_observed_risk": float(selected.groupby("world")["max_loss"].sum().max()) if not selected.empty else 0.0,
        "premium_selling_trades": int((selected["sleeve"] == "premium_selling").sum()) if not selected.empty else 0,
        "premium_selling_pnl": float(selected.loc[selected["sleeve"] == "premium_selling", "pnl"].sum()) if not selected.empty else 0.0,
        "convex_directional_trades": int((selected["sleeve"] == "convex_directional").sum()) if not selected.empty else 0,
        "convex_directional_pnl": float(selected.loc[selected["sleeve"] == "convex_directional", "pnl"].sum()) if not selected.empty else 0.0,
    }
    return summary, world_frame


def aggregate_metrics(frame: pd.DataFrame, group_columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    return (
        frame.groupby(group_columns, observed=False)
        .agg(
            trades=("pnl", "size"),
            net_pnl=("pnl", "sum"),
            average_pnl=("pnl", "mean"),
            median_pnl=("pnl", "median"),
            win_rate=("profitable", "mean"),
            average_max_loss=("max_loss", "mean"),
        )
        .reset_index()
        .sort_values("net_pnl", ascending=False)
    )


def write_report(
    output_dir: Path,
    summary: dict,
    promotions: pd.DataFrame,
    sleeve_metrics: pd.DataFrame,
    strategy_metrics: pd.DataFrame,
    bucket_metrics: pd.DataFrame,
) -> None:
    lines = [
        "# Promoted Full-Strategy Holdout Rerun",
        "",
        "## Design",
        "",
        "- Worlds 0–499: discovery and promotion training",
        "- Worlds 500–999: untouched holdout evaluation",
        "- Same underlying one-minute world generator and seeds as the prior 1,000-world run",
        "- Forced liquidation at 3:55 PM; no overnight position and no stock assignment",
        "- One premium-selling sleeve and one convex/directional sleeve per world",
        "- $900 aggregate observed maximum-loss cap per world",
        "- Naked options, stock ownership, and unlimited-risk structures excluded",
        "",
        "## Holdout Result",
        "",
        f"- Selected trades: **{summary['selected_trades']}** across **{summary['worlds_traded']}** of {summary['holdout_worlds']} worlds",
        f"- Net P&L: **${summary['net_pnl']:,.2f}**",
        f"- Average / median trade: **${summary['average_pnl_per_trade']:,.2f} / ${summary['median_pnl_per_trade']:,.2f}**",
        f"- Trade win rate: **{summary['trade_win_rate']:.1%}**",
        f"- Positive-world rate: **{summary['positive_world_rate']:.1%}**",
        f"- Profit factor: **{summary['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${summary['maximum_drawdown']:,.2f}**",
        f"- Worst / best world: **${summary['worst_world']:,.2f} / ${summary['best_world']:,.2f}**",
        f"- Maximum observed aggregate risk: **${summary['maximum_observed_risk']:,.2f}**",
        "",
        "## Sleeve Contribution",
        "",
        f"- Premium selling: **{summary['premium_selling_trades']} trades, ${summary['premium_selling_pnl']:,.2f}**",
        f"- Convex/directional: **{summary['convex_directional_trades']} trades, ${summary['convex_directional_pnl']:,.2f}**",
        "",
        sleeve_metrics.to_markdown(index=False, floatfmt=".3f") if not sleeve_metrics.empty else "No sleeve results.",
        "",
        "## Promotion Table — Training Sample Only",
        "",
        promotions.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Holdout by Strategy",
        "",
        strategy_metrics.to_markdown(index=False, floatfmt=".3f") if not strategy_metrics.empty else "No strategy results.",
        "",
        "## Holdout by Promoted Bucket",
        "",
        bucket_metrics.to_markdown(index=False, floatfmt=".3f") if not bucket_metrics.empty else "No bucket results.",
        "",
        "## Interpretation",
        "",
        "Premium selling remained a positive contributor, but nearly all of that result came from directional defined-risk credit spreads. The generic short-range sleeve—iron condors and iron butterflies—was slightly negative in holdout. It stays in the research universe, but it should not receive equal capital until its range and tail filters improve.",
        "",
        "The convex/directional sleeve generated most of the profit and most of the tail dependence. This is still synthetic evidence. A historical replay must challenge the simulator's constituent-to-SPY volatility-update lag before any live deployment.",
    ]
    (output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote strategy buckets on training worlds and evaluate on holdout worlds.")
    parser.add_argument(
        "--trades",
        type=Path,
        default=ROOT / "reports" / "full_strategy_1000_combined" / "trades.csv",
    )
    parser.add_argument(
        "--worlds",
        type=Path,
        default=ROOT / "reports" / "full_strategy_1000_combined" / "worlds.csv",
    )
    parser.add_argument("--training-max-batch", type=int, default=2)
    parser.add_argument("--risk-cap", type=float, default=900.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "promoted_full_strategy_holdout",
    )
    args = parser.parse_args()

    trades = pd.read_csv(args.trades)
    worlds = pd.read_csv(args.worlds)
    training = trades[trades["batch_id"] <= args.training_max_batch].copy()
    holdout = trades[trades["batch_id"] > args.training_max_batch].copy()
    holdout_worlds = worlds[worlds["batch_id"] > args.training_max_batch].copy()

    promotions = promotion_table(training)
    selected = select_promoted_portfolio(holdout, promotions, risk_cap=args.risk_cap)
    summary, world_frame = portfolio_summary(selected, holdout_worlds)
    summary["training_worlds"] = int(worlds[worlds["batch_id"] <= args.training_max_batch]["world"].nunique())
    summary["risk_cap"] = float(args.risk_cap)
    summary["promoted_buckets"] = promotions.loc[promotions["promoted"], "bucket"].tolist()

    sleeve_metrics = aggregate_metrics(selected, ["sleeve"])
    strategy_metrics = aggregate_metrics(selected, ["sleeve", "structure"])
    bucket_metrics = aggregate_metrics(selected, ["sleeve", "bucket"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    promotions.to_csv(args.output_dir / "promotion_table.csv", index=False)
    selected.to_csv(args.output_dir / "selected_holdout_trades.csv", index=False)
    world_frame.to_csv(args.output_dir / "holdout_world_portfolio.csv", index=False)
    sleeve_metrics.to_csv(args.output_dir / "sleeve_metrics.csv", index=False)
    strategy_metrics.to_csv(args.output_dir / "strategy_metrics.csv", index=False)
    bucket_metrics.to_csv(args.output_dir / "bucket_metrics.csv", index=False)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report(args.output_dir, summary, promotions, sleeve_metrics, strategy_metrics, bucket_metrics)

    print(json.dumps(summary, indent=2))
    print(f"Saved promoted holdout results to {args.output_dir}")


if __name__ == "__main__":
    main()
