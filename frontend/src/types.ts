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
