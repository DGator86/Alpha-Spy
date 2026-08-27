from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from typing import Any


@dataclass(frozen=True)
class PlaybookStatus:
    playbook: str
    status: str
    samples: int
    net_pnl: float
    mean_pnl: float
    win_rate: float
    profit_factor: float | None
    average_process_score: float | None
    worst_drawdown: float
    lifecycle_error_rate: float | None
    execution_eligible: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = list(self.reasons)
        return out


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def evaluate_playbooks(journal, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
    """Govern Step 16 from closed trades only.

    This layer never mutates model coefficients. It decides whether a setup remains
    experimental, may challenge, is provisionally repeatable, is validated enough
    for the paper playbook, should be narrowed, or should be retired. Tiny samples
    cannot promote a playbook regardless of headline P&L.
    """
    with journal.session() as con:
        rows = con.execute(
            """
            SELECT opened_at,closed_at,realized_pnl,payload_json
            FROM positions
            WHERE status='CLOSED' AND realized_pnl IS NOT NULL
            ORDER BY opened_at ASC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        candidate = payload.get("candidate") or {}
        inner = candidate.get("payload") or {}
        thesis = inner.get("trade_thesis") or payload.get("trade_thesis") or {}
        if not isinstance(thesis, dict):
            continue
        playbook = str(thesis.get("playbook") or "")
        if not playbook:
            continue
        review = payload.get("post_trade_review") or {}
        grouped.setdefault(playbook, []).append(
            {
                "pnl": _num(row["realized_pnl"]),
                "review": review if isinstance(review, dict) else {},
            }
        )

    output: dict[str, dict[str, Any]] = {}
    for playbook, records in grouped.items():
        pnl = [record["pnl"] for record in records]
        samples = len(pnl)
        wins = [value for value in pnl if value > 0]
        losses = [value for value in pnl if value < 0]
        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
        process_scores = [
            _num(record["review"].get("process_score"), -1.0)
            for record in records
            if _num(record["review"].get("process_score"), -1.0) >= 0.0
        ]
        lifecycle_failures = 0
        lifecycle_known = 0
        for record in records:
            components = record["review"].get("component_attribution") or {}
            if not isinstance(components, dict):
                continue
            for name in ("regime_identification", "duration_forecast", "transition_forecast"):
                item = components.get(name) or {}
                status = str(item.get("status") or "UNKNOWN") if isinstance(item, dict) else "UNKNOWN"
                if status in {"PASS", "FAIL"}:
                    lifecycle_known += 1
                    lifecycle_failures += int(status == "FAIL")

        mean_pnl = sum(pnl) / max(samples, 1)
        win_rate = len(wins) / max(samples, 1)
        avg_process = sum(process_scores) / len(process_scores) if process_scores else None
        lifecycle_error = lifecycle_failures / lifecycle_known if lifecycle_known else None
        reasons: list[str] = []

        if samples < 8:
            status = "EXPERIMENTAL"
            eligible = False
            reasons.append("fewer_than_8_independent_closed_examples")
        elif samples < 20:
            status = "CHALLENGER"
            eligible = False
            reasons.append("sample_support_below_repeatability_threshold")
        elif mean_pnl <= 0.0 or win_rate < 0.45:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("realized_action_value_not_positive_enough")
        elif avg_process is not None and avg_process < 0.70:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("process_quality_below_threshold")
        elif lifecycle_error is not None and lifecycle_error > 0.45:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("lifecycle_assumptions_fail_too_often")
        elif samples < 40:
            status = "PROVISIONAL_REPEATABLE"
            eligible = True
            reasons.append("positive_20_plus_sample_record_requires_more_forward_evidence")
        else:
            pf_ok = profit_factor is None or profit_factor >= 1.20
            if mean_pnl > 0.0 and win_rate >= 0.50 and pf_ok:
                status = "VALIDATED_PLAYBOOK"
                eligible = True
                reasons.append("forty_plus_closed_examples_with_positive_action_value")
            else:
                status = "CHALLENGER"
                eligible = False
                reasons.append("large_sample_record_not_robust_enough_for_validation")

        output[playbook] = PlaybookStatus(
            playbook=playbook,
            status=status,
            samples=samples,
            net_pnl=sum(pnl),
            mean_pnl=mean_pnl,
            win_rate=win_rate,
            profit_factor=profit_factor,
            average_process_score=avg_process,
            worst_drawdown=_drawdown(pnl),
            lifecycle_error_rate=lifecycle_error,
            execution_eligible=eligible,
            reasons=tuple(reasons),
        ).as_dict()
    return output


def governance_for(playbook: str, governance: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
    governance = governance or {}
    row = governance.get(playbook)
    if isinstance(row, dict):
        return row
    return {
        "playbook": playbook,
        "status": "EXPERIMENTAL",
        "samples": 0,
        "net_pnl": 0.0,
        "mean_pnl": 0.0,
        "win_rate": 0.0,
        "profit_factor": None,
        "average_process_score": None,
        "worst_drawdown": 0.0,
        "lifecycle_error_rate": None,
        "execution_eligible": False,
        "reasons": ["no_closed_examples_yet"],
    }
