from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from scipy.stats import t as student_t

from .v2_policy import CURRENT_POLICY_VERSION

_FORWARD_ACTUAL_CHAIN = "FORWARD_ACTUAL_CHAIN"
_ET = ZoneInfo("America/New_York")


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
    forward_sessions: int
    forward_net_pnl: float
    forward_mean_pnl: float | None
    forward_win_rate: float | None
    forward_profit_factor: float | None
    forward_average_process_score: float | None
    forward_worst_drawdown: float | None
    forward_lifecycle_error_rate: float | None
    forward_session_mean_pnl: float | None
    forward_session_win_rate: float | None
    forward_session_profit_factor: float | None
    forward_session_pnl_lcb90: float | None
    forward_session_pnl_lcb95: float | None
    policy_version: str
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
        and str(provenance.get("policy_version") or "") == CURRENT_POLICY_VERSION
        and str(thesis.get("policy_version") or "") == CURRENT_POLICY_VERSION
    )


def _session_key(opened_at: Any) -> str | None:
    try:
        parsed = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(_ET).date().isoformat()


def _session_pnl(records: list[dict[str, Any]]) -> list[float]:
    grouped: dict[str, float] = defaultdict(float)
    for record in records:
        key = record.get("session_key")
        if key:
            grouped[str(key)] += _num(record.get("pnl"))
    return [grouped[key] for key in sorted(grouped)]


def _one_sided_lower_bound(values: list[float], confidence: float) -> float | None:
    """Student-t lower bound for the mean of independent session P&L."""
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1)
    if variance <= 1e-12:
        return mean
    critical = float(student_t.ppf(confidence, df=n - 1))
    if not math.isfinite(critical):
        return None
    standard_error = math.sqrt(variance / n)
    return mean - critical * standard_error


def evaluate_playbooks(journal, *, limit: int = 1000) -> dict[str, dict[str, Any]]:
    """Govern Step 16 using current-policy forward actual-chain evidence only.

    Historical/synthetic/replay outcomes may reject or narrow a hypothesis, but
    they cannot promote it. Forward evidence from an older material policy version
    is also excluded from promotion, because changing the trading policy changes
    the statistical experiment.
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
                "session_key": _session_key(row["opened_at"]),
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

        session_pnl = _session_pnl(forward_records)
        forward_sessions = len(session_pnl)
        session_mean = sum(session_pnl) / forward_sessions if forward_sessions else None
        session_win = (
            sum(value > 0.0 for value in session_pnl) / forward_sessions
            if forward_sessions
            else None
        )
        session_pf = _profit_factor(session_pnl) if forward_sessions else None
        session_lcb90 = _one_sided_lower_bound(session_pnl, 0.90)
        session_lcb95 = _one_sided_lower_bound(session_pnl, 0.95)

        reasons: list[str] = []
        if samples < 8:
            status = "EXPERIMENTAL"
            eligible = False
            reasons.append("fewer_than_8_closed_examples")
        elif forward_samples < 20 or forward_sessions < 10:
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
                if forward_samples < 20:
                    reasons.append("fewer_than_20_current_policy_forward_actual_chain_examples")
                if forward_sessions < 10:
                    reasons.append("fewer_than_10_current_policy_independent_forward_sessions")
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
        elif session_lcb90 is None or session_lcb90 <= 0.0:
            status = "CHALLENGER"
            eligible = False
            reasons.append("forward_session_profit_not_positive_at_90pct_one_sided_confidence")
        elif forward_samples < 40 or forward_sessions < 20:
            status = "PROVISIONAL_REPEATABLE"
            eligible = True
            reasons.append("positive_current_policy_forward_record_passed_90pct_session_profit_bound")
            if forward_samples < 40:
                reasons.append("fewer_than_40_current_policy_forward_actual_chain_examples")
            if forward_sessions < 20:
                reasons.append("fewer_than_20_current_policy_independent_forward_sessions")
        else:
            pf_ok = forward_pf is None or forward_pf >= 1.20
            session_pf_ok = session_pf is None or session_pf >= 1.20
            confidence_ok = session_lcb95 is not None and session_lcb95 > 0.0
            if (
                forward_mean > 0.0
                and forward_win >= 0.50
                and pf_ok
                and session_pf_ok
                and confidence_ok
            ):
                status = "VALIDATED_PLAYBOOK"
                eligible = True
                reasons.append("current_policy_forward_session_profit_positive_at_95pct_one_sided_confidence")
            else:
                status = "CHALLENGER"
                eligible = False
                if not confidence_ok:
                    reasons.append("forward_session_profit_not_positive_at_95pct_one_sided_confidence")
                if not pf_ok or not session_pf_ok:
                    reasons.append("forward_profit_factor_below_1_20")
                if forward_win < 0.50:
                    reasons.append("forward_win_rate_below_50pct")

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
            forward_sessions=forward_sessions,
            forward_net_pnl=forward_net,
            forward_mean_pnl=forward_mean,
            forward_win_rate=forward_win,
            forward_profit_factor=forward_pf,
            forward_average_process_score=forward_process,
            forward_worst_drawdown=forward_drawdown,
            forward_lifecycle_error_rate=forward_lifecycle_error,
            forward_session_mean_pnl=session_mean,
            forward_session_win_rate=session_win,
            forward_session_profit_factor=session_pf,
            forward_session_pnl_lcb90=session_lcb90,
            forward_session_pnl_lcb95=session_lcb95,
            policy_version=CURRENT_POLICY_VERSION,
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
        "forward_sessions": 0,
        "forward_net_pnl": 0.0,
        "forward_mean_pnl": None,
        "forward_win_rate": None,
        "forward_profit_factor": None,
        "forward_average_process_score": None,
        "forward_worst_drawdown": None,
        "forward_lifecycle_error_rate": None,
        "forward_session_mean_pnl": None,
        "forward_session_win_rate": None,
        "forward_session_profit_factor": None,
        "forward_session_pnl_lcb90": None,
        "forward_session_pnl_lcb95": None,
        "policy_version": CURRENT_POLICY_VERSION,
        "execution_eligible": False,
        "reasons": ["no_current_policy_closed_examples_yet"],
    }
