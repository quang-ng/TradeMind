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
  })
})
