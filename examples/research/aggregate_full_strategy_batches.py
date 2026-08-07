from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def aggregate_metrics(frame: pd.DataFrame, group: str) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    return (
        frame.groupby(group, observed=False)
        .agg(
            trades=("pnl", "size"),
            net_pnl=("pnl", "sum"),
            average_pnl=("pnl", "mean"),
            median_pnl=("pnl", "median"),
            win_rate=("profitable", "mean"),
            average_max_loss=("max_loss", "mean"),
            average_return_on_risk=("return_on_risk", "mean"),
        )
        .reset_index()
        .sort_values("net_pnl", ascending=False)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate full-strategy tournament batches.")
    parser.add_argument(
        "--batch-root",
        type=Path,
        default=ROOT / "reports" / "full_strategy_batches",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports" / "full_strategy_1000_combined",
    )
    args = parser.parse_args()

    batch_dirs = sorted(path for path in args.batch_root.glob("batch_*") if path.is_dir())
    if not batch_dirs:
        raise FileNotFoundError(f"No batch directories under {args.batch_root}")

    trades = pd.concat([pd.read_csv(path / "trades.csv") for path in batch_dirs], ignore_index=True)
    worlds = pd.concat([pd.read_csv(path / "worlds.csv") for path in batch_dirs], ignore_index=True)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    trades.to_csv(args.output_dir / "trades.csv", index=False)
    worlds.to_csv(args.output_dir / "worlds.csv", index=False)

    for group in ("structure", "family", "premium_style", "regime", "mispricing_type"):
        aggregate_metrics(trades, group).to_csv(args.output_dir / f"{group}_metrics.csv", index=False)

    summary = {
        "worlds": int(worlds["world"].nunique()),
        "independent_opportunities": len(trades),
        "worlds_with_opportunities": int(worlds["trade_taken"].sum()),
        "credit_share": float((trades["premium_style"] == "credit").mean()),
        "independent_aggregate_pnl": float(trades["pnl"].sum()),
        "average_pnl": float(trades["pnl"].mean()),
        "win_rate": float(trades["profitable"].mean()),
        "training_worlds": int(worlds.loc[worlds["batch_id"] <= 2, "world"].nunique()),
        "holdout_worlds": int(worlds.loc[worlds["batch_id"] > 2, "world"].nunique()),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
