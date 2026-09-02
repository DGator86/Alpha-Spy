from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from .regime import RegimeHierarchy

REGIMES = (
    "QUIET",
    "DIRECTIONAL_UP",
    "DIRECTIONAL_DOWN",
    "EXPANSION",
    "TRANSITION",
)


@dataclass(frozen=True)
class LifecycleForecast:
    forecast_id: str
    created_at: str
    source: str
    current_regime: str
    alpha_hierarchy: dict[str, Any]
    regime_age_minutes: float
    definable: bool
    confidence: float
    persistence_5: float
    persistence_15: float
    persistence_30: float
    hazard_0_5: float
    hazard_5_15: float
    hazard_15_30: float
    expected_remaining_minutes: float
    remaining_duration_quantiles: dict[str, float]
    successor_probabilities: dict[str, float]
    most_likely_successor: str
    successor_confidence: float
    matched_episodes: int
    effective_episodes: float
    calibration: dict[str, Any]
    beta_witness: dict[str, Any]
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = list(self.reasons)
        return out


def _iso(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _clip(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, _num(value)))


def canonical_alpha_regime(hierarchy: RegimeHierarchy) -> str:
    """Compress Alpha's hierarchy into the actionable lifecycle state alphabet.

    Alpha remains the authority: this mapping uses Alpha volatility, breadth,
    gamma, liquidity, event/risk-tone and cross-horizon conflict. Beta is not
    consulted here and therefore cannot define the market regime.
    """
    micro = hierarchy.micro
    intraday = hierarchy.intraday

    if hierarchy.conflict_score >= 0.65:
        return "TRANSITION"
    if intraday.event in {"macro_announcement", "rebalance", "unknown"}:
        return "TRANSITION"
    if intraday.liquidity == "thin":
        return "TRANSITION"

    expansion = (
        intraday.volatility in {"high", "crisis"}
        and (
            intraday.dealer_gamma == "negative_gamma"
            or micro.volatility in {"high", "crisis"}
            or intraday.volatility_term == "backwardation"
        )
    )
    if expansion:
        return "EXPANSION"

    bullish = (
        intraday.breadth == "broad_up"
        and intraday.risk_tone != "risk_off"
        and micro.breadth != "broad_down"
    )
    bearish = (
        intraday.breadth == "broad_down"
        and intraday.risk_tone != "risk_on"
        and micro.breadth != "broad_up"
    )
    if bullish:
        return "DIRECTIONAL_UP"
    if bearish:
        return "DIRECTIONAL_DOWN"

    quiet = (
        intraday.volatility in {"low", "normal"}
        and intraday.breadth == "mixed"
        and intraday.liquidity == "normal"
        and intraday.volatility_term != "backwardation"
        and intraday.event in {"ordinary", "earnings_heavy"}
    )
    if quiet:
        return "QUIET"
    return "TRANSITION"


def _beta_witness(beta: dict[str, Any] | None) -> dict[str, Any]:
    beta = beta or {}
    hgb = beta.get("hgb_direction") or {}
    state = beta.get("predictive_state") or {}
    beta_regime = beta.get("regime_forecast") or {}
    return {
        "direction_eligible": bool(hgb.get("eligible")) if isinstance(hgb, dict) else False,
        "direction": str(hgb.get("direction") or "") if isinstance(hgb, dict) else "",
        "strength": _num(hgb.get("strength")) if isinstance(hgb, dict) else 0.0,
        "p_big_15": _clip(state.get("p_big_15")) if isinstance(state, dict) else 0.0,
        "p_big_30": _clip(state.get("p_big_30")) if isinstance(state, dict) else 0.0,
        "p_reversal_15": _clip(state.get("p_reversal_15")) if isinstance(state, dict) else 0.0,
        "p_persistent_30": _clip(state.get("p_persistent_30")) if isinstance(state, dict) else 0.0,
        "beta_regime": str(beta_regime.get("current_regime") or "UNDEFINED") if isinstance(beta_regime, dict) else "UNDEFINED",
        "beta_regime_confidence": _clip(beta_regime.get("confidence")) if isinstance(beta_regime, dict) else 0.0,
    }


