/**
 * Wire types for the dashboard state published by `alpha_spy.state.build_dashboard_state`.
 *
 * Nearly every field is nullable. The engine publishes a partial state before
 * the first snapshot matures, and a panel that assumes a number is present will
 * render `NaN` on a cold start — which on a risk screen is worse than rendering
 * nothing. `Num` makes that explicit at every call site.
 */
export type Num = number | null | undefined
export type Str = string | null | undefined

export interface Engine {
  name?: string
  version?: string
  environment?: string
  mode?: string
  market_data_environment?: string
  market_stream_enabled?: boolean
}

export interface Session {
  market_open?: boolean
  exchange_time?: string
  entry_window?: string
  entry_grid_minutes?: number
  exit_monitor_seconds?: number
  forced_flat_time?: string
}

export interface RegimeLevel {
  label?: string
  key?: string
  history_samples?: number
  evidence?: string
  [k: string]: unknown
}

export interface RegimeHierarchy {
  micro?: RegimeLevel
  intraday?: RegimeLevel
  swing?: RegimeLevel
  structural?: RegimeLevel
  transition_risk?: Num
  [k: string]: unknown
}

export interface Market {
  symbol?: string
  price?: Num
  bid?: Num
  ask?: Num
  spread?: Num
  change?: Num
  change_pct?: Num
  predicted_price_15m?: Num
  predicted_low_15m?: Num
  predicted_high_15m?: Num
  probability_up?: Num
  probability_down?: Num
  expected_return_15m?: Num
  raw_expected_return_15m?: Num
  regime?: Str
  regime_state?: Record<string, unknown>
  regime_hierarchy?: RegimeHierarchy
  market_context?: Record<string, unknown>
  gamma_state?: Str
  gamma_proxy?: Record<string, unknown>
  liquidity_state?: Str
  event_state?: Str
  event_source?: Str
  breadth?: Num
  pressure?: Num
  concentration?: Num
  dispersion?: Num
  correlation?: Num
  downside_correlation?: Num
  physical_vol?: Num
  constituent_iv?: Num
  spy_iv?: Num
  vol_gap?: Num
  skew_gap?: Num
  iv_reference_expiration?: Str
  iv_coverage?: Num
  option_activity?: Record<string, unknown>
  signal_model?: Record<string, unknown>
}

export interface Quantiles {
  p10?: Num
  p25?: Num
  p50?: Num
  p75?: Num
  p90?: Num
  [k: string]: Num
}

export interface HorizonForecast {
  created_at?: Str
  target_at?: Str
  role?: Str
  horizon_minutes?: number
  expected_return?: Num
  probability_up?: Num
  predicted_price?: Num
  predicted_low?: Num
  predicted_high?: Num
  sigma_return?: Num
  model_uncertainty?: Num
  integrity?: Str
  path?: Record<string, unknown>
  distribution?: {
    quantiles?: Quantiles
    physical_sigma?: Num
    risk_neutral_sigma?: Num
    [k: string]: unknown
  }
  signal_model?: Record<string, unknown>
  shadow_model?: Record<string, unknown>
}

/** One entry gate from `alpha_spy.risk.evaluate_entry_gates`. */
export interface DecisionGate {
  name: string
  label?: string
  kind?: 'veto' | 'qualifier' | string
  passed: boolean
  reason?: string
  detail?: string
}

export interface Decision {
  decision_id?: Str
  prediction_id?: Str
  candidate_id?: Str
  created_at?: Str
  action?: Str
  reason?: Str
  allowed_risk?: Num
  trust_score?: Num
  health_state?: Str
  gates?: DecisionGate[]
  failed_gates?: string[]
  candidate?: Candidate | null
  trades_today?: Num
  considered_candidates?: Num
  affordable_candidates?: Num
}

export interface Leg {
  side?: string
  symbol?: string
  strike?: Num
  type?: string
  right?: string
  quantity?: Num
}

export interface Candidate {
  candidate_id?: Str
  strategy?: Str
  status?: Str
  score?: Num
  expected_value?: Num
  probability_profit?: Num
  max_loss?: Num
  max_profit?: Num
  entry_value?: Num
  legs?: Leg[]
  rejection_reason?: Str
  valuation_method?: Str
  q_executable_edge?: Num
  stress_expected_value?: Num
  doubled_cost_expected_value?: Num
  breakevens?: number[]
  greeks?: Record<string, Num>
  payload?: Record<string, unknown>
}

export interface Position {
  open?: boolean
  position_id?: Str
  strategy?: Str
  description?: Str
  quantity?: Num
  entry_debit?: Num
  entry_fees?: Num
  current_value?: Num
  pnl?: Num
  pnl_pct?: Num
  max_loss?: Num
  max_profit?: Num
  mfe?: Num
  mae?: Num
  profit_target?: Num
  stop_loss?: Num
  trailing_floor?: Num
  thesis_status?: Str
  exit_recommendation?: Str
  management_state?: Record<string, unknown>
  broker_reconciliation?: Record<string, unknown>
  opened_at?: Str
  legs?: Leg[]
}

export interface Account {
  equity?: Num
  cash?: Num
  buying_power?: Num
  daily_pnl?: Num
  daily_pnl_pct?: Num
  valid?: boolean
  source?: Str
  reason?: Str
  daily_loss_limit?: Num
  base_risk?: Num
  allowed_risk?: Num
}

