from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ELIGIBLE = ["BEAR_CALL_CREDIT_SPREAD", "BULL_PUT_CREDIT_SPREAD"]
NUMERIC_FEATURES = [
    "entry_minute", "minutes_remaining", "market_iv", "model_q_iv", "model_p_iv",
    "market_skew", "model_q_skew", "surface_vol_gap", "surface_skew_gap",
    "forecast_remaining_return", "breadth_5", "concentration_5", "credit",
    "credit_to_risk", "physical_ev_to_risk", "q_edge_to_risk", "iv_over_p",
    "iv_over_q", "direction_ratio", "breadth_extremity", "direction_confirmation",
    "predicted_probability_profit", "selection_score", "uncertainty",
    "market_model_skew_gap",
]
FEATURES = NUMERIC_FEATURES + ["structure"]


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    d = frame[frame["structure"].isin(ELIGIBLE)].copy()
    d["credit"] = -d["entry_cashflow"]
    max_loss_share = (d["max_loss"] / 100.0).clip(lower=1e-6)
    d["credit_to_risk"] = d["credit"] / max_loss_share
    d["physical_ev_to_risk"] = d["physical_expected_net"] / max_loss_share
    d["q_edge_to_risk"] = d["net_q_edge"] / max_loss_share
    d["iv_over_p"] = (d["market_iv"] - d["model_p_iv"]) / d["market_iv"].clip(lower=0.01)
    d["iv_over_q"] = (d["market_iv"] - d["model_q_iv"]) / d["market_iv"].clip(lower=0.01)
    physical_move = d["model_p_iv"] * np.sqrt(d["minutes_remaining"] / (252.0 * 390.0))
    d["direction_ratio"] = d["forecast_remaining_return"].abs() / physical_move.clip(lower=1e-6)
    d["breadth_extremity"] = 2.0 * (d["breadth_5"] - 0.5).abs()
    d["direction_confirmation"] = np.where(
        d["structure"].str.startswith("BULL"), d["breadth_5"] - 0.5, 0.5 - d["breadth_5"]
    )
    d["market_model_skew_gap"] = d["market_skew"] - d["model_q_skew"]
    return d


def make_models() -> tuple[Pipeline, Pipeline]:
    preprocessing = ColumnTransformer([
        ("num", Pipeline([
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]), NUMERIC_FEATURES),
        ("cat", OneHotEncoder(handle_unknown="ignore"), ["structure"]),
    ])
    classifier = Pipeline([
        ("pre", preprocessing),
        ("model", LogisticRegression(C=0.25, max_iter=3000)),
    ])
    regressor = Pipeline([
        ("pre", preprocessing),
        ("model", Ridge(alpha=25.0)),
    ])
    return classifier, regressor


