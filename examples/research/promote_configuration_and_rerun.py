from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeRegressor, export_text

ROOT = Path(__file__).resolve().parents[1]

BASE_FEATURES = [
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
    "net_q_edge",
    "physical_expected_net",
    "uncertainty",
    "edge_score",
    "selection_score",
    "predicted_probability_profit",
    "max_loss",
]

DERIVED_FEATURES = [
    "abs_forecast_return",
    "vol_gap_ratio",
    "skew_gap_ratio",
    "physical_expected_ror",
    "q_edge_ror",
    "premium_to_risk",
    "breadth_extremity",
    "time_fraction_elapsed",
]

FEATURES = BASE_FEATURES + DERIVED_FEATURES


@dataclass
class StructurePolicy:
    structure: str
    premium_style: str
    family: str
    depth: int
    min_samples_leaf: int
    ccp_alpha: float
    oof_prediction_threshold: float
    final_prediction_threshold: float
    selected_fraction: float
    training_candidates: int
    selected_oof_trades: int
    oof_net_pnl: float
    oof_average_pnl: float
    oof_mean_ror: float
    oof_win_rate: float
    oof_profit_factor: float
    oof_max_drawdown: float
    positive_folds: int
    score: float
    rule_text: str


def add_features(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df["abs_forecast_return"] = df["forecast_remaining_return"].abs()
    df["vol_gap_ratio"] = df["surface_vol_gap"] / df["market_iv"].clip(lower=0.04)
    df["skew_gap_ratio"] = df["surface_skew_gap"] / df["market_skew"].clip(lower=0.04)
    df["physical_expected_ror"] = 100.0 * df["physical_expected_net"] / df["max_loss"].clip(lower=1.0)
    df["q_edge_ror"] = 100.0 * df["net_q_edge"] / df["max_loss"].clip(lower=1.0)
    df["premium_to_risk"] = -100.0 * df["entry_cashflow"] / df["max_loss"].clip(lower=1.0)
    df["breadth_extremity"] = 2.0 * (df["breadth_5"] - 0.5).abs()
    df["time_fraction_elapsed"] = df["entry_minute"] / 390.0
    df["realized_ror"] = df["pnl"] / df["max_loss"].clip(lower=1.0)
    return df


def contiguous_folds(worlds: np.ndarray, n_folds: int = 5) -> list[np.ndarray]:
    ordered = np.array(sorted(np.unique(worlds)), dtype=int)
    return [chunk for chunk in np.array_split(ordered, n_folds) if len(chunk)]


def profit_factor(pnl: np.ndarray) -> float:
    gains = float(pnl[pnl > 0].sum())
    losses = float(-pnl[pnl < 0].sum())
    if losses > 0:
        return gains / losses
    return math.inf if gains > 0 else 0.0


def drawdown_by_world(frame: pd.DataFrame, all_worlds: np.ndarray) -> float:
    if frame.empty:
        return 0.0
    series = frame.groupby("world")["pnl"].sum().reindex(all_worlds, fill_value=0.0)
    equity = series.cumsum()
    dd = equity - equity.cummax()
    return float(dd.min())


def evaluate_selection(frame: pd.DataFrame, selected_mask: np.ndarray, folds: list[np.ndarray]) -> dict:
    selected = frame.loc[selected_mask].copy()
    if selected.empty:
        return {
            "trades": 0,
            "net_pnl": 0.0,
            "average_pnl": 0.0,
            "mean_ror": 0.0,
            "median_ror": 0.0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "positive_folds": 0,
            "q10_ror": 0.0,
        }
    pnl = selected["pnl"].to_numpy(float)
    ror = selected["realized_ror"].to_numpy(float)
    fold_pnl = []
    for fold in folds:
        fold_pnl.append(float(selected.loc[selected["world"].isin(fold), "pnl"].sum()))
    return {
        "trades": len(selected),
        "net_pnl": float(pnl.sum()),
        "average_pnl": float(pnl.mean()),
        "mean_ror": float(ror.mean()),
        "median_ror": float(np.median(ror)),
        "win_rate": float((pnl > 0).mean()),
        "profit_factor": profit_factor(pnl),
        "max_drawdown": drawdown_by_world(selected, np.array(sorted(frame["world"].unique()))),
        "positive_folds": int(sum(x > 0 for x in fold_pnl)),
        "q10_ror": float(np.quantile(ror, 0.10)),
    }


def policy_score(metrics: dict) -> float:
    n = metrics["trades"]
    if n <= 0:
        return -math.inf
    stability = metrics["positive_folds"] / 5.0
    dd_penalty = abs(metrics["max_drawdown"]) / max(metrics["net_pnl"], 250.0)
    tail_penalty = max(0.0, -metrics["q10_ror"] - 1.0)
    return float(
        metrics["mean_ror"] * math.sqrt(n)
        + 0.18 * metrics["median_ror"] * math.sqrt(n)
        + 0.50 * stability
        + 0.08 * math.log1p(max(metrics["profit_factor"] - 1.0, 0.0))
        - 0.22 * dd_penalty
        - 0.08 * tail_penalty
    )


def oof_predictions(
    frame: pd.DataFrame,
    folds: list[np.ndarray],
    *,
    depth: int,
    min_samples_leaf: int,
    ccp_alpha: float,
) -> np.ndarray:
    preds = np.full(len(frame), np.nan, dtype=float)
    X = frame[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(float)
    y = frame["realized_ror"].clip(-1.25, 4.0).to_numpy(float)
    worlds = frame["world"].to_numpy(int)
    for fold in folds:
        valid = np.isin(worlds, fold)
        train = ~valid
        if train.sum() < max(20, min_samples_leaf * 2) or valid.sum() == 0:
            continue
        model = DecisionTreeRegressor(
            max_depth=depth,
            min_samples_leaf=min_samples_leaf,
            ccp_alpha=ccp_alpha,
            random_state=20260804,
        )
        model.fit(X[train], y[train])
        preds[valid] = model.predict(X[valid])
    return preds


def tune_structure(frame: pd.DataFrame, *, minimum_candidates: int = 40) -> tuple[StructurePolicy | None, DecisionTreeRegressor | None]:
    if len(frame) < minimum_candidates:
        return None, None
    folds = contiguous_folds(frame["world"].to_numpy(), n_folds=5)
    if len(folds) < 5:
        return None, None

    hyperparams = [
        (depth, leaf, alpha)
        for depth in (1, 2, 3)
        for leaf in (8, 12, 18, 25, 35)
        for alpha in (0.0, 0.002, 0.006, 0.015)
        if leaf * 2 <= len(frame)
    ]

    best: tuple[float, dict, tuple[int, int, float], float, np.ndarray] | None = None
    for depth, leaf, alpha in hyperparams:
        preds = oof_predictions(frame, folds, depth=depth, min_samples_leaf=leaf, ccp_alpha=alpha)
        valid_pred = preds[np.isfinite(preds)]
        if len(valid_pred) < 0.9 * len(frame):
            continue
        quantile_thresholds = [
            float(np.quantile(valid_pred, q)) for q in (0.45, 0.55, 0.65, 0.75, 0.82, 0.88)
        ]
        thresholds = sorted({-0.02, 0.0, 0.03, 0.06, 0.10, *quantile_thresholds})
        for threshold in thresholds:
            mask = np.isfinite(preds) & (preds >= threshold)
            metrics = evaluate_selection(frame, mask, folds)
            min_trades = max(12, int(0.06 * len(frame)))
            if metrics["trades"] < min_trades:
                continue
            if metrics["net_pnl"] <= 0 or metrics["mean_ror"] <= 0:
                continue
            if metrics["positive_folds"] < 3 or metrics["profit_factor"] < 1.08:
                continue
            score = policy_score(metrics)
            if best is None or score > best[0]:
                best = (score, metrics, (depth, leaf, alpha), float(threshold), preds.copy())

    if best is None:
        return None, None

    score, metrics, (depth, leaf, alpha), threshold, _ = best
    X = frame[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0)
    y = frame["realized_ror"].clip(-1.25, 4.0)
    model = DecisionTreeRegressor(
        max_depth=depth,
        min_samples_leaf=leaf,
        ccp_alpha=alpha,
        random_state=20260804,
    )
    model.fit(X, y)
    rule_text = export_text(model, feature_names=FEATURES, decimals=5)
    row0 = frame.iloc[0]
    policy = StructurePolicy(
        structure=str(row0["structure"]),
        premium_style=str(row0["premium_style"]),
        family=str(row0["family"]),
        depth=depth,
        min_samples_leaf=leaf,
        ccp_alpha=alpha,
        oof_prediction_threshold=threshold,
        final_prediction_threshold=threshold,
        selected_fraction=float(metrics["trades"] / len(frame)),
        training_candidates=len(frame),
        selected_oof_trades=int(metrics["trades"]),
        oof_net_pnl=float(metrics["net_pnl"]),
        oof_average_pnl=float(metrics["average_pnl"]),
        oof_mean_ror=float(metrics["mean_ror"]),
        oof_win_rate=float(metrics["win_rate"]),
        oof_profit_factor=float(metrics["profit_factor"]),
        oof_max_drawdown=float(metrics["max_drawdown"]),
        positive_folds=int(metrics["positive_folds"]),
        score=float(score),
        rule_text=rule_text,
    )
    return policy, model


def predict_candidates(frame: pd.DataFrame, policies: dict[str, StructurePolicy], models: dict[str, DecisionTreeRegressor]) -> pd.DataFrame:
    pieces = []
    for structure, policy in policies.items():
        subset = frame[frame["structure"] == structure].copy()
        if subset.empty:
            continue
        X = subset[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        subset["predicted_ror"] = models[structure].predict(X)
        subset["entry_threshold"] = policy.final_prediction_threshold
        subset = subset[subset["predicted_ror"] >= policy.final_prediction_threshold].copy()
        if subset.empty:
            continue
        subset["policy_score"] = policy.score
        subset["sleeve"] = np.where(subset["premium_style"] == "credit", "premium_selling", "convex_directional")
        subset["allocation_score"] = (
            subset["predicted_ror"]
            + 0.08 * subset["edge_score"]
            + 0.04 * subset["selection_score"]
            + 0.02 * subset["predicted_probability_profit"]
            - 0.04 * (subset["max_loss"] / 600.0)
        )
        pieces.append(subset)
    if not pieces:
        return frame.iloc[0:0].copy()
    return pd.concat(pieces, ignore_index=True)


def select_portfolio(
    candidates: pd.DataFrame,
    all_worlds: np.ndarray,
    *,
    risk_cap: float,
    premium_risk_cap: float = 400.0,
    convex_risk_cap: float = 600.0,
    max_per_sleeve: int = 1,
) -> pd.DataFrame:
    selected_rows = []
    for world in all_worlds:
        wc = candidates[candidates["world"] == world]
        if wc.empty:
            continue
        picks = []
        sleeve_caps = {"premium_selling": premium_risk_cap, "convex_directional": convex_risk_cap}
        for sleeve in ("premium_selling", "convex_directional"):
            sc = wc[(wc["sleeve"] == sleeve) & (wc["max_loss"] <= sleeve_caps[sleeve])].sort_values(
                "allocation_score", ascending=False
            )
            if not sc.empty:
                picks.extend([row for _, row in sc.head(max_per_sleeve).iterrows()])
        if not picks:
            continue
        # Preserve one trade from each sleeve when each is individually within its sleeve budget.
        # The total cap remains the final portfolio safety constraint.
        if sum(float(row["max_loss"]) for row in picks) <= risk_cap:
            selected_rows.extend(picks)
            continue
        picks = sorted(
            picks,
            key=lambda r: float(r["allocation_score"]) / max(float(r["max_loss"]), 1.0),
            reverse=True,
        )
        accepted = []
        risk = 0.0
        for row in picks:
            row_risk = float(row["max_loss"])
            if risk + row_risk <= risk_cap:
                accepted.append(row)
                risk += row_risk
        selected_rows.extend(accepted)
    if not selected_rows:
        return candidates.iloc[0:0].copy()
    return pd.DataFrame(selected_rows).reset_index(drop=True)


def portfolio_metrics(selected: pd.DataFrame, all_worlds: np.ndarray) -> tuple[dict, pd.DataFrame]:
    world_pnl = selected.groupby("world")["pnl"].sum().reindex(all_worlds, fill_value=0.0)
    world_risk = selected.groupby("world")["max_loss"].sum().reindex(all_worlds, fill_value=0.0)
    equity = world_pnl.cumsum()
    drawdown = equity - equity.cummax()
    pnl = selected["pnl"].to_numpy(float) if not selected.empty else np.array([])
    summary = {
        "worlds": len(all_worlds),
        "selected_trades": len(selected),
        "worlds_traded": int(selected["world"].nunique()) if not selected.empty else 0,
        "trade_rate": float(selected["world"].nunique() / max(len(all_worlds), 1)) if not selected.empty else 0.0,
        "net_pnl": float(pnl.sum()) if len(pnl) else 0.0,
        "average_pnl_per_trade": float(pnl.mean()) if len(pnl) else 0.0,
        "median_pnl_per_trade": float(np.median(pnl)) if len(pnl) else 0.0,
        "trade_win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "positive_world_rate": float((world_pnl > 0).mean()),
        "profit_factor": profit_factor(pnl),
        "maximum_drawdown": float(drawdown.min()),
        "worst_world": float(world_pnl.min()),
        "best_world": float(world_pnl.max()),
        "maximum_observed_risk": float(world_risk.max()),
        "premium_selling_trades": int((selected["sleeve"] == "premium_selling").sum()) if not selected.empty else 0,
        "premium_selling_pnl": float(selected.loc[selected["sleeve"] == "premium_selling", "pnl"].sum()) if not selected.empty else 0.0,
        "convex_directional_trades": int((selected["sleeve"] == "convex_directional").sum()) if not selected.empty else 0,
        "convex_directional_pnl": float(selected.loc[selected["sleeve"] == "convex_directional", "pnl"].sum()) if not selected.empty else 0.0,
    }
    wf = pd.DataFrame({
        "world": all_worlds,
        "portfolio_pnl": world_pnl.to_numpy(float),
        "portfolio_max_loss": world_risk.to_numpy(float),
        "cumulative_pnl": equity.to_numpy(float),
        "drawdown": drawdown.to_numpy(float),
    })
    return summary, wf


def aggregate_metrics(frame: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    return (
        frame.groupby(groups, observed=False)
        .agg(
            trades=("pnl", "size"),
            net_pnl=("pnl", "sum"),
            average_pnl=("pnl", "mean"),
            median_pnl=("pnl", "median"),
            win_rate=("profitable", "mean"),
            average_ror=("realized_ror", "mean"),
            average_predicted_ror=("predicted_ror", "mean"),
            average_entry_minute=("entry_minute", "mean"),
            average_max_loss=("max_loss", "mean"),
        )
        .reset_index()
        .sort_values("net_pnl", ascending=False)
    )


def optimize_risk_budgets(oof_candidates: pd.DataFrame, training_worlds: np.ndarray) -> tuple[float, float, float]:
    best = None
    for total_cap in (600.0, 750.0, 900.0):
        for premium_cap in (250.0, 350.0, 450.0):
            for convex_cap in (350.0, 500.0, 600.0):
                selected = select_portfolio(
                    oof_candidates,
                    training_worlds,
                    risk_cap=total_cap,
                    premium_risk_cap=premium_cap,
                    convex_risk_cap=convex_cap,
                )
                summary, _ = portfolio_metrics(selected, training_worlds)
                premium_bonus = 0.04 * summary["premium_selling_pnl"]
                utility = (
                    summary["net_pnl"]
                    + 0.20 * max(summary["maximum_drawdown"], -5000.0)
                    + 250.0 * math.log(max(summary["profit_factor"], 1e-6))
                    + premium_bonus
                )
                if best is None or utility > best[0]:
                    best = (utility, total_cap, premium_cap, convex_cap, summary)
    return (float(best[1]), float(best[2]), float(best[3])) if best else (900.0, 400.0, 600.0)


def write_report(output: Path, full: dict, train: dict, holdout: dict, policy_table: pd.DataFrame, strategy_metrics: pd.DataFrame, sleeve_metrics: pd.DataFrame) -> None:
    lines = [
        "# Promoted Configuration and Entry-Parameter Replay",
        "",
        "## Method",
        "",
        "- Same 1,000 one-minute worlds and original seeds as the v0.3.0 tournament.",
        "- Worlds 0–499 used only to tune shallow, interpretable entry-rule trees.",
        "- Worlds 500–999 remained untouched until the policy was frozen.",
        "- Entry rules use observable candidate fields only; injected-edge flags, event labels, true regime labels, and true mispricing labels were excluded.",
        "- At most one premium-selling trade and one debit/convex trade per world.",
        "- Forced same-day liquidation; no stock ownership, naked risk, unlimited loss, or overnight holdings.",
        "",
        "## Out-of-Sample Holdout — Worlds 500–999",
        "",
        f"- Net P&L: **${holdout['net_pnl']:,.2f}**",
        f"- Trades / worlds traded: **{holdout['selected_trades']} / {holdout['worlds_traded']}**",
        f"- Average / median trade: **${holdout['average_pnl_per_trade']:,.2f} / ${holdout['median_pnl_per_trade']:,.2f}**",
        f"- Win rate: **{holdout['trade_win_rate']:.1%}**",
        f"- Profit factor: **{holdout['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${holdout['maximum_drawdown']:,.2f}**",
        f"- Premium sleeve: **{holdout['premium_selling_trades']} trades, ${holdout['premium_selling_pnl']:,.2f}**",
        f"- Convex/directional sleeve: **{holdout['convex_directional_trades']} trades, ${holdout['convex_directional_pnl']:,.2f}**",
        "",
        "## Full Same-1,000-World Replay",
        "",
        f"- Net P&L: **${full['net_pnl']:,.2f}**",
        f"- Trades / worlds traded: **{full['selected_trades']} / {full['worlds_traded']}**",
        f"- Average / median trade: **${full['average_pnl_per_trade']:,.2f} / ${full['median_pnl_per_trade']:,.2f}**",
        f"- Win rate: **{full['trade_win_rate']:.1%}**",
        f"- Profit factor: **{full['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${full['maximum_drawdown']:,.2f}**",
        f"- Maximum observed aggregate risk: **${full['maximum_observed_risk']:,.2f}**",
        "",
        "## Promoted Configurations",
        "",
        policy_table.drop(columns=["rule_text"], errors="ignore").to_markdown(index=False, floatfmt=".4f") if not policy_table.empty else "No strategies passed promotion.",
        "",
        "## Replay by Strategy",
        "",
        strategy_metrics.to_markdown(index=False, floatfmt=".4f") if not strategy_metrics.empty else "No selected trades.",
        "",
        "## Replay by Sleeve",
        "",
        sleeve_metrics.to_markdown(index=False, floatfmt=".4f") if not sleeve_metrics.empty else "No selected trades.",
        "",
        "## Interpretation",
        "",
        "The full 1,000-world number is partly in-sample because the first 500 worlds were used to tune the entry rules. The 500-world holdout is the decision-grade synthetic result. These rules still operate on simulated candidate data and do not establish live-market alpha.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades", type=Path, default=ROOT / "reports" / "full_strategy_1000_combined" / "trades.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "promoted_configuration_1000_replay")
    args = parser.parse_args()

    raw = pd.read_csv(args.trades)
    data = add_features(raw)
    training = data[data["batch_id"] <= 2].copy()
    training_worlds = np.array(sorted(training["world"].unique()), dtype=int)
    all_worlds = np.arange(1000, dtype=int)

    policies: dict[str, StructurePolicy] = {}
    models: dict[str, DecisionTreeRegressor] = {}
    for structure, frame in training.groupby("structure", sort=True):
        policy, model = tune_structure(frame)
        if policy is not None and model is not None:
            policies[structure] = policy
            models[structure] = model
            print(f"PROMOTED {structure}: OOF pnl={policy.oof_net_pnl:.2f}, trades={policy.selected_oof_trades}, threshold={policy.oof_prediction_threshold:.4f}")
        else:
            print(f"REJECTED {structure}: insufficient stable cross-validated edge")

    # Calibrate each final-model cutoff to preserve the cross-validated selection fraction.
    for structure, policy in policies.items():
        subset = training[training["structure"] == structure]
        X = subset[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0)
        full_predictions = models[structure].predict(X)
        q = float(np.clip(1.0 - policy.selected_fraction, 0.0, 1.0))
        policy.final_prediction_threshold = float(np.quantile(full_predictions, q))

    training_candidates = predict_candidates(training, policies, models)
    risk_cap, premium_risk_cap, convex_risk_cap = optimize_risk_budgets(training_candidates, training_worlds)

    all_candidates = predict_candidates(data, policies, models)
    full_selected = select_portfolio(
        all_candidates,
        all_worlds,
        risk_cap=risk_cap,
        premium_risk_cap=premium_risk_cap,
        convex_risk_cap=convex_risk_cap,
    )
    train_selected = full_selected[full_selected["world"] < 500].copy()
    holdout_selected = full_selected[full_selected["world"] >= 500].copy()

    full_summary, full_worlds = portfolio_metrics(full_selected, all_worlds)
    train_summary, train_worlds_frame = portfolio_metrics(train_selected, np.arange(0, 500, dtype=int))
    holdout_summary, holdout_worlds_frame = portfolio_metrics(holdout_selected, np.arange(500, 1000, dtype=int))
    for summary_part in (full_summary, train_summary, holdout_summary):
        summary_part["optimized_risk_cap"] = risk_cap
        summary_part["premium_risk_cap"] = premium_risk_cap
        summary_part["convex_risk_cap"] = convex_risk_cap

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    policy_rows = [asdict(p) for p in policies.values()]
    policy_table = pd.DataFrame(policy_rows).sort_values("score", ascending=False) if policy_rows else pd.DataFrame()
    policy_table.to_csv(output / "promoted_entry_configurations.csv", index=False)
    full_selected.to_csv(output / "selected_trades_1000.csv", index=False)
    holdout_selected.to_csv(output / "selected_trades_holdout_500.csv", index=False)
    full_worlds.to_csv(output / "world_portfolio_1000.csv", index=False)
    train_worlds_frame.to_csv(output / "world_portfolio_training_500.csv", index=False)
    holdout_worlds_frame.to_csv(output / "world_portfolio_holdout_500.csv", index=False)

    strategy_metrics = aggregate_metrics(full_selected, ["sleeve", "structure"])
    sleeve_metrics = aggregate_metrics(full_selected, ["sleeve"])
    holdout_strategy_metrics = aggregate_metrics(holdout_selected, ["sleeve", "structure"])
    strategy_metrics.to_csv(output / "strategy_metrics_1000.csv", index=False)
    sleeve_metrics.to_csv(output / "sleeve_metrics_1000.csv", index=False)
    holdout_strategy_metrics.to_csv(output / "strategy_metrics_holdout_500.csv", index=False)

    summary = {
        "method": {
            "training_worlds": 500,
            "holdout_worlds": 500,
            "same_original_worlds": True,
            "observable_features_only": True,
            "optimized_risk_cap": risk_cap,
            "premium_risk_cap": premium_risk_cap,
            "convex_risk_cap": convex_risk_cap,
            "promoted_structures": sorted(policies),
        },
        "training": train_summary,
        "holdout": holdout_summary,
        "full_replay": full_summary,
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    rule_dir = output / "entry_rules"
    rule_dir.mkdir(exist_ok=True)
    for policy in policies.values():
        (rule_dir / f"{policy.structure}.txt").write_text(policy.rule_text, encoding="utf-8")

    write_report(output, full_summary, train_summary, holdout_summary, policy_table, strategy_metrics, sleeve_metrics)
    print(json.dumps(summary, indent=2))
    print(f"Saved to {output}")


if __name__ == "__main__":
    main()