export interface Health {
  state?: Str
  trust_score?: Num
  input_health?: Record<string, unknown>
  components?: Record<string, Num>
}

export interface Prediction {
  prediction_id: string
  created_at?: Str
  target_at?: Str
  horizon_minutes?: number
  spy_price?: Num
  predicted_price?: Num
  predicted_low?: Num
  predicted_high?: Num
  probability_up?: Num
  actual_price?: Num
  direction_correct?: boolean | null
  integrity?: Str
  model_version?: Str
  payload?: Record<string, unknown>
}

export interface Alert {
  id?: number
  severity?: 'info' | 'warning' | 'critical' | string
  title?: string
  message?: string
  source?: string
  timestamp?: string
  acknowledged?: boolean
}

export interface CommandRow {
  id?: number
  created_at?: string
  command?: string
  status?: string
  reason?: string
  message?: string
  completed_at?: string
}

/** One paper-validation gate from `alpha_spy.validation`. */
export interface ValidationGate {
  name: string
  passed: boolean
  actual?: unknown
  threshold?: unknown
  detail?: string
}

export interface Promotion {
  status?: Str
  validation_id?: Str
  created_at?: Str
  sessions?: Num
  matured_forecasts?: Num
  trades?: Num
  gates?: ValidationGate[]
  failed_gates?: string[]
  metrics?: Record<string, unknown>
  automatic_live_enable?: boolean
  report_path?: Str
}

export interface Replay {
  status?: Str
  replay_id?: Str
  samples?: Num
  mismatches?: Num
  method?: Str
}

export interface Security {
  execution_mode?: Str
  submit_orders?: boolean
  paper_mode?: boolean
  broker_environment?: Str
  market_data_environment?: Str
  production_unlocked?: boolean
  production_sentinel?: Str
  production_sentinel_present?: boolean
  production_approval?: Str
  production_approval_present?: boolean
  production_approval_valid?: boolean
  production_approval_reason?: Str
  production_credential_present?: boolean
  live_authorization?: boolean
  automatic_live_enable?: boolean
}

export interface ServiceRow {
  name?: string
  status?: string
  latency_ms?: Num
  last_event_age_ms?: Num
}

export interface PricePoint {
  t?: number
  price?: Num
  timestamp?: Str
}

export interface PredictionBand {
  t?: number
  mid?: Num
  low?: Num
  high?: Num
}

export interface StrategyMatrixRow {
  strategy?: Str
  regime?: Str
  status?: Str
  score?: Num
  expectancy?: Num
  probability_profit?: Num
  max_loss?: Num
  valuation_method?: Str
  q_executable_edge?: Num
}

export interface ChallengerRow {
  name?: Str
  status?: Str
  calibration?: Num
  expectancy?: Num
  tail_loss?: Num
  sessions?: Num
}

export interface AttributionRow {
  cause?: string
  count?: Num
  share?: Num
}

export interface ConstituentRow {
  symbol?: string
  contribution?: Num
  weight?: Num
  change_pct?: Num
  [k: string]: unknown
}

export interface AuditMetrics {
  sample_size?: Num
  direction_accuracy?: Num
  brier?: Num
  range_coverage?: Num
  price_mae?: Num
  vol_mae?: Num
  integrity_verified_pct?: Num
  current_prediction_status?: Str
  t_minus_15_match?: Str
  calibration?: Record<string, unknown>
  [k: string]: unknown
}

/** The flat state; the socket delivers it as named sections that merge into this. */
export interface WorkstationState {
  timestamp?: string
  engine?: Engine
  session?: Session
  market?: Market
  forecast_horizons?: Record<string, HorizonForecast>
  decision?: Decision
  candidates?: Candidate[]
  position?: Position
  broker_reconciliation?: Record<string, unknown>
  account?: Account
  health?: Health
  audit?: AuditMetrics
  prediction_metrics?: AuditMetrics
  predictions?: Prediction[]
  alerts?: Alert[]
  commands?: CommandRow[]
  promotion?: Promotion
  replay?: Replay
  security?: Security
  services?: ServiceRow[]
  tradier?: Record<string, unknown>
  strategy_matrix?: StrategyMatrixRow[]
  challengers?: ChallengerRow[]
  attribution?: AttributionRow[]
  constituent_attribution?: ConstituentRow[]
  price_series?: PricePoint[]
  prediction_series?: PredictionBand[]
}

export type SectionName =
  | 'engine'
  | 'session'
  | 'market'
  | 'forecast'
  | 'decision'
  | 'candidates'
  | 'position'
  | 'account'
  | 'health'
  | 'audit'
  | 'predictions'
  | 'alerts'
  | 'commands'
  | 'validation'
  | 'security'
  | 'services'
  | 'research'

export type Frame =
  | { type: 'snapshot'; seq: number; timestamp?: string; sections: Record<string, Partial<WorkstationState>> }
  | { type: 'patch'; seq: number; timestamp?: string; sections: Record<string, Partial<WorkstationState>>; removed?: string[] }
  | { type: 'heartbeat'; seq: number; timestamp?: string }

export type ConnectionStatus = 'connecting' | 'live' | 'reconnecting' | 'unauthorized'

export type CommandName =
  | 'PAUSE_NEW_ENTRIES'
  | 'RESUME_NEW_ENTRIES'
  | 'FLATTEN_MANAGED_POSITION'
  | 'RELOAD_MODEL'
