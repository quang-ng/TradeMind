import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import App from './App'

afterEach(() => {
  sessionStorage.clear()
  vi.restoreAllMocks()
})

describe('Operator authentication', () => {
  it('keeps the admin key in session storage and opens the console', async () => {
    const user = userEvent.setup()
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const path = String(input)
      const data = path.endsWith('/status')
        ? { killswitch_enabled: false, dry_run: true, open_positions: 0, equity_usdt: '5000', daily_pnl_pct: '0', pairs: {} }
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
