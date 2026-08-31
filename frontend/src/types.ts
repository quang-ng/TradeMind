export type Action = 'BUY' | 'SELL' | 'HOLD'

export interface PairStatus {
  last_cycle_at: string | null
  last_action: Action | null
}

export interface SystemStatus {
  killswitch_enabled: boolean
  dry_run: boolean
  open_positions: number
  equity_usdt: string
  free_balance_usdt: string
  daily_pnl_pct: string
  pairs: Record<string, PairStatus>
}

export interface Signal {
  id: string
  trace_id: string
  symbol: string
  timeframe: string
  candle_ts: string
  action: Action
  confidence: string
  reasoning: string
  model_name: string
  price: string
  atr_14: string
  status: string
  created_at: string
  raw_response?: Record<string, unknown> | null
  model_input?: Record<string, unknown> | null
  // Positive-expectancy plan D3/M2 — computed once per cycle, before the
  // LLM call, so present regardless of the eventual action.
  trade_score?: number | null
  score_breakdown?: Record<string, number> | null
  setup_regime?: string | null
  volatility_regime?: 'HIGH_VOLATILITY' | 'NORMAL' | 'LOW_VOLATILITY' | null
}

export interface Decision {
  id: string
  trace_id: string
  signal_id: string
  approved: boolean
  rejection_reason: string | null
  position_size_usdt: string | null
  position_size_base: string | null
  stop_loss_price: string | null
  equity_snapshot_usdt: string
  risk_pct_applied: string | null
  created_at: string
  // Positive-expectancy plan M1 (D1) — set only when approved.
  nominal_risk_amount_usdt?: string | null
  actual_risk_usdt?: string | null
  stop_distance_pct?: string | null
}

export interface Order {
  id: string
  trace_id: string
  risk_decision_id: string
  freqtrade_trade_id: number | null
  symbol: string
  side: 'BUY' | 'SELL'
  status: 'SUBMITTED' | 'FILLED' | 'FAILED' | 'CANCELLED'
  requested_amount: string
  filled_amount: string | null
  avg_price: string | null
  dry_run: boolean
  created_at: string
  updated_at: string
}

export interface Position {
  id: string
  symbol: string
  status: 'OPEN' | 'CLOSED'
  entry_order_id: string
  exit_order_id: string | null
  entry_price: string
  exit_price: string | null
  amount: string
  pnl_usdt: string | null
  pnl_pct: string | null
  opened_at: string
  closed_at: string | null
  current_price: string | null
  current_value_usdt: string | null
  unrealized_pnl_usdt: string | null
  unrealized_pnl_pct: string | null
  price_updated_at: string | null
  // Positive-expectancy plan M1 — set on close.
  exit_reason?: string | null
  fees_usdt?: string | null
  fees_estimated?: boolean
  r_multiple?: string | null
  // Positive-expectancy plan M2 — denormalized from the entry Signal at open.
  market_regime?: string | null
  trade_score?: number | null
}

export interface AuditEvent {
  id: string
  trace_id: string
  event_type: string
  payload: Record<string, unknown>
  created_at: string
}

export interface AuditTimeline {
  trace_id: string
  signals: Signal[]
  risk_decisions: Decision[]
  orders: Order[]
  audit_events: AuditEvent[]
}

export interface PerformanceFilters {
  symbol: string | null
  regime: string | null
  score_min: number | null
  score_max: number | null
  since: string | null
  until: string | null
}

// Positive-expectancy plan M3 — mirrors admin_api PerformanceSummary. Every
// R-based figure and both drawdown figures are nullable: a closed trade
// opened before M1 has no r_multiple and is excluded from the R metrics
// (D5), and the drawdown pair needs a live equity anchor that may be
// missing. total_slippage_usdt is null (not 0) — production has no
// per-trade slippage source yet.
export interface PerformanceMetrics {
  trades: number
  wins: number
  losses: number
  breakeven: number
  trades_with_r: number
  win_rate: string | null
  avg_win_r: string | null
  avg_loss_r: string | null
  expectancy_r: string | null
  total_r: string | null
  total_pnl_usdt: string
  profit_factor: string | null
  max_drawdown_pct: string | null
  avg_drawdown_pct: string | null
  total_fees_usdt: string
  total_slippage_usdt: string | null
  starting_equity_usdt: string | null
}

// Positive-expectancy plan M4 — the same metric set as the headline, over
// just the closed trades sharing one dimension value (`key`). Rows are
// ordered by descending trade count; the catch-all
// "(unclassified)"/"(unscored)" cohort is always last.
export interface PerformanceCohort extends PerformanceMetrics {
  key: string
}

export interface PerformanceBreakdowns {
  by_regime: PerformanceCohort[]
  by_volatility: PerformanceCohort[]
  by_score_bucket: PerformanceCohort[]
}

export interface PerformanceSummary extends PerformanceMetrics {
  breakdowns: PerformanceBreakdowns
  filters: PerformanceFilters
}

export interface RiskConfig {
  risk_per_trade_pct: string
  max_position_pct: string
  max_total_exposure_pct: string
  max_open_positions: number
  max_daily_loss_pct: string
  consecutive_loss_limit: number
  cooldown_minutes: number
  min_confidence: string
  signal_max_age_minutes: number
  atr_stop_multiplier: string
  min_stop_loss_pct: string
  max_stop_loss_pct: string
  dry_run: boolean
  // Positive-expectancy plan M5 (D4) — the Historical Expectancy Filter
  // ships disabled/shadow-mode; the operator flips `expectancy_filter_enabled`
  // via PATCH /config after reviewing the shadow data.
  expectancy_filter_enabled: boolean
  expectancy_min_sample_size: number
  expectancy_min_r: string
}

export interface LLMConfig {
  llm_provider: 'anthropic' | 'ollama'
  anthropic_model: string
  ollama_model: string
  ollama_temperature: number
}

export interface DashboardData {
  status: SystemStatus
  signals: Signal[]
  decisions: Decision[]
  orders: Order[]
  positions: Position[]
  config: RiskConfig
  llmConfig: LLMConfig
}
