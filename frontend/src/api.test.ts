import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadDashboard, setKillSwitch, triggerCycle } from './api'

const payloads: Record<string, unknown> = {
  '/api/status': {
    killswitch_enabled: false,
    dry_run: true,
    open_positions: 0,
    equity_usdt: '5000',
    daily_pnl_pct: '0',
    pairs: {},
  },
  '/api/signals?limit=100': [],
  '/api/decisions?limit=100': [],
  '/api/orders?limit=100': [],
  '/api/positions': [],
  '/api/config': { dry_run: true },
}

afterEach(() => vi.restoreAllMocks())

describe('Admin API client', () => {
  it('loads dashboard resources concurrently with bearer authentication', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input)
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer secret-key')
      return new Response(JSON.stringify(payloads[url]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    })

    const dashboard = await loadDashboard('secret-key')

    expect(fetchMock).toHaveBeenCalledTimes(6)
    expect(dashboard.status.equity_usdt).toBe('5000')
    expect(dashboard.orders).toEqual([])
  })

  it('sends a reason when enabling the global kill switch', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ killswitch_enabled: true }), { status: 200 }),
    )

    await setKillSwitch('secret-key', true, 'manual review')

    expect(fetchMock).toHaveBeenCalledWith('/api/killswitch/enable', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ reason: 'manual review' }),
    }))
  })

  it('normalizes pair names for manual cycles', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ trace_id: null, skipped: true }), { status: 200 }),
    )

    await triggerCycle('secret-key', 'BTC/USDT')

    expect(fetchMock.mock.calls[0][0]).toBe('/api/cycles/BTC-USDT/trigger')
  })

  it('surfaces typed API errors', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () =>
      new Response(JSON.stringify({ detail: 'invalid or missing API key' }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(loadDashboard('bad-key')).rejects.toEqual(
      expect.objectContaining({ status: 401, message: 'invalid or missing API key' }),
    )
  })
})
