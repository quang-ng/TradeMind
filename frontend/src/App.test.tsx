import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  // Not auto-registered: vitest `globals` is off, so @testing-library/react
  // never hooks its own afterEach. Without this each test leaves its App
  // mounted and the next render() stacks a second copy in the DOM.
  cleanup()
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('Operator authentication', () => {
  it('keeps the admin key in session storage and opens the console', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      const data = path.endsWith('/status')
        ? { killswitch_enabled: false, dry_run: true, open_positions: 0, equity_usdt: '5000', free_balance_usdt: '4200', daily_pnl_pct: '0', pairs: {} }
        : path.endsWith('/config/llm')
          ? { llm_provider: 'anthropic', anthropic_model: 'claude-sonnet-5', ollama_model: 'llama3.2:3b', ollama_temperature: 0.4 }
          : path.endsWith('/config')
            ? { dry_run: true, max_open_positions: 2 }
            : []
      return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    render(<App />)

    await user.type(screen.getByLabelText('Admin API key'), 'operator-secret')
    await user.click(screen.getByRole('button', { name: /connect securely/i }))

    expect(sessionStorage.getItem('trademind_api_key')).toBe('operator-secret')
    expect(await screen.findByText('Portfolio equity')).toBeInTheDocument()
    expect(screen.getByText('$5,000.00')).toBeInTheDocument()
  })

  it('does not allow an empty API key', async () => {
    render(<App />)
    expect(screen.getByRole('button', { name: /connect securely/i })).toBeDisabled()
  })
})

describe('Performance view', () => {
  it('loads R-normalized metrics from GET /performance when opened', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      let data: unknown = []
      if (path.endsWith('/status')) {
        data = { killswitch_enabled: false, dry_run: true, open_positions: 0, equity_usdt: '5000', free_balance_usdt: '4200', daily_pnl_pct: '0', pairs: { 'BTC/USDT': { last_cycle_at: null, last_action: null } } }
      } else if (path.endsWith('/config/llm')) {
        data = { llm_provider: 'anthropic', anthropic_model: 'claude-sonnet-5', ollama_model: 'llama3.2:3b', ollama_temperature: 0.4 }
      } else if (path.endsWith('/config')) {
        data = { dry_run: true, max_open_positions: 2 }
      } else if (path.includes('/performance')) {
        data = {
          trades: 12, wins: 7, losses: 4, breakeven: 1, trades_with_r: 10,
          win_rate: '0.5833', avg_win_r: '1.42', avg_loss_r: '-0.91', expectancy_r: '0.34',
          total_r: '3.4', total_pnl_usdt: '18.75', profit_factor: '2.6',
          max_drawdown_pct: '0.012', avg_drawdown_pct: '0.004', total_fees_usdt: '1.20',
          total_slippage_usdt: null, starting_equity_usdt: '5000',
          breakdowns: {
            by_regime: [
              {
                key: 'trend_pullback', trades: 8, wins: 5, losses: 3, breakeven: 0, trades_with_r: 8,
                win_rate: '0.625', avg_win_r: '1.5', avg_loss_r: '-0.9', expectancy_r: '0.42',
                total_r: '3.36', total_pnl_usdt: '15.00', profit_factor: '2.8',
                max_drawdown_pct: '0.01', avg_drawdown_pct: '0.003', total_fees_usdt: '0.8',
                total_slippage_usdt: null, starting_equity_usdt: '5000',
              },
            ],
            by_volatility: [],
            by_score_bucket: [],
          },
          filters: { symbol: null, regime: null, score_min: null, score_max: null, since: null, until: null },
        }
      }
      return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })
    render(<App />)
    await user.type(screen.getByLabelText('Admin API key'), 'operator-secret')
    await user.click(screen.getByRole('button', { name: /connect securely/i }))
    await screen.findByText('Portfolio equity')

    await user.click(screen.getByRole('button', { name: /performance/i }))

    expect(await screen.findByText('Expectancy (R / trade)')).toBeInTheDocument()
    expect(screen.getByText('+0.34R')).toBeInTheDocument()
    expect(screen.getByText('7W · 4L · 1BE')).toBeInTheDocument()

    // M4 breakdown table renders its cohort rows
    expect(screen.getByText('Expectancy by setup regime')).toBeInTheDocument()
    expect(screen.getByText('+0.42R')).toBeInTheDocument()
    // 'trend_pullback' now appears both as a filter option and a cohort row
    expect(screen.getAllByText('trend_pullback').length).toBeGreaterThan(1)
  })
})