def profit_factor(pnl: pd.Series) -> float:
    gross = float(pnl[pnl > 0].sum())
    loss = float(-pnl[pnl < 0].sum())
    return gross / loss if loss > 0 else (math.inf if gross > 0 else 0.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--training", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--worlds", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--probability-threshold", type=float, default=0.86)
    parser.add_argument("--expected-pnl-threshold", type=float, default=2.5)
    args = parser.parse_args()

    training = enrich(pd.read_csv(args.training))
    evaluation = enrich(pd.read_csv(args.evaluation))
    worlds = pd.read_csv(args.worlds)
    classifier, regressor = make_models()
    classifier.fit(training[FEATURES], training["profitable"].astype(int))
    regressor.fit(training[FEATURES], training["pnl"])

    evaluation["model_probability_win"] = classifier.predict_proba(evaluation[FEATURES])[:, 1]
    evaluation["model_expected_pnl"] = regressor.predict(evaluation[FEATURES])
    evaluation["policy_rank"] = (
        evaluation["model_expected_pnl"]
        + 30.0 * (evaluation["model_probability_win"] - 0.75)
        + 10.0 * evaluation["physical_ev_to_risk"]
    )
    eligible = evaluation[
        (evaluation["model_probability_win"] >= args.probability_threshold)
        & (evaluation["model_expected_pnl"] >= args.expected_pnl_threshold)
        & (evaluation["max_loss"] <= 600.0)
    ].copy()
    selected = eligible.sort_values("policy_rank", ascending=False).drop_duplicates("world")
    selected = selected.sort_values("world").reset_index(drop=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    evaluation.to_csv(args.output_dir / "scored_credit_candidates.csv", index=False)
    selected.to_csv(args.output_dir / "selected_trades.csv", index=False)

    batch = selected.groupby("batch_id").agg(
        trades=("pnl", "size"),
        win_rate=("profitable", "mean"),
        net_pnl=("pnl", "sum"),
        average_pnl=("pnl", "mean"),
    ).reset_index()
    batch.to_csv(args.output_dir / "batch_metrics.csv", index=False)
    strategy = selected.groupby("structure").agg(
        trades=("pnl", "size"),
        win_rate=("profitable", "mean"),
        net_pnl=("pnl", "sum"),
        average_pnl=("pnl", "mean"),
        worst_trade=("pnl", "min"),
        best_trade=("pnl", "max"),
    ).reset_index()
    strategy.to_csv(args.output_dir / "strategy_metrics.csv", index=False)

    total_pnl = float(selected["pnl"].sum()) if len(selected) else 0.0
    summary = {
        "policy": {
            "name": "high_win_directional_credit_v1",
            "training_worlds": int(pd.read_csv(args.training)["world"].nunique()),
            "evaluation_worlds": int(worlds["world"].nunique()),
            "eligible_structures": ELIGIBLE,
            "probability_threshold": args.probability_threshold,
            "expected_pnl_threshold": args.expected_pnl_threshold,
            "max_trades_per_world": 1,
            "maximum_loss_per_trade": 600.0,
            "uses_hidden_simulator_labels": False,
        },
        "results": {
            "trades": int(len(selected)),
            "worlds_traded": int(selected["world"].nunique()),
            "win_rate": float(selected["profitable"].mean()) if len(selected) else 0.0,
            "net_pnl": total_pnl,
            "average_pnl": float(selected["pnl"].mean()) if len(selected) else 0.0,
            "median_pnl": float(selected["pnl"].median()) if len(selected) else 0.0,
            "profit_factor": profit_factor(selected["pnl"]) if len(selected) else 0.0,
            "worst_trade": float(selected["pnl"].min()) if len(selected) else 0.0,
            "best_trade": float(selected["pnl"].max()) if len(selected) else 0.0,
            "positive_batches": int((batch["net_pnl"] > 0).sum()) if len(batch) else 0,
            "minimum_batch_pnl": float(batch["net_pnl"].min()) if len(batch) else 0.0,
            "target_75_percent_wins_met": bool(len(selected) >= 100 and selected["profitable"].mean() >= 0.75 and total_pnl > 0),
        },
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = [
        "# High-Win Directional Credit Policy — Fresh 1,000-World Evaluation",
        "",
        "The policy was trained on the prior 1,000 universes and frozen before this evaluation.",
        "It uses only observable entry-state features and selects at most one defined-risk spread per world.",
        "",
        f"- Trades: **{summary['results']['trades']}**",
        f"- Win rate: **{summary['results']['win_rate']:.1%}**",
        f"- Net P&L: **${summary['results']['net_pnl']:,.2f}**",
        f"- Profit factor: **{summary['results']['profit_factor']:.2f}**",
        f"- Positive 250-world batches: **{summary['results']['positive_batches']}/4**",
        f"- 75% profitable-trade target met: **{summary['results']['target_75_percent_wins_met']}**",
        "",
        "## Batch metrics",
        "",
        batch.to_markdown(index=False, floatfmt=".3f") if len(batch) else "No trades.",
        "",
        "## Strategy metrics",
        "",
        strategy.to_markdown(index=False, floatfmt=".3f") if len(strategy) else "No trades.",
    ]
    (args.output_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