def _weighted_quantile(values: list[float], weights: list[float], probability: float) -> float:
    if not values:
        return 0.0
    rows = sorted(zip(values, weights, strict=False), key=lambda row: row[0])
    total = sum(max(0.0, weight) for _, weight in rows)
    if total <= 0.0:
        return rows[len(rows) // 2][0]
    threshold = probability * total
    running = 0.0
    for value, weight in rows:
        running += max(0.0, weight)
        if running >= threshold:
            return float(value)
    return float(rows[-1][0])


def _effective_sample_size(weights: list[float]) -> float:
    total = sum(weights)
    square = sum(weight * weight for weight in weights)
    if total <= 0.0 or square <= 0.0:
        return 0.0
    return total * total / square


class AlphaRegimeLifecycleEngine:
    """Empirical survival/transition model downstream of Alpha's regime hierarchy.

    Each V2 observation is persisted. Completed historical Alpha regime episodes
    become the only training outcomes. The current episode is right-censored and
    never used as a completed target. Beta constituent/HGB information is used only
    to weight analogous prior episodes; it never defines the current regime.
    """

    def __init__(self, journal) -> None:
        self.journal = journal
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.journal.transaction() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS v2_regime_observations (
                    observation_id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    captured_at TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    canonical_regime TEXT NOT NULL,
                    micro_key TEXT NOT NULL,
                    intraday_key TEXT NOT NULL,
                    swing_key TEXT NOT NULL,
                    structural_key TEXT NOT NULL,
                    conflict_score REAL NOT NULL,
                    transition_risk INTEGER NOT NULL,
                    beta_direction TEXT,
                    beta_strength REAL NOT NULL DEFAULT 0,
                    beta_p_big_15 REAL NOT NULL DEFAULT 0,
                    beta_p_big_30 REAL NOT NULL DEFAULT 0,
                    beta_p_reversal_15 REAL NOT NULL DEFAULT 0,
                    beta_p_persistent_30 REAL NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v2_regime_obs_time
                    ON v2_regime_observations(captured_at);
                CREATE INDEX IF NOT EXISTS idx_v2_regime_obs_state
                    ON v2_regime_observations(canonical_regime,captured_at);

                CREATE TABLE IF NOT EXISTS v2_lifecycle_forecasts (
                    forecast_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    snapshot_id TEXT NOT NULL,
                    current_regime TEXT NOT NULL,
                    regime_age_minutes REAL NOT NULL,
                    p_survive_5 REAL NOT NULL,
                    p_survive_15 REAL NOT NULL,
                    p_survive_30 REAL NOT NULL,
                    expected_remaining_minutes REAL NOT NULL,
                    successor_probabilities_json TEXT NOT NULL,
                    source TEXT NOT NULL,
                    scored_at TEXT,
                    score_json TEXT,
                    payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_v2_lifecycle_created
                    ON v2_lifecycle_forecasts(created_at);
                """
            )

    def record_observation(
        self,
        *,
        snapshot_id: str,
        captured_at: str,
        hierarchy: RegimeHierarchy,
        beta: dict[str, Any] | None,
    ) -> dict[str, Any]:
        regime = canonical_alpha_regime(hierarchy)
        witness = _beta_witness(beta)
        stamp = _iso(captured_at)
        payload = {
            "alpha_hierarchy": hierarchy.as_dict(),
            "beta_witness": witness,
            "regime_authority": "alpha_hierarchical_regime",
        }
        with self.journal.transaction() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO v2_regime_observations(
                    observation_id,snapshot_id,captured_at,session_date,canonical_regime,
                    micro_key,intraday_key,swing_key,structural_key,conflict_score,
                    transition_risk,beta_direction,beta_strength,beta_p_big_15,
                    beta_p_big_30,beta_p_reversal_15,beta_p_persistent_30,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"RO-{uuid.uuid4().hex[:16]}",
                    snapshot_id,
                    stamp.isoformat(),
                    stamp.date().isoformat(),
                    regime,
                    hierarchy.micro.key,
                    hierarchy.intraday.key,
                    hierarchy.swing.key,
                    hierarchy.structural.key,
                    hierarchy.conflict_score,
                    int(bool(hierarchy.transition_risk)),
                    witness["direction"],
                    witness["strength"],
                    witness["p_big_15"],
                    witness["p_big_30"],
                    witness["p_reversal_15"],
                    witness["p_persistent_30"],
                    self.journal._json(payload),
                ),
            )
        return {
            "canonical_regime": regime,
            "alpha_hierarchy": hierarchy.as_dict(),
            "beta_witness": witness,
        }

    def _rows(self, *, limit: int = 6000) -> list[dict[str, Any]]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT * FROM v2_regime_observations
                ORDER BY captured_at ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _episodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        episodes: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        previous_time: datetime | None = None
        for row in rows:
            stamp = _iso(row["captured_at"])
            same_episode = bool(
                current
                and row["session_date"] == current["session_date"]
                and row["canonical_regime"] == current["regime"]
                and previous_time is not None
                and (stamp - previous_time).total_seconds() <= 15 * 60
            )
            if not same_episode:
                if current is not None:
                    current["last_observed_at"] = previous_time
                    episodes.append(current)
                current = {
                    "session_date": row["session_date"],
                    "regime": row["canonical_regime"],
                    "started_at": stamp,
                    "last_observed_at": stamp,
                    "start_row": row,
                    "successor": None,
                    "completed": False,
                    "duration_minutes": None,
                }
            else:
                current["last_observed_at"] = stamp
            previous_time = stamp

        if current is not None:
            episodes.append(current)

        for index, episode in enumerate(episodes[:-1]):
            following = episodes[index + 1]
            if following["session_date"] != episode["session_date"]:
                continue
            episode["successor"] = following["regime"]
            episode["completed"] = True
            episode["duration_minutes"] = max(
                1.0,
                (following["started_at"] - episode["started_at"]).total_seconds() / 60.0,
            )
        return episodes

    @staticmethod
    def _similarity(current_row: dict[str, Any], episode: dict[str, Any]) -> float:
        row = episode["start_row"]
        score = 1.0
        score *= 1.35 if row["intraday_key"] == current_row["intraday_key"] else 0.82
        score *= 1.18 if row["micro_key"] == current_row["micro_key"] else 0.92
        score *= 1.10 if row["swing_key"] == current_row["swing_key"] else 0.96
        score *= 1.06 if row["structural_key"] == current_row["structural_key"] else 0.98
        score *= math.exp(-abs(_num(row["conflict_score"]) - _num(current_row["conflict_score"])) / 0.35)
        if str(row.get("beta_direction") or "") == str(current_row.get("beta_direction") or ""):
            score *= 1.08
        distance = (
            abs(_num(row.get("beta_strength")) - _num(current_row.get("beta_strength"))) / 0.75
            + abs(_num(row.get("beta_p_big_15")) - _num(current_row.get("beta_p_big_15")))
            + abs(_num(row.get("beta_p_reversal_15")) - _num(current_row.get("beta_p_reversal_15")))
            + abs(_num(row.get("beta_p_persistent_30")) - _num(current_row.get("beta_p_persistent_30")))
        )
        score *= math.exp(-0.65 * distance)
        return max(1e-6, score)

    def _calibration(self, *, limit: int = 300) -> dict[str, Any]:
        with self.journal.session() as con:
            rows = con.execute(
                """
                SELECT score_json FROM v2_lifecycle_forecasts
                WHERE score_json IS NOT NULL
                ORDER BY scored_at DESC LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        scores: list[dict[str, Any]] = []
        for row in rows:
            try:
                value = json.loads(row["score_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                scores.append(value)
        briers = [
            _num(score.get("mean_survival_brier"), -1.0)
            for score in scores
            if _num(score.get("mean_survival_brier"), -1.0) >= 0.0
        ]
        transition = [
            float(bool(score.get("transition_correct")))
            for score in scores
            if score.get("transition_correct") is not None
        ]
        return {
            "scored_forecasts": len(scores),
            "mean_survival_brier": sum(briers) / len(briers) if briers else None,
            "transition_accuracy": sum(transition) / len(transition) if transition else None,
        }

    def _fallback(
        self,
        *,
        hierarchy: RegimeHierarchy,
        regime: str,
        witness: dict[str, Any],
        age: float,
    ) -> tuple[float, float, float, float, dict[str, float], list[str]]:
        pbig15 = witness["p_big_15"]
        pbig30 = witness["p_big_30"]
        reversal = witness["p_reversal_15"]
        persistent = witness["p_persistent_30"]
        if regime == "QUIET":
            p5 = 1.0 - 0.45 * pbig15
            p15 = 1.0 - pbig15
            p30 = 1.0 - pbig30
        elif regime in {"DIRECTIONAL_UP", "DIRECTIONAL_DOWN"}:
            p5 = 1.0 - 0.55 * reversal
            p15 = (1.0 - reversal) * max(0.45, persistent)
            p30 = persistent
        elif regime == "EXPANSION":
            p5 = max(0.35, pbig15)
            p15 = pbig15
            p30 = pbig30
        else:
            p5 = max(0.20, 0.60 - 0.30 * hierarchy.conflict_score)
            p15 = max(0.10, 0.45 - 0.35 * hierarchy.conflict_score)
            p30 = max(0.05, 0.30 - 0.25 * hierarchy.conflict_score)
        p5 = _clip(p5)
        p15 = min(p5, _clip(p15))
        p30 = min(p15, _clip(p30))
        expected = max(5.0, min(30.0, 5.0 + 10.0 * p15 + 15.0 * p30 - 0.25 * age))

        up = 0.5
        if witness["direction"] == "BULLISH":
            up = 0.65
        elif witness["direction"] == "BEARISH":
            up = 0.35
        transition_mass = max(0.15, 1.0 - p30)
        raw = {
            "QUIET": 0.35 if regime != "QUIET" else 0.10,
            "DIRECTIONAL_UP": transition_mass * up,
            "DIRECTIONAL_DOWN": transition_mass * (1.0 - up),
            "EXPANSION": max(0.05, pbig30),
            "TRANSITION": max(0.10, hierarchy.conflict_score),
        }
        raw[regime] = 0.0
        total = sum(raw.values()) or 1.0
        successors = {name: value / total for name, value in raw.items()}
        return p5, p15, p30, expected, successors, ["empirical_episode_support_insufficient"]

    def forecast(
        self,
        *,
        snapshot_id: str,
        captured_at: str,
        hierarchy: RegimeHierarchy,
        beta: dict[str, Any] | None,
    ) -> LifecycleForecast:
        self.score_matured_forecasts(now=_iso(captured_at))
        observation = self.record_observation(
            snapshot_id=snapshot_id,
            captured_at=captured_at,
            hierarchy=hierarchy,
            beta=beta,
        )
        regime = observation["canonical_regime"]
        witness = observation["beta_witness"]
        rows = self._rows()
        episodes = self._episodes(rows)
        current_episode = episodes[-1] if episodes else None
        stamp = _iso(captured_at)
        age = (
            max(0.0, (stamp - current_episode["started_at"]).total_seconds() / 60.0)
            if current_episode is not None
            else 0.0
        )
        current_row = rows[-1] if rows else {}

        historical = [
            episode
            for episode in episodes[:-1]
            if episode.get("completed")
            and episode.get("regime") == regime
            and _num(episode.get("duration_minutes")) >= age
        ]
        weights = [self._similarity(current_row, episode) for episode in historical]
        ess = _effective_sample_size(weights)
        calibration = self._calibration()
        reasons: list[str] = ["alpha_hierarchy_is_regime_authority"]

        if len(historical) >= 10 and ess >= 6.0:
            remaining = [max(0.0, _num(ep["duration_minutes"]) - age) for ep in historical]
            total_weight = sum(weights) or 1.0

            def survival(horizon: float) -> float:
                return sum(weight for rem, weight in zip(remaining, weights, strict=False) if rem >= horizon) / total_weight

            p5 = _clip(survival(5.0))
            p15 = min(p5, _clip(survival(15.0)))
            p30 = min(p15, _clip(survival(30.0)))
            expected = sum(rem * weight for rem, weight in zip(remaining, weights, strict=False)) / total_weight
            successor_raw = {name: 0.0 for name in REGIMES}
            for episode, weight in zip(historical, weights, strict=False):
                successor = str(episode.get("successor") or "TRANSITION")
                if successor in successor_raw and successor != regime:
                    successor_raw[successor] += weight
            successor_total = sum(successor_raw.values())
            if successor_total <= 0.0:
                successors = {name: (1.0 if name == "TRANSITION" else 0.0) for name in REGIMES}
            else:
                successors = {name: value / successor_total for name, value in successor_raw.items()}
            source = "EMPIRICAL_ALPHA_EPISODE_SURVIVAL"
            reasons.append("completed_prior_alpha_regime_episodes_conditioned_on_current_age")
        else:
            p5, p15, p30, expected, successors, fallback_reasons = self._fallback(
                hierarchy=hierarchy,
                regime=regime,
                witness=witness,
                age=age,
            )
            remaining = []
            source = "PROVISIONAL_ALPHA_PLUS_BETA_WITNESS_FALLBACK"
            reasons.extend(fallback_reasons)

        brier = calibration.get("mean_survival_brier")
        calibration_factor = 1.0 if brier is None else max(0.35, 1.0 - min(1.0, float(brier)))
        alpha_support = min(1.0, max(0.0, 1.0 - hierarchy.conflict_score / 1.25))
        empirical_support = min(1.0, ess / 18.0) if source.startswith("EMPIRICAL") else 0.35
        confidence = _clip(0.45 * alpha_support + 0.40 * empirical_support + 0.15 * calibration_factor)
        definable = bool(confidence >= 0.40 and source.startswith("EMPIRICAL") and ess >= 6.0)
        if not definable:
            reasons.append("lifecycle_empirical_support_not_yet_production_grade")

        q = {
            "p10": _weighted_quantile(remaining, weights, 0.10) if remaining else max(0.0, 0.35 * expected),
            "p25": _weighted_quantile(remaining, weights, 0.25) if remaining else max(0.0, 0.60 * expected),
            "p50": _weighted_quantile(remaining, weights, 0.50) if remaining else expected,
            "p75": _weighted_quantile(remaining, weights, 0.75) if remaining else 1.30 * expected,
            "p90": _weighted_quantile(remaining, weights, 0.90) if remaining else 1.60 * expected,
        }
        hazard0_5 = 1.0 - p5
        hazard5_15 = 1.0 - (p15 / p5 if p5 > 1e-9 else 0.0)
        hazard15_30 = 1.0 - (p30 / p15 if p15 > 1e-9 else 0.0)
        successor_candidates = {name: prob for name, prob in successors.items() if name != regime}
        successor = max(successor_candidates, key=successor_candidates.get, default="TRANSITION")
        successor_conf = float(successor_candidates.get(successor, 0.0))
        forecast = LifecycleForecast(
            forecast_id=f"LC-{uuid.uuid4().hex[:16]}",
            created_at=stamp.isoformat(),
            source=source,
            current_regime=regime,
            alpha_hierarchy=hierarchy.as_dict(),
            regime_age_minutes=age,
            definable=definable,
            confidence=confidence,
            persistence_5=p5,
            persistence_15=p15,
            persistence_30=p30,
            hazard_0_5=_clip(hazard0_5),
            hazard_5_15=_clip(hazard5_15),
            hazard_15_30=_clip(hazard15_30),
            expected_remaining_minutes=max(0.0, expected),
            remaining_duration_quantiles=q,
            successor_probabilities=successors,
            most_likely_successor=successor,
            successor_confidence=successor_conf,
            matched_episodes=len(historical),
            effective_episodes=ess,
            calibration=calibration,
            beta_witness=witness,
            reasons=tuple(reasons),
        )
        with self.journal.transaction() as con:
            con.execute(
                """
                INSERT OR REPLACE INTO v2_lifecycle_forecasts(
                    forecast_id,created_at,snapshot_id,current_regime,regime_age_minutes,
                    p_survive_5,p_survive_15,p_survive_30,expected_remaining_minutes,
                    successor_probabilities_json,source,payload_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    forecast.forecast_id,
                    forecast.created_at,
                    snapshot_id,
                    forecast.current_regime,
                    forecast.regime_age_minutes,
                    forecast.persistence_5,
                    forecast.persistence_15,
                    forecast.persistence_30,
                    forecast.expected_remaining_minutes,
                    self.journal._json(forecast.successor_probabilities),
                    forecast.source,
                    self.journal._json(forecast.as_dict()),
                ),
            )
        return forecast

    def score_matured_forecasts(self, *, now: datetime | None = None, limit: int = 200) -> int:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        cutoff = (current - timedelta(minutes=30)).isoformat()
        with self.journal.session() as con:
            forecasts = con.execute(
                """
                SELECT * FROM v2_lifecycle_forecasts
                WHERE score_json IS NULL AND created_at<=?
                ORDER BY created_at ASC LIMIT ?
                """,
                (cutoff, int(limit)),
            ).fetchall()
        scored = 0
        for raw in forecasts:
            forecast = dict(raw)
            start = _iso(forecast["created_at"])
            end = start + timedelta(minutes=35)
            with self.journal.session() as con:
                observations = con.execute(
                    """
                    SELECT captured_at,canonical_regime FROM v2_regime_observations
                    WHERE captured_at>=? AND captured_at<=?
                    ORDER BY captured_at ASC
                    """,
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
            if not observations:
                continue
            initial = str(forecast["current_regime"])
            transition_time: datetime | None = None
            actual_successor: str | None = None
            obs = [( _iso(row["captured_at"]), str(row["canonical_regime"]) ) for row in observations]
            for stamp, regime in obs:
                if stamp > start and regime != initial:
                    transition_time = stamp
                    actual_successor = regime
                    break

            briers: list[float] = []
            actual_survival: dict[str, int] = {}
            for horizon, column in ((5, "p_survive_5"), (15, "p_survive_15"), (30, "p_survive_30")):
                target = start + timedelta(minutes=horizon)
                survived = int(transition_time is None or transition_time > target)
                actual_survival[str(horizon)] = survived
                prob = _clip(forecast[column])
                briers.append((prob - survived) ** 2)

            successor_probs = json.loads(forecast["successor_probabilities_json"] or "{}")
            transition_correct = None if actual_successor is None else actual_successor == max(
                {k: v for k, v in successor_probs.items() if k != initial},
                key=lambda key: successor_probs[key],
                default="TRANSITION",
            )
            actual_remaining = (
                (transition_time - start).total_seconds() / 60.0
                if transition_time is not None
                else None
            )
            score = {
                "actual_survival": actual_survival,
                "mean_survival_brier": sum(briers) / len(briers),
                "actual_remaining_minutes": actual_remaining,
                "duration_absolute_error": (
                    abs(_num(forecast["expected_remaining_minutes"]) - actual_remaining)
                    if actual_remaining is not None
                    else None
                ),
                "duration_censored_at_30m": actual_remaining is None,
                "actual_successor": actual_successor,
                "transition_correct": transition_correct,
                "actual_successor_probability": (
                    _num(successor_probs.get(actual_successor)) if actual_successor else None
                ),
            }
            with self.journal.transaction() as con:
                con.execute(
                    """
                    UPDATE v2_lifecycle_forecasts
                    SET scored_at=?,score_json=? WHERE forecast_id=?
                    """,
                    (current.isoformat(), self.journal._json(score), forecast["forecast_id"]),
                )
            scored += 1
        return scored
