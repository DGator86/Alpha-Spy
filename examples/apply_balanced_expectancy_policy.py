from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

ELIGIBLE_STRUCTURES = [
    "LONG_STRANGLE",
    "LONG_PUT",
    "LONG_CALL",
    "LONG_STRADDLE",
    "BEAR_CALL_CREDIT_SPREAD",
    "BULL_PUT_CREDIT_SPREAD",
]

NUMERIC_FEATURES = [
    "entry_minute",
    "minutes_remaining",
    "market_iv",
    "model_q_iv",
    "model_p_iv",
    "market_skew",
    "model_q_skew",
    "surface_vol_gap",
    "surface_skew_gap",
    "forecast_remaining_return",
    "breadth_5",
    "concentration_5",
    "entry_cashflow",
    "fair_q_value",
    "net_q_edge",
    "physical_expected_net",
    "uncertainty",
    "edge_score",
    "selection_score",
    "predicted_probability_profit",
    "central_range_probability",
    "tail_loss_probability",
    "expected_tail_loss",
    "cvar_95_loss",
    "tail_risk_score",
    "range_score",
    "max_loss",
    "max_profit",
    "credit",
    "debit",
    "credit_to_risk",
    "physical_ev_to_risk",
    "q_edge_to_risk",
    "iv_over_p",
    "iv_over_q",
    "direction_ratio",
    "breadth_extremity",
    "direction_confirmation",
    "market_model_skew_gap",
]

FORBIDDEN_FEATURES = {
    "regime",
    "event_type",
    "mispricing_type",
    "injected_edge_active",
    "actual_remaining_return",
    "exit_minute",
    "exit_reason",
    "exit_spot",
    "terminal_spot",
    "exit_cashflow",
    "pnl",
    "profitable",
    "return_on_risk",
}

# Locked after grouped out-of-fold development on 4,000 prior universes.
LOCKED_POLICY = {
    "name": "balanced_expectancy_severity_v1",
    "minimum_probability_win": 0.55,
    "minimum_predicted_profit_factor": 1.50,
    "minimum_predicted_expected_pnl": 1.00,
    "maximum_predicted_90pct_loss": 500.00,
    "maximum_loss_per_trade": 900.00,
    "maximum_trades_per_world": 1,
    "target_win_rate_floor": 0.65,
    "target_profit_factor_floor": 1.35,
    "tail_penalty": 0.15,
}


def profit_factor(pnl: pd.Series) -> float:
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())
    if gross_loss > 0:
        return gross_profit / gross_loss
    return math.inf if gross_profit > 0 else 0.0


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame[frame["structure"].isin(ELIGIBLE_STRUCTURES)].copy()
    max_loss_share = (data["max_loss"] / 100.0).clip(lower=1e-6)
    data["credit"] = (-data["entry_cashflow"]).clip(lower=0.0)
    data["debit"] = data["entry_cashflow"].clip(lower=0.0)
    data["credit_to_risk"] = data["credit"] / max_loss_share
    data["physical_ev_to_risk"] = data["physical_expected_net"] / max_loss_share
    data["q_edge_to_risk"] = data["net_q_edge"] / max_loss_share
    data["iv_over_p"] = (data["market_iv"] - data["model_p_iv"]) / data["market_iv"].clip(lower=0.01)
    data["iv_over_q"] = (data["market_iv"] - data["model_q_iv"]) / data["market_iv"].clip(lower=0.01)
    physical_move = data["model_p_iv"] * np.sqrt(data["minutes_remaining"] / (252.0 * 390.0))
    data["direction_ratio"] = data["forecast_remaining_return"].abs() / physical_move.clip(lower=1e-6)
    data["breadth_extremity"] = 2.0 * (data["breadth_5"] - 0.5).abs()
    bullish = data["structure"].str.contains("BULL|CALL", regex=True)
    data["direction_confirmation"] = np.where(
        bullish,
        data["breadth_5"] - 0.5,
        0.5 - data["breadth_5"],
    )
    data["market_model_skew_gap"] = data["market_skew"] - data["model_q_skew"]
    return data