describe('Trades view', () => {
  const signal = {
    id: 's1', trace_id: 't1', symbol: 'BTC/USDT', timeframe: '1h', candle_ts: '2026-08-28T10:00:00Z',
    action: 'BUY', confidence: '0.72', reasoning: 'Clean trend pullback into the 50 EMA.',
    model_name: 'claude-sonnet-5', price: '50000', atr_14: '900', status: 'VALIDATED',
    created_at: '2026-08-28T10:00:05Z',
    trade_score: 74, score_breakdown: { trend_alignment: 30, momentum: 24 },
    setup_regime: 'trend_pullback', volatility_regime: 'NORMAL',
  }
  const decision = {
    id: 'd1', trace_id: 't1', signal_id: 's1', approved: true, rejection_reason: null,
    position_size_usdt: '500', position_size_base: '0.01', stop_loss_price: '49000',
    equity_snapshot_usdt: '5000', risk_pct_applied: '0.0075',
    nominal_risk_amount_usdt: '37.5', actual_risk_usdt: '13.30', stop_distance_pct: '0.02',
    created_at: '2026-08-28T10:00:06Z',
  }
  const entryOrder = {
    id: 'o1', trace_id: 't1', risk_decision_id: 'd1', freqtrade_trade_id: 42, symbol: 'BTC/USDT',
    side: 'BUY', status: 'FILLED', requested_amount: '0.01', filled_amount: '0.01', avg_price: '50000',
    dry_run: false, created_at: '2026-08-28T10:00:07Z', updated_at: '2026-08-28T10:00:08Z',
  }
  const exitOrder = {
    id: 'o2', trace_id: 't2', risk_decision_id: 'd1', freqtrade_trade_id: 42, symbol: 'BTC/USDT',
    side: 'SELL', status: 'FILLED', requested_amount: '0.01', filled_amount: '0.01', avg_price: '52000',
    dry_run: false, created_at: '2026-08-29T09:00:00Z', updated_at: '2026-08-29T09:00:01Z',
  }
  const position = {
    id: 'p1', symbol: 'BTC/USDT', status: 'CLOSED', entry_order_id: 'o1', exit_order_id: 'o2',
    entry_price: '50000', exit_price: '52000', amount: '0.01', pnl_usdt: '20', pnl_pct: '0.04',
    opened_at: '2026-08-28T10:00:07Z', closed_at: '2026-08-29T09:00:00Z',
    exit_reason: 'take_profit', fees_usdt: '0.5', fees_estimated: true, r_multiple: '1.50',
    market_regime: 'trend_pullback', trade_score: 74,
    current_price: null, current_value_usdt: null, unrealized_pnl_usdt: null,
    unrealized_pnl_pct: null, price_updated_at: null,
  }

  const mockDashboard = () =>
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      let data: unknown = []
      if (path.endsWith('/status')) {
        data = { killswitch_enabled: false, dry_run: false, open_positions: 0, equity_usdt: '5000', free_balance_usdt: '4200', daily_pnl_pct: '0', pairs: { 'BTC/USDT': { last_cycle_at: null, last_action: null } } }
      } else if (path.endsWith('/config/llm')) {
        data = { llm_provider: 'anthropic', anthropic_model: 'claude-sonnet-5', ollama_model: 'llama3.2:3b', ollama_temperature: 0.4 }
      } else if (path.endsWith('/config')) {
        data = { dry_run: false, max_open_positions: 2 }
      } else if (path.includes('/signals')) {
        data = [signal]
      } else if (path.includes('/decisions')) {
        data = [decision]
      } else if (path.includes('/orders')) {
        data = [entryOrder, exitOrder]
      } else if (path.endsWith('/positions')) {
        data = [position]
      }
      return new Response(JSON.stringify(data), { status: 200, headers: { 'Content-Type': 'application/json' } })
    })

  const openTrades = async () => {
    const user = userEvent.setup()
    mockDashboard()
    render(<App />)
    await user.type(screen.getByLabelText('Admin API key'), 'operator-secret')
    await user.click(screen.getByRole('button', { name: /connect securely/i }))
    await screen.findByText('Portfolio equity')
    await user.click(screen.getByRole('button', { name: /^trades$/i }))
    return user
  }

  it('consolidates each position into one row with its joined setup, score and outcome', async () => {
    await openTrades()

    expect(await screen.findByText('Trade ledger')).toBeInTheDocument()
    // one row per position — the exit reason, R multiple and setup regime all
    // resolve onto that single record
    expect(screen.getByText('Take Profit')).toBeInTheDocument()
    expect(screen.getByText('+1.50R')).toBeInTheDocument()
    expect(screen.getByText('Trend Pullback')).toBeInTheDocument()
    expect(screen.getByText('1 trade')).toBeInTheDocument()

    // Orders is no longer a top-level tab
    expect(screen.queryByRole('button', { name: /^orders$/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /positions & p&l/i })).not.toBeInTheDocument()
  })

  it('opens a lifecycle detail drawer when a trade row is clicked', async () => {
    const user = await openTrades()
    await screen.findByText('Trade ledger')

    await user.click(screen.getByText('Take Profit'))

    expect(await screen.findByText('TRADE DETAIL')).toBeInTheDocument()
    expect(screen.getByText('Actual risk (1R)')).toBeInTheDocument()
    expect(screen.getByText('$13.30')).toBeInTheDocument()
    expect(screen.getByText('Trend Alignment')).toBeInTheDocument()
    expect(screen.getByText('Clean trend pullback into the 50 EMA.')).toBeInTheDocument()
  })
})
