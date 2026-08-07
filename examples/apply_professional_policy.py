from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

ALL_STRUCTURES = [
    "LONG_CALL", "LONG_PUT", "CALL_DEBIT_VERTICAL", "PUT_DEBIT_VERTICAL",
    "BULL_PUT_CREDIT_SPREAD", "BEAR_CALL_CREDIT_SPREAD",
    "ADAPTIVE_BULL_PUT_CREDIT_SPREAD", "ADAPTIVE_BEAR_CALL_CREDIT_SPREAD",
    "LONG_STRADDLE", "LONG_STRANGLE", "REVERSE_IRON_BUTTERFLY", "REVERSE_IRON_CONDOR",
    "IRON_BUTTERFLY", "IRON_CONDOR", "ADAPTIVE_IRON_CONDOR_BALANCED", "ADAPTIVE_IRON_CONDOR_DEFENSIVE",
    "CALL_BUTTERFLY", "PUT_BUTTERFLY", "CALL_CONDOR", "PUT_CONDOR",
    "CALL_BROKEN_WING_BUTTERFLY", "PUT_BROKEN_WING_BUTTERFLY",
    "CALL_BACKSPREAD", "PUT_BACKSPREAD",
]

NUMERIC_FEATURES = [
    "entry_minute", "minutes_remaining", "market_iv", "model_q_iv", "model_p_iv",
    "market_skew", "model_q_skew", "surface_vol_gap", "surface_skew_gap",
    "forecast_remaining_return", "breadth_5", "concentration_5", "entry_cashflow",
    "fair_q_value", "net_q_edge", "physical_expected_net", "uncertainty", "edge_score",
    "selection_score", "predicted_probability_profit", "central_range_probability",
    "tail_loss_probability", "expected_tail_loss", "cvar_95_loss", "tail_risk_score",
    "range_score", "max_loss", "max_profit", "credit", "debit", "credit_to_risk",
    "physical_ev_to_risk", "q_edge_to_risk", "iv_over_p", "iv_over_q", "direction_ratio",
    "breadth_extremity", "direction_confirmation", "market_model_skew_gap", "time_fraction",
    "entry_spread_efficiency", "tail_budget_ratio",
]

FORBIDDEN_FEATURES = {
    "regime", "event_type", "mispricing_type", "injected_edge_active", "actual_remaining_return",
    "exit_minute", "exit_reason", "exit_spot", "terminal_spot", "exit_cashflow", "pnl",
    "profitable", "return_on_risk", "holding_minutes", "mfe", "mae", "exit_forecast_return",
    "exit_breadth", "exit_iv_gap",
}

LOCKED_POLICY = {
    "name": "professional_intraday_expectancy_v1",
    "maximum_loss_per_trade": 900.0,
    "maximum_trades_per_world": 1,
    "tail_penalty": 0.18,
    "uncertainty_penalty": 0.35,
    "credit": {
        "minimum_probability_win": 0.67,
        "minimum_predicted_profit_factor": 1.35,
        "minimum_predicted_expected_pnl": 0.25,
        "maximum_loss_q90": 260.0,
        "minimum_credit_to_risk": 0.055,
        "minimum_excursion_ratio": 0.70,
    },
    "debit": {
        "minimum_probability_win": 0.34,
        "minimum_predicted_profit_factor": 1.55,
        "minimum_predicted_expected_pnl": 3.00,
        "maximum_loss_q90": 600.0,
        "minimum_excursion_ratio": 1.20,
    },
    "portfolio_targets": {
        "minimum_win_rate": 0.60,
        "minimum_profit_factor": 1.50,
        "positive_batches": 3,
    },
}


def profit_factor(pnl: pd.Series) -> float:
    gp = float(pnl[pnl > 0].sum())
    gl = float(-pnl[pnl < 0].sum())
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame[frame["structure"].isin(ALL_STRUCTURES)].copy()
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
    bullish = data["structure"].str.contains("BULL|LONG_CALL|CALL_DEBIT|CALL_BACK|CALL_BROKEN", regex=True)
    bearish = data["structure"].str.contains("BEAR|LONG_PUT|PUT_DEBIT|PUT_BACK|PUT_BROKEN", regex=True)
    data["direction_confirmation"] = np.where(
        bullish, data["breadth_5"] - 0.5,
        np.where(bearish, 0.5 - data["breadth_5"], data["breadth_extremity"]),
    )
    data["market_model_skew_gap"] = data["market_skew"] - data["model_q_skew"]
    data["time_fraction"] = data["entry_minute"] / 390.0
    data["entry_spread_efficiency"] = np.where(
        data["premium_style"].eq("credit"), data["credit_to_risk"],
        data["physical_expected_net"] / data["debit"].clip(lower=0.01),
    )
    data["tail_budget_ratio"] = data["cvar_95_loss"].fillna(data["max_loss"]) / data["max_loss"].clip(lower=1.0)
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
            raise RuntimeError("FeatureMatrix must be fitted first")
        numeric = data[NUMERIC_FEATURES].replace([np.inf, -np.inf], np.nan).fillna(self.medians)
        return self._combine(numeric, data["structure"])

    @staticmethod
    def _combine(numeric: pd.DataFrame, structures: pd.Series) -> np.ndarray:
        categorical = pd.get_dummies(structures).reindex(columns=ALL_STRUCTURES, fill_value=0)
        return np.column_stack([numeric.to_numpy(float), categorical.to_numpy(float)])