class FeatureMatrix:
    def __init__(self) -> None:
        self.medians: pd.Series | None = None

    def fit_transform(self, data: pd.DataFrame) -> np.ndarray:
        numeric = data[NUMERIC_FEATURES].replace([np.inf, -np.inf], np.nan)
        self.medians = numeric.median().fillna(0.0)
        return self._combine(numeric.fillna(self.medians), data["structure"])

    def transform(self, data: pd.DataFrame) -> np.ndarray:
        if self.medians is None:
            raise RuntimeError("FeatureMatrix must be fitted before transform.")
        numeric = data[NUMERIC_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(self.medians)
        return self._combine(numeric, data["structure"])

    @staticmethod
    def _combine(numeric: pd.DataFrame, structures: pd.Series) -> np.ndarray:
        categorical = pd.get_dummies(structures).reindex(columns=ELIGIBLE_STRUCTURES, fill_value=0)
        return np.column_stack([numeric.to_numpy(dtype=float), categorical.to_numpy(dtype=float)])


class BalancedSeverityModels:
    def __init__(self) -> None:
        self.features = FeatureMatrix()
        self.win_classifier = HistGradientBoostingClassifier(
            max_iter=180,
            learning_rate=0.05,
            max_leaf_nodes=15,
            min_samples_leaf=35,
            l2_regularization=3.0,
            random_state=8101,
        )
        common = {
            "max_iter": 160,
            "learning_rate": 0.05,
            "max_leaf_nodes": 12,
            "min_samples_leaf": 25,
            "l2_regularization": 5.0,
        }
        self.win_size = HistGradientBoostingRegressor(**common, random_state=8102)
        self.loss_size = HistGradientBoostingRegressor(**common, random_state=8103)
        self.loss_q90 = HistGradientBoostingRegressor(
            loss="quantile",
            quantile=0.90,
            **common,
            random_state=8104,
        )

    def fit(self, training: pd.DataFrame) -> None:
        x = self.features.fit_transform(training)
        pnl = training["pnl"].to_numpy(dtype=float)
        profitable = training["profitable"].astype(int).to_numpy()
        winners = pnl > 0
        losers = pnl < 0
        if winners.sum() < 50 or losers.sum() < 50:
            raise ValueError("Insufficient winner or loser observations for severity modeling.")
        self.win_classifier.fit(x, profitable)
        self.win_size.fit(x[winners], np.log1p(pnl[winners]))
        self.loss_size.fit(x[losers], np.log1p(-pnl[losers]))
        self.loss_q90.fit(x[losers], np.log1p(-pnl[losers]))

    def score(self, evaluation: pd.DataFrame) -> pd.DataFrame:
        result = evaluation.copy()
        x = self.features.transform(result)
        p_win = self.win_classifier.predict_proba(x)[:, 1]
        expected_win = np.maximum(np.expm1(self.win_size.predict(x)), 0.0)
        expected_loss = np.maximum(np.expm1(self.loss_size.predict(x)), 0.0)
        loss_q90 = np.maximum(np.expm1(self.loss_q90.predict(x)), expected_loss)
        expected_pnl = p_win * expected_win - (1.0 - p_win) * expected_loss
        expected_pf = (p_win * expected_win) / np.maximum((1.0 - p_win) * expected_loss, 1e-6)
        tail_adjusted = expected_pnl - LOCKED_POLICY["tail_penalty"] * (1.0 - p_win) * loss_q90
        result["model_probability_win"] = p_win
        result["model_expected_win"] = expected_win
        result["model_expected_loss"] = expected_loss
        result["model_loss_q90"] = loss_q90
        result["model_expected_pnl"] = expected_pnl
        result["model_expected_profit_factor"] = expected_pf
        result["model_tail_adjusted_ev"] = tail_adjusted
        result["policy_rank"] = (
            tail_adjusted
            + 5.0 * np.log(np.maximum(expected_pf, 1.0))
            + 2.0 * (p_win - 0.65) * expected_win
        )
        return result


def select_trades(scored: pd.DataFrame) -> pd.DataFrame:
    policy = LOCKED_POLICY
    eligible = scored[
        (scored["model_probability_win"] >= policy["minimum_probability_win"])
        & (scored["model_expected_profit_factor"] >= policy["minimum_predicted_profit_factor"])
        & (scored["model_expected_pnl"] >= policy["minimum_predicted_expected_pnl"])
        & (scored["model_loss_q90"] <= policy["maximum_predicted_90pct_loss"])
        & (scored["max_loss"] <= policy["maximum_loss_per_trade"])
    ].copy()
    return (
        eligible.sort_values("policy_rank", ascending=False)
        .drop_duplicates("world")
        .sort_values("world")
        .reset_index(drop=True)
    )


def metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "net_pnl": 0.0,
            "average_pnl": 0.0,
            "median_pnl": 0.0,
            "profit_factor": 0.0,
            "average_win": 0.0,
            "average_loss": 0.0,
            "win_loss_ratio": 0.0,
            "worst_trade": 0.0,
            "best_trade": 0.0,
        }
    winners = frame.loc[frame["pnl"] > 0, "pnl"]
    losers = -frame.loc[frame["pnl"] < 0, "pnl"]
    average_win = float(winners.mean()) if len(winners) else 0.0
    average_loss = float(losers.mean()) if len(losers) else 0.0
    return {
        "trades": len(frame),
        "win_rate": float(frame["profitable"].mean()),
        "net_pnl": float(frame["pnl"].sum()),
        "average_pnl": float(frame["pnl"].mean()),
        "median_pnl": float(frame["pnl"].median()),
        "profit_factor": profit_factor(frame["pnl"]),
        "average_win": average_win,
        "average_loss": average_loss,
        "win_loss_ratio": average_win / average_loss if average_loss > 0 else math.inf,
        "worst_trade": float(frame["pnl"].min()),
        "best_trade": float(frame["pnl"].max()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply the locked probability-and-severity SPY policy.")
    parser.add_argument("--training", type=Path, nargs="+", required=True)
    parser.add_argument("--evaluation", type=Path, nargs="+", required=True)
    parser.add_argument("--worlds", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    raw_training = pd.concat([pd.read_csv(path) for path in args.training], ignore_index=True)
    raw_evaluation = pd.concat([pd.read_csv(path) for path in args.evaluation], ignore_index=True)
    training = enrich(raw_training)
    evaluation = enrich(raw_evaluation)
    worlds = pd.concat([pd.read_csv(path) for path in args.worlds], ignore_index=True)

    models = BalancedSeverityModels()
    models.fit(training)
    scored = models.score(evaluation)
    selected = select_trades(scored)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.output_dir / "scored_candidates.csv", index=False)
    selected.to_csv(args.output_dir / "selected_trades.csv", index=False)
    worlds.to_csv(args.output_dir / "worlds.csv", index=False)

    batch_metrics = []
    for batch_id, group in selected.groupby("batch_id"):
        row = {"batch_id": int(batch_id), **metrics(group)}
        batch_metrics.append(row)
    batch = pd.DataFrame(batch_metrics)
    batch.to_csv(args.output_dir / "batch_metrics.csv", index=False)

    strategy_metrics = []
    for structure, group in selected.groupby("structure"):
        row = {"structure": structure, **metrics(group)}
        strategy_metrics.append(row)
    strategy = pd.DataFrame(strategy_metrics)
    strategy.to_csv(args.output_dir / "strategy_metrics.csv", index=False)

    overall = metrics(selected)
    excluding_top_10 = float(selected.nsmallest(max(len(selected) - 10, 0), "pnl")["pnl"].sum()) if len(selected) > 10 else 0.0
    summary = {
        "policy": {
            **LOCKED_POLICY,
            "eligible_structures": ELIGIBLE_STRUCTURES,
            "training_source_worlds": int(raw_training["world"].nunique()),
            "training_worlds_with_eligible_candidates": int(training["world"].nunique()),
            "evaluation_worlds": int(worlds["world"].nunique()),
            "uses_hidden_simulator_labels": False,
            "forbidden_feature_names": sorted(FORBIDDEN_FEATURES),
        },
        "results": {
            **overall,
            "worlds_traded": int(selected["world"].nunique()),
            "positive_batches": int((batch["net_pnl"] > 0).sum()) if not batch.empty else 0,
            "minimum_batch_pnl": float(batch["net_pnl"].min()) if not batch.empty else 0.0,
            "pnl_excluding_top_10": excluding_top_10,
            "win_rate_floor_met": bool(overall["win_rate"] >= LOCKED_POLICY["target_win_rate_floor"]),
            "profit_factor_floor_met": bool(overall["profit_factor"] >= LOCKED_POLICY["target_profit_factor_floor"]),
        },
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    report = [
        "# Balanced Expectancy and Loss-Severity Policy — New 1,000-Universe Evaluation",
        "",
        "The model was frozen before this evaluation. It predicts win probability, expected winner size, conditional expected loss, and conditional 90th-percentile loss using entry-time information only.",
        "",
        "## Locked gates",
        "",
        f"- Minimum predicted win probability: **{LOCKED_POLICY['minimum_probability_win']:.0%}**",
        f"- Minimum predicted profit factor: **{LOCKED_POLICY['minimum_predicted_profit_factor']:.2f}**",
        f"- Minimum predicted expected P&L: **${LOCKED_POLICY['minimum_predicted_expected_pnl']:.2f}**",
        f"- Maximum predicted conditional 90th-percentile loss: **${LOCKED_POLICY['maximum_predicted_90pct_loss']:.2f}**",
        f"- Maximum defined loss: **${LOCKED_POLICY['maximum_loss_per_trade']:.2f}**",
        "- Maximum one trade per universe; same-day forced liquidation; no shares or unlimited risk.",
        "",
        "## Results",
        "",
        f"- Trades: **{overall['trades']}**",
        f"- Win rate: **{overall['win_rate']:.1%}**",
        f"- Net P&L: **${overall['net_pnl']:,.2f}**",
        f"- Profit factor: **{overall['profit_factor']:.2f}**",
        f"- Average winner / loser: **${overall['average_win']:,.2f} / ${overall['average_loss']:,.2f}**",
        f"- Winner-to-loser ratio: **{overall['win_loss_ratio']:.2f}**",
        f"- Positive 250-world batches: **{summary['results']['positive_batches']}/4**",
        f"- P&L excluding ten largest winners: **${excluding_top_10:,.2f}**",
        "",
        "## Batch metrics",
        "",
        batch.to_markdown(index=False, floatfmt=".3f") if not batch.empty else "No selected trades.",
        "",
        "## Strategy metrics",
        "",
        strategy.to_markdown(index=False, floatfmt=".3f") if not strategy.empty else "No selected trades.",
        "",
        "## Interpretation",
        "",
        "The win-rate and profit-factor floors are evaluated independently. A high predicted win probability cannot qualify a trade when conditional loss severity destroys expected value or projected profit factor.",
    ]
    (args.output_dir / "REPORT.md").write_text("\n".join(report), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
