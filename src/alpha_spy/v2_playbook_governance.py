from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from typing import Any

_FORWARD_ACTUAL_CHAIN = "FORWARD_ACTUAL_CHAIN"


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
    forward_actual_chain_samples: int
    forward_net_pnl: float
    forward_mean_pnl: float | None
    forward_win_rate: float | None
    forward_profit_factor: float | None
    forward_average_process_score: float | None
    forward_worst_drawdown: float | None
    forward_lifecycle_error_rate: float | None
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


def _profit_factor(values: list[float]) -> float | None:
    gross_profit = sum(value for value in values if value > 0.0)
    gross_loss = abs(sum(value for value in values if value < 0.0))
    return gross_profit / gross_loss if gross_loss > 0.0 else None


def _review_metrics(records: list[dict[str, Any]]) -> tuple[float | None, float | None]:
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
    average_process = sum(process_scores) / len(process_scores) if process_scores else None
    lifecycle_error = lifecycle_failures / lifecycle_known if lifecycle_known else None
    return average_process, lifecycle_error


def _is_forward_actual_chain(thesis: dict[str, Any]) -> bool:
    provenance = thesis.get("evidence_provenance") or {}
    return (
        isinstance(provenance, dict)
        and str(provenance.get("evidence_class") or "") == _FORWARD_ACTUAL_CHAIN
        and provenance.get("actual_chain") is True
    )


def evaluate_playbooks(journal, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
    """Govern Step 16 from closed trades with explicit evidence lineage.

    Historical/synthetic/replay outcomes may reject or narrow a hypothesis, but
    they cannot promote it. PROVISIONAL_REPEATABLE and VALIDATED_PLAYBOOK require
    untouched forward trades built from fresh verified actual option chains.
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
                "forward_actual_chain": _is_forward_actual_chain(thesis),
            }
        )

    output: dict[str, dict[str, Any]] = {}
    for playbook, records in grouped.items():
        pnl = [record["pnl"] for record in records]
        samples = len(pnl)
        wins = [value for value in pnl if value > 0.0]
        mean_pnl = sum(pnl) / max(samples, 1)
        win_rate = len(wins) / max(samples, 1)
        profit_factor = _profit_factor(pnl)
        avg_process, lifecycle_error = _review_metrics(records)

        forward_records = [record for record in records if record["forward_actual_chain"]]
        forward_pnl = [record["pnl"] for record in forward_records]
        forward_samples = len(forward_pnl)
        forward_net = sum(forward_pnl)
        forward_mean = forward_net / forward_samples if forward_samples else None
        forward_win = (
            sum(value > 0.0 for value in forward_pnl) / forward_samples
            if forward_samples
            else None
        )
        forward_pf = _profit_factor(forward_pnl) if forward_samples else None
        forward_process, forward_lifecycle_error = _review_metrics(forward_records)
        forward_drawdown = _drawdown(forward_pnl) if forward_samples else None

        reasons: list[str] = []
        if samples < 8:
            status = "EXPERIMENTAL"
            eligible = False
            reasons.append("fewer_than_8_independent_closed_examples")
        elif forward_samples < 20:
            poor_research_record = bool(
                samples >= 20
                and (
                    mean_pnl <= 0.0
                    or win_rate < 0.40
                    or (avg_process is not None and avg_process < 0.60)
                )
            )
            if poor_research_record:
                status = "NARROW_OR_RETIRE"
                reasons.append("research_record_is_poor_before_forward_promotion_gate")
            else:
                status = "CHALLENGER"
                reasons.append("fewer_than_20_forward_actual_chain_examples")
            eligible = False
        elif forward_mean is None or forward_win is None:
            status = "CHALLENGER"
            eligible = False
            reasons.append("forward_metrics_unavailable")
        elif forward_mean <= 0.0 or forward_win < 0.45:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("forward_realized_action_value_not_positive_enough")
        elif forward_process is not None and forward_process < 0.70:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("forward_process_quality_below_threshold")
        elif forward_lifecycle_error is not None and forward_lifecycle_error > 0.45:
            status = "NARROW_OR_RETIRE"
            eligible = False
            reasons.append("forward_lifecycle_assumptions_fail_too_often")
        elif forward_samples < 40:
            status = "PROVISIONAL_REPEATABLE"
            eligible = True
            reasons.append("positive_20_plus_forward_actual_chain_record_requires_more_evidence")
        else:
            pf_ok = forward_pf is None or forward_pf >= 1.20
            if forward_mean > 0.0 and forward_win >= 0.50 and pf_ok:
                status = "VALIDATED_PLAYBOOK"
                eligible = True
                reasons.append("forty_plus_forward_actual_chain_examples_with_positive_action_value")
            else:
                status = "CHALLENGER"
                eligible = False
                reasons.append("large_forward_sample_record_not_robust_enough_for_validation")

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
            forward_actual_chain_samples=forward_samples,
            forward_net_pnl=forward_net,
            forward_mean_pnl=forward_mean,
            forward_win_rate=forward_win,
            forward_profit_factor=forward_pf,
            forward_average_process_score=forward_process,
            forward_worst_drawdown=forward_drawdown,
            forward_lifecycle_error_rate=forward_lifecycle_error,
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
        "forward_actual_chain_samples": 0,
        "forward_net_pnl": 0.0,
        "forward_mean_pnl": None,
        "forward_win_rate": None,
        "forward_profit_factor": None,
        "forward_average_process_score": None,
        "forward_worst_drawdown": None,
        "forward_lifecycle_error_rate": None,
        "execution_eligible": False,
        "reasons": ["no_closed_examples_yet"],
    }