class ProfessionalEnsemble:
    def __init__(self, members: int = 3) -> None:
        self.features = FeatureMatrix()
        self.members = members
        self.win_models = []
        self.win_size_models = []
        self.loss_size_models = []
        self.loss_q90_models = []
        self.mfe_models = []
        self.mae_models = []
        for i in range(members):
            self.win_models.append(HistGradientBoostingClassifier(
                max_iter=190, learning_rate=0.045, max_leaf_nodes=15,
                min_samples_leaf=35, l2_regularization=4.0, random_state=9200 + i,
            ))
            common = {"max_iter": 170, "learning_rate": 0.045, "max_leaf_nodes": 12,
                      "min_samples_leaf": 28, "l2_regularization": 6.0}
            self.win_size_models.append(HistGradientBoostingRegressor(**common, random_state=9300 + i))
            self.loss_size_models.append(HistGradientBoostingRegressor(**common, random_state=9400 + i))
            self.loss_q90_models.append(HistGradientBoostingRegressor(
                loss="quantile", quantile=0.90, **common, random_state=9500 + i,
            ))
            self.mfe_models.append(HistGradientBoostingRegressor(**common, random_state=9600 + i))
            self.mae_models.append(HistGradientBoostingRegressor(**common, random_state=9700 + i))

    def fit(self, training: pd.DataFrame) -> None:
        x = self.features.fit_transform(training)
        pnl = training["pnl"].to_numpy(float)
        winners = pnl > 0
        losers = pnl < 0
        y = winners.astype(int)
        if winners.sum() < 100 or losers.sum() < 100:
            raise ValueError("Insufficient outcomes for professional ensemble")
        mfe = np.log1p(training["mfe"].clip(lower=0.0).to_numpy(float))
        mae = np.log1p((-training["mae"].clip(upper=0.0)).to_numpy(float))
        for i in range(self.members):
            self.win_models[i].fit(x, y)
            self.win_size_models[i].fit(x[winners], np.log1p(pnl[winners]))
            self.loss_size_models[i].fit(x[losers], np.log1p(-pnl[losers]))
            self.loss_q90_models[i].fit(x[losers], np.log1p(-pnl[losers]))
            self.mfe_models[i].fit(x, mfe)
            self.mae_models[i].fit(x, mae)

    def score(self, evaluation: pd.DataFrame) -> pd.DataFrame:
        result = evaluation.copy()
        x = self.features.transform(result)
        p = np.vstack([m.predict_proba(x)[:, 1] for m in self.win_models])
        wins = np.vstack([np.maximum(np.expm1(m.predict(x)), 0.0) for m in self.win_size_models])
        losses = np.vstack([np.maximum(np.expm1(m.predict(x)), 0.0) for m in self.loss_size_models])
        q90s = np.vstack([np.maximum(np.expm1(m.predict(x)), 0.0) for m in self.loss_q90_models])
        mfes = np.vstack([np.maximum(np.expm1(m.predict(x)), 0.0) for m in self.mfe_models])
        maes = np.vstack([np.maximum(np.expm1(m.predict(x)), 0.0) for m in self.mae_models])

        p_mean, p_std = p.mean(0), p.std(0)
        p_cons = np.clip(p_mean - 0.50 * p_std, 0.01, 0.99)
        expected_win = np.median(wins, axis=0)
        expected_loss = losses.mean(0) + 0.50 * losses.std(0)
        loss_q90 = np.maximum(q90s.max(0), expected_loss)
        expected_mfe = np.maximum(mfes.mean(0) - 0.25 * mfes.std(0), 0.0)
        expected_mae = maes.mean(0) + 0.50 * maes.std(0)
        expected_pnl = p_cons * expected_win - (1.0 - p_cons) * expected_loss
        expected_pf = (p_cons * expected_win) / np.maximum((1.0 - p_cons) * expected_loss, 1e-6)
        excursion_ratio = expected_mfe / np.maximum(expected_mae, 1e-6)
        tail_adjusted = (
            expected_pnl
            - LOCKED_POLICY["tail_penalty"] * (1.0 - p_cons) * loss_q90
            - LOCKED_POLICY["uncertainty_penalty"] * p_std * expected_loss
        )
        result["model_probability_win"] = p_cons
        result["model_probability_uncertainty"] = p_std
        result["model_expected_win"] = expected_win
        result["model_expected_loss"] = expected_loss
        result["model_loss_q90"] = loss_q90
        result["model_expected_mfe"] = expected_mfe
        result["model_expected_mae"] = expected_mae
        result["model_excursion_ratio"] = excursion_ratio
        result["model_expected_pnl"] = expected_pnl
        result["model_expected_profit_factor"] = expected_pf
        result["model_tail_adjusted_ev"] = tail_adjusted
        result["policy_rank"] = (
            tail_adjusted
            + 4.0 * np.log(np.maximum(expected_pf, 1.0))
            + 0.04 * expected_mfe
            - 0.025 * loss_q90
            - 2.0 * p_std
        )
        return result


