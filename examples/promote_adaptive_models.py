from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

CORE_MODELS = {
    "LONG_STRANGLE": "convex_directional",
    "LONG_CALL": "convex_directional",
    "LONG_PUT": "convex_directional",
    "BEAR_CALL_CREDIT_SPREAD": "premium_selling",
    "BULL_PUT_CREDIT_SPREAD": "premium_selling",
    "ADAPTIVE_IRON_CONDOR_BALANCED": "premium_selling",
    "ADAPTIVE_IRON_CONDOR_DEFENSIVE": "premium_selling",
}


def load_batches(root: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    trades, worlds = [], []
    for batch in sorted(root.glob("batch_*")):
        trades.append(pd.read_csv(batch / "trades.csv"))
        worlds.append(pd.read_csv(batch / "worlds.csv"))
    if not trades:
        raise FileNotFoundError(f"No batch directories found under {root}")
    return pd.concat(trades, ignore_index=True), pd.concat(worlds, ignore_index=True)


def observable_gate(frame: pd.DataFrame) -> pd.Series:
    s = frame["structure"]
    vol_gap = frame["surface_vol_gap"]
    forecast = frame["forecast_remaining_return"]
    breadth = frame["breadth_5"]
    gate = pd.Series(False, index=frame.index)

    gate |= (s == "LONG_STRANGLE") & (vol_gap >= 0.004) & (frame["selection_score"] >= 0.28)
    gate |= (s == "LONG_CALL") & (forecast >= 0.0011) & (breadth >= 0.53)
    gate |= (s == "LONG_PUT") & (forecast <= -0.0011) & (breadth <= 0.47)
    gate |= (
        (s == "BEAR_CALL_CREDIT_SPREAD")
        & (forecast <= -0.00075)
        & (vol_gap <= 0.002)
        & (breadth <= 0.49)
    )
    gate |= (
        (s == "BULL_PUT_CREDIT_SPREAD")
        & (forecast >= 0.00075)
        & (vol_gap <= 0.002)
        & (breadth >= 0.51)
    )
    adaptive = s.str.startswith("ADAPTIVE_IRON_CONDOR", na=False)
    gate |= (
        adaptive
        & (frame["range_score"] >= 0.020)
        & (frame["tail_risk_score"] <= 0.86)
        & (frame["tail_loss_probability"] <= 0.24)
        & (frame["central_range_probability"] >= 0.82)
        & (frame["entry_minute"].between(180, 325))
    )
    return gate


def model_metrics(frame: pd.DataFrame, sample: str) -> pd.DataFrame:
    x = frame[frame["structure"].isin(CORE_MODELS)].copy()
    x = x[observable_gate(x)].copy()
    x["return_on_risk"] = x["pnl"] / x["max_loss"].clip(lower=1.0)

    rows = []
    for structure, group in x.groupby("structure"):
        pnl = group["pnl"].to_numpy(float)
        gp = float(pnl[pnl > 0].sum())
        gl = float(-pnl[pnl < 0].sum())
        rows.append({
            "sample": sample,
            "structure": structure,
            "sleeve": CORE_MODELS[structure],
            "trades": int(len(group)),
            "net_pnl": float(pnl.sum()),
            "average_pnl": float(pnl.mean()),
            "median_pnl": float(np.median(pnl)),
            "win_rate": float((pnl > 0).mean()),
            "profit_factor": gp / gl if gl else (math.inf if gp else 0.0),
            "mean_return_on_risk": float(group["return_on_risk"].mean()),
            "q10_return_on_risk": float(group["return_on_risk"].quantile(0.10)),
            "max_loss_trade": float(pnl.min()),
        })
    return pd.DataFrame(rows)


def promotion_table(development: pd.DataFrame, validation: pd.DataFrame) -> pd.DataFrame:
    dev = model_metrics(development, "development").set_index("structure")
    val = model_metrics(validation, "validation").set_index("structure")
    structures = sorted(CORE_MODELS)
    rows = []
    for structure in structures:
        d = dev.loc[structure].to_dict() if structure in dev.index else {}
        v = val.loc[structure].to_dict() if structure in val.index else {}
        d_trades, v_trades = int(d.get("trades", 0)), int(v.get("trades", 0))
        d_pf, v_pf = float(d.get("profit_factor", 0.0)), float(v.get("profit_factor", 0.0))
        d_ror, v_ror = float(d.get("mean_return_on_risk", 0.0)), float(v.get("mean_return_on_risk", 0.0))
        robustness = min(d_ror, v_ror)
        sample_weight = min(1.0, (d_trades + v_trades) / 80.0)
        score = sample_weight * (0.55 * robustness + 0.20 * min(d_ror, 1.0) + 0.20 * min(v_ror, 1.0))
        score += 0.03 * min(math.log1p(max(d_pf, 0.0)), math.log1p(max(v_pf, 0.0)))
        min_trades = 12 if structure.startswith("ADAPTIVE_IRON_CONDOR") else 20
        promoted = bool(
            d_trades >= min_trades
            and v_trades >= min_trades
            and float(d.get("net_pnl", 0.0)) > 0
            and float(v.get("net_pnl", 0.0)) > 0
            and d_pf > 1.03
            and v_pf > 1.03
            and robustness > 0
        )
        rows.append({
            "structure": structure,
            "sleeve": CORE_MODELS[structure],
            "development_trades": d_trades,
            "development_pnl": float(d.get("net_pnl", 0.0)),
            "development_profit_factor": d_pf,
            "development_mean_ror": d_ror,
            "validation_trades": v_trades,
            "validation_pnl": float(v.get("net_pnl", 0.0)),
            "validation_profit_factor": v_pf,
            "validation_mean_ror": v_ror,
            "robustness_score": float(score),
            "promoted": promoted,
        })
    return pd.DataFrame(rows).sort_values("robustness_score", ascending=False).reset_index(drop=True)


def select_portfolio(candidates: pd.DataFrame, promotions: pd.DataFrame, risk_cap: float) -> pd.DataFrame:
    promoted = promotions[promotions["promoted"]][["structure", "sleeve", "robustness_score"]]
    frame = candidates.merge(promoted, on="structure", how="inner")
    frame = frame[observable_gate(frame)].copy()
    if frame.empty:
        return frame
    frame["allocation_score"] = (
        frame["robustness_score"]
        + 0.055 * frame["selection_score"]
        + 0.020 * frame["predicted_probability_profit"]
        + 0.015 * frame["physical_expected_net"] / frame["max_loss"].clip(lower=1.0)
    )
    adaptive = frame["structure"].str.startswith("ADAPTIVE_IRON_CONDOR", na=False)
    frame.loc[adaptive, "allocation_score"] += (
        0.10 * frame.loc[adaptive, "range_score"]
        - 0.07 * frame.loc[adaptive, "tail_risk_score"]
        - 0.06 * frame.loc[adaptive, "cvar_95_loss"] / frame.loc[adaptive, "max_loss"].clip(lower=1.0)
    )

    picks = []
    for _, world in frame.groupby("world", sort=True):
        selected = []
        for sleeve in ("premium_selling", "convex_directional"):
            available = world[world["sleeve"] == sleeve]
            if not available.empty:
                selected.append(available.sort_values("allocation_score", ascending=False).iloc[0])
        if len(selected) == 2 and sum(float(row["max_loss"]) for row in selected) > risk_cap:
            selected = [max(selected, key=lambda row: float(row["allocation_score"]) / max(float(row["max_loss"]), 1.0))]
        picks.extend(selected)
    return pd.DataFrame(picks).reset_index(drop=True)


def summarize(selected: pd.DataFrame, worlds: pd.DataFrame) -> tuple[dict, pd.DataFrame]:
    world_ids = sorted(worlds["world"].unique())
    pnl = selected.groupby("world")["pnl"].sum().reindex(world_ids, fill_value=0.0)
    equity = pnl.cumsum()
    dd = equity - equity.cummax()
    trade_pnl = selected["pnl"].to_numpy(float) if not selected.empty else np.array([])
    gp = float(trade_pnl[trade_pnl > 0].sum()) if len(trade_pnl) else 0.0
    gl = float(-trade_pnl[trade_pnl < 0].sum()) if len(trade_pnl) else 0.0
    summary = {
        "worlds": len(world_ids),
        "selected_trades": int(len(selected)),
        "worlds_traded": int(selected["world"].nunique()) if not selected.empty else 0,
        "net_pnl": float(pnl.sum()),
        "average_trade": float(trade_pnl.mean()) if len(trade_pnl) else 0.0,
        "median_trade": float(np.median(trade_pnl)) if len(trade_pnl) else 0.0,
        "win_rate": float((trade_pnl > 0).mean()) if len(trade_pnl) else 0.0,
        "profit_factor": gp / gl if gl else (math.inf if gp else 0.0),
        "max_drawdown": float(dd.min()),
        "worst_world": float(pnl.min()),
        "best_world": float(pnl.max()),
        "max_observed_risk": float(selected.groupby("world")["max_loss"].sum().max()) if not selected.empty else 0.0,
        "premium_pnl": float(selected.loc[selected["sleeve"] == "premium_selling", "pnl"].sum()) if not selected.empty else 0.0,
        "convex_pnl": float(selected.loc[selected["sleeve"] == "convex_directional", "pnl"].sum()) if not selected.empty else 0.0,
    }
    world_frame = pd.DataFrame({"world": world_ids, "pnl": pnl.values, "cumulative_pnl": equity.values, "drawdown": dd.values})
    return summary, world_frame


def grouped(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()
    return frame.groupby(columns, observed=False).agg(
        trades=("pnl", "size"),
        net_pnl=("pnl", "sum"),
        average_pnl=("pnl", "mean"),
        median_pnl=("pnl", "median"),
        win_rate=("profitable", "mean"),
        average_max_loss=("max_loss", "mean"),
    ).reset_index().sort_values("net_pnl", ascending=False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--development-root", type=Path, required=True)
    parser.add_argument("--validation-root", type=Path, required=True)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--risk-cap", type=float, default=900.0)
    args = parser.parse_args()

    development, _ = load_batches(args.development_root)
    validation, _ = load_batches(args.validation_root)
    evaluation, evaluation_worlds = load_batches(args.evaluation_root)

    promotions = promotion_table(development, validation)
    selected = select_portfolio(evaluation, promotions, args.risk_cap)
    summary, world_frame = summarize(selected, evaluation_worlds)
    summary.update({
        "status": "third-sample out-of-sample evaluation",
        "risk_cap": args.risk_cap,
        "promoted_models": promotions.loc[promotions["promoted"], "structure"].tolist(),
    })

    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    promotions.to_csv(output / "model_promotion_table.csv", index=False)
    selected.to_csv(output / "selected_trades.csv", index=False)
    world_frame.to_csv(output / "world_portfolio.csv", index=False)
    grouped(selected, ["sleeve", "structure"]).to_csv(output / "strategy_metrics.csv", index=False)
    adaptive = selected[selected["structure"].str.startswith("ADAPTIVE_IRON_CONDOR", na=False)]
    grouped(adaptive, ["structure", "exit_reason"]).to_csv(output / "adaptive_condor_exit_metrics.csv", index=False)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    strategy_metrics = grouped(selected, ["sleeve", "structure"])
    adaptive_metrics = grouped(adaptive, ["structure", "exit_reason"])
    lines = [
        "# Promoted Models with Adaptive Iron Condor — Third-Sample Test",
        "",
        "## Integrity",
        "",
        "- Development and validation samples are separate from the 1,000 evaluation worlds.",
        "- Promotion uses observable entry features only; hidden simulator regime and mispricing labels are excluded.",
        "- All positions are intraday, defined risk, and require no stock ownership.",
        "",
        "## Result",
        "",
        f"- Net P&L: **${summary['net_pnl']:,.2f}**",
        f"- Trades: **{summary['selected_trades']}** across **{summary['worlds_traded']}** worlds",
        f"- Win rate: **{summary['win_rate']:.1%}**",
        f"- Profit factor: **{summary['profit_factor']:.2f}**",
        f"- Maximum drawdown: **${summary['max_drawdown']:,.2f}**",
        f"- Premium / convex contribution: **${summary['premium_pnl']:,.2f} / ${summary['convex_pnl']:,.2f}**",
        "",
        "## Promotion Table",
        "",
        promotions.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Evaluation by Strategy",
        "",
        strategy_metrics.to_markdown(index=False, floatfmt=".3f") if not strategy_metrics.empty else "No selected trades.",
        "",
        "## Adaptive Condor Exit Attribution",
        "",
        adaptive_metrics.to_markdown(index=False, floatfmt=".3f") if not adaptive_metrics.empty else "No adaptive condor trades.",
    ]
    (output / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(promotions.to_string(index=False))


if __name__ == "__main__":
    main()