def derive_promoted_structures(training: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    rows = []
    for structure, group in training.groupby("structure"):
        batch_pnl = group.groupby("batch_id")["pnl"].sum()
        rows.append({
            "structure": structure,
            "trades": len(group),
            "win_rate": float(group["profitable"].mean()),
            "net_pnl": float(group["pnl"].sum()),
            "profit_factor": profit_factor(group["pnl"]),
            "positive_batches": int((batch_pnl > 0).sum()),
            "worst_batch_pnl": float(batch_pnl.min()),
        })
    table = pd.DataFrame(rows).sort_values(["positive_batches", "profit_factor", "net_pnl"], ascending=False)
    promoted = table[
        (table["trades"] >= 40)
        & (table["net_pnl"] > 0)
        & (table["profit_factor"] >= 1.05)
        & (table["positive_batches"] >= 3)
    ]["structure"].tolist()
    # Never leave the selector empty due to an unusually difficult calibration sample.
    conditional_credit = [
        "BULL_PUT_CREDIT_SPREAD",
        "BEAR_CALL_CREDIT_SPREAD",
        "IRON_CONDOR",
        "ADAPTIVE_IRON_CONDOR_BALANCED",
        "ADAPTIVE_IRON_CONDOR_DEFENSIVE",
    ]
    promoted = list(dict.fromkeys(promoted + [s for s in conditional_credit if s in set(table["structure"])]))
    if not promoted:
        promoted = table.head(4)["structure"].tolist()
    table["promoted"] = table["structure"].isin(promoted)
    return promoted, table


def select_trades(scored: pd.DataFrame, promoted: list[str]) -> pd.DataFrame:
    data = scored[scored["structure"].isin(promoted)].copy()
    is_credit = data["premium_style"].eq("credit")
    c = LOCKED_POLICY["credit"]
    d = LOCKED_POLICY["debit"]
    credit_gate = (
        is_credit
        & (data["model_probability_win"] >= c["minimum_probability_win"])
        & (data["model_expected_profit_factor"] >= c["minimum_predicted_profit_factor"])
        & (data["model_expected_pnl"] >= c["minimum_predicted_expected_pnl"])
        & (data["model_loss_q90"] <= c["maximum_loss_q90"])
        & (data["credit_to_risk"] >= c["minimum_credit_to_risk"])
        & (data["model_excursion_ratio"] >= c["minimum_excursion_ratio"])
    )
    debit_gate = (
        ~is_credit
        & (data["model_probability_win"] >= d["minimum_probability_win"])
        & (data["model_expected_profit_factor"] >= d["minimum_predicted_profit_factor"])
        & (data["model_expected_pnl"] >= d["minimum_predicted_expected_pnl"])
        & (data["model_loss_q90"] <= d["maximum_loss_q90"])
        & (data["model_excursion_ratio"] >= d["minimum_excursion_ratio"])
    )
    eligible = data[(credit_gate | debit_gate) & (data["max_loss"] <= LOCKED_POLICY["maximum_loss_per_trade"])].copy()
    return eligible.sort_values("policy_rank", ascending=False).drop_duplicates("world").sort_values("world").reset_index(drop=True)


def metrics(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"trades": 0, "win_rate": 0.0, "net_pnl": 0.0, "average_pnl": 0.0,
                "median_pnl": 0.0, "profit_factor": 0.0, "average_win": 0.0,
                "average_loss": 0.0, "win_loss_ratio": 0.0, "max_drawdown": 0.0,
                "worst_trade": 0.0, "best_trade": 0.0}
    winners = frame.loc[frame.pnl > 0, "pnl"]
    losers = -frame.loc[frame.pnl < 0, "pnl"]
    equity = frame.sort_values("world")["pnl"].cumsum()
    drawdown = equity - equity.cummax()
    aw = float(winners.mean()) if len(winners) else 0.0
    al = float(losers.mean()) if len(losers) else 0.0
    return {
        "trades": len(frame), "win_rate": float(frame.profitable.mean()),
        "net_pnl": float(frame.pnl.sum()), "average_pnl": float(frame.pnl.mean()),
        "median_pnl": float(frame.pnl.median()), "profit_factor": profit_factor(frame.pnl),
        "average_win": aw, "average_loss": al, "win_loss_ratio": aw / al if al else math.inf,
        "max_drawdown": float(drawdown.min()), "worst_trade": float(frame.pnl.min()),
        "best_trade": float(frame.pnl.max()),
    }


def grouped_metrics(frame: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = [{key: value, **metrics(group)} for value, group in frame.groupby(key)]
    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False) if rows else pd.DataFrame()


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply locked professional intraday options policy")
    parser.add_argument("--training", type=Path, nargs="+", required=True)
    parser.add_argument("--evaluation", type=Path, nargs="+", required=True)
    parser.add_argument("--worlds", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    raw_training = pd.concat([pd.read_csv(p) for p in args.training], ignore_index=True)
    raw_eval = pd.concat([pd.read_csv(p) for p in args.evaluation], ignore_index=True)
    training = enrich(raw_training)
    evaluation = enrich(raw_eval)
    worlds = pd.concat([pd.read_csv(p) for p in args.worlds], ignore_index=True)

    promoted, promotion_table = derive_promoted_structures(training)
    models = ProfessionalEnsemble()
    models.fit(training)
    scored = models.score(evaluation)
    selected = select_trades(scored, promoted)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    scored.to_csv(args.output_dir / "scored_candidates.csv", index=False)
    selected.to_csv(args.output_dir / "selected_trades.csv", index=False)
    worlds.to_csv(args.output_dir / "worlds.csv", index=False)
    promotion_table.to_csv(args.output_dir / "promotion_table.csv", index=False)
    grouped_metrics(selected, "batch_id").to_csv(args.output_dir / "batch_metrics.csv", index=False)
    grouped_metrics(selected, "structure").to_csv(args.output_dir / "strategy_metrics.csv", index=False)
    grouped_metrics(selected, "premium_style").to_csv(args.output_dir / "sleeve_metrics.csv", index=False)
    grouped_metrics(selected, "exit_reason").to_csv(args.output_dir / "exit_metrics.csv", index=False)

    overall = metrics(selected)
    batch = grouped_metrics(selected, "batch_id")
    top10 = float(selected.nlargest(min(10, len(selected)), "pnl")["pnl"].sum()) if len(selected) else 0.0
    summary = {
        "policy": {**LOCKED_POLICY, "promoted_structures": promoted, "entry_information_only": True,
                   "forbidden_features": sorted(FORBIDDEN_FEATURES)},
        "results": {**overall, "worlds": int(worlds.world.nunique()),
                    "worlds_traded": int(selected.world.nunique()),
                    "positive_batches": int((batch.net_pnl > 0).sum()) if not batch.empty else 0,
                    "pnl_excluding_top_10": float(selected.pnl.sum() - top10),
                    "top_10_pnl_share": top10 / max(float(selected.pnl.sum()), 1e-9)},
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    lines = [
        "# Professional Intraday Options Policy — Fresh Evaluation", "",
        "The selector and exit state machines were frozen before this evaluation. Every trade is finite-risk, same-day, and limited to one position per universe.", "",
        "## Promoted structures", "", *[f"- {s}" for s in promoted], "",
        "## Aggregate results", "",
        f"- Trades: **{overall['trades']}**", f"- Win rate: **{overall['win_rate']:.1%}**",
        f"- Net P&L: **${overall['net_pnl']:,.2f}**", f"- Profit factor: **{overall['profit_factor']:.2f}**",
        f"- Average win / loss: **${overall['average_win']:,.2f} / -${overall['average_loss']:,.2f}**",
        f"- Maximum drawdown: **${overall['max_drawdown']:,.2f}**", f"- P&L excluding top ten: **${summary['results']['pnl_excluding_top_10']:,.2f}**",
        "", "## By strategy", "", grouped_metrics(selected, "structure").to_markdown(index=False, floatfmt=".3f") if len(selected) else "No trades.",
        "", "## By sleeve", "", grouped_metrics(selected, "premium_style").to_markdown(index=False, floatfmt=".3f") if len(selected) else "No trades.",
        "", "## By exit", "", grouped_metrics(selected, "exit_reason").to_markdown(index=False, floatfmt=".3f") if len(selected) else "No trades.",
        "", "## Caveat", "", "This remains a historically anchored simulation, not a licensed point-in-time historical replay or proof of live profitability.",
    ]
    (args.output_dir / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
