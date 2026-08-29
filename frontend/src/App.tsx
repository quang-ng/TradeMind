import { FormEvent, ReactNode, useCallback, useEffect, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowDownRight,
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Bot,
  Check,
  ChevronRight,
  CircleDollarSign,
  ClipboardList,
  Clock3,
  Eye,
  EyeOff,
  Gauge,
  KeyRound,
  LayoutDashboard,
  LoaderCircle,
  LockKeyhole,
  LogOut,
  Menu,
  Octagon,
  Play,
  RefreshCw,
  Save,
  ShieldCheck,
  ShieldOff,
  Signal as SignalIcon,
  SlidersHorizontal,
  TrendingUp,
  WalletCards,
  X,
  XCircle,
} from 'lucide-react'
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import {
  ApiError,
  getAudit,
  getPerformance,
  getSignal,
  loadDashboard,
  setKillSwitch,
  triggerCycle,
  updateLLMConfig,
  updateRiskConfig,
} from './api'
import { compactNumber, dateTime, duration, money, percent, readable, rMultiple, shortId, timeAgo } from './format'
import type {
  Action,
  AuditTimeline,
  DashboardData,
  Decision,
  LLMConfig,
  Order,
  PerformanceCohort,
  PerformanceSummary,
  Position,
  RiskConfig,
  Signal,
} from './types'

type Page = 'overview' | 'signals' | 'trades' | 'performance' | 'risk' | 'llm'
type Detail = { kind: 'trace'; id: string } | { kind: 'signal'; id: string } | null

const nav: { id: Page; label: string; icon: typeof LayoutDashboard }[] = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'signals', label: 'Signals & decisions', icon: SignalIcon },
  { id: 'trades', label: 'Trades', icon: WalletCards },
  { id: 'performance', label: 'Performance', icon: BarChart3 },
  { id: 'risk', label: 'Risk controls', icon: SlidersHorizontal },
  { id: 'llm', label: 'LLM engine', icon: Bot },
]

export default function App() {
  const [apiKey, setApiKey] = useState(() => sessionStorage.getItem('trademind_api_key') ?? '')
  const [authenticated, setAuthenticated] = useState(Boolean(apiKey))
  const [data, setData] = useState<DashboardData | null>(null)
  const [page, setPage] = useState<Page>('overview')
  const [loading, setLoading] = useState(Boolean(apiKey))
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [killDialog, setKillDialog] = useState(false)
  const [detail, setDetail] = useState<Detail>(null)

  const refresh = useCallback(
    async (quiet = false) => {
      if (!apiKey) return
      if (quiet) setRefreshing(true)
      else setLoading(true)
      try {
        const next = await loadDashboard(apiKey)
        setData(next)
        setAuthenticated(true)
        setError(null)
        setLastUpdated(new Date())
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 401) {
          sessionStorage.removeItem('trademind_api_key')
          setAuthenticated(false)
          setData(null)
          setError('That API key was not accepted. Check ADMIN_API_KEY and try again.')
        } else {
          setError(caught instanceof Error ? caught.message : 'Unable to reach TradeMind')
        }
      } finally {
        setLoading(false)
        setRefreshing(false)
      }
    },
    [apiKey],
  )

  useEffect(() => {
    if (!authenticated || !apiKey) return
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(true), 30_000)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [apiKey, authenticated, refresh])

  const connect = (key: string) => {
    const normalized = key.trim()
    if (!normalized) return
    sessionStorage.setItem('trademind_api_key', normalized)
    setApiKey(normalized)
    setAuthenticated(true)
    setError(null)
  }

  const logout = () => {
    sessionStorage.removeItem('trademind_api_key')
    setAuthenticated(false)
    setData(null)
    setApiKey('')
  }

  if (!authenticated || !apiKey) return <Login onConnect={connect} error={error} />

  if (loading && !data) {
    return (
      <div className="loading-screen">
        <Brand />
        <LoaderCircle className="spin" size={28} />
        <p>Opening your trading console…</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="loading-screen">
        <XCircle size={34} />
        <h2>Console unavailable</h2>
        <p>{error}</p>
        <button className="button primary" onClick={() => void refresh()}>
          Try again
        </button>
        <button className="button ghost" onClick={logout}>Use another API key</button>
      </div>
    )
  }

  const pageTitle = nav.find((item) => item.id === page)?.label ?? 'Overview'

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-head">
          <Brand />
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="Close menu">
            <X size={20} />
          </button>
        </div>
        <nav className="nav-list" aria-label="Main navigation">
          {nav.map((item) => (
            <button
              key={item.id}
              className={`nav-item ${page === item.id ? 'active' : ''}`}
              onClick={() => {
                setPage(item.id)
                setSidebarOpen(false)
              }}
            >
              <item.icon size={19} strokeWidth={1.8} />
              <span>{item.label}</span>
              {item.id === 'trades' && data.positions.some((position) => position.status === 'OPEN') && (
                <small>{data.positions.filter((position) => position.status === 'OPEN').length}</small>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className={`mode-card ${data.status.killswitch_enabled ? 'stopped' : ''}`}>
            <div className="mode-icon">{data.status.killswitch_enabled ? <ShieldOff /> : <ShieldCheck />}</div>
            <div>
              <strong>{data.status.killswitch_enabled ? 'Trading paused' : 'Risk engine active'}</strong>
              <span>{data.status.dry_run ? 'Dry-run · simulated funds' : 'Live mode'}</span>
            </div>
          </div>
          <button className="nav-item logout" onClick={logout}>
            <LogOut size={18} /> End session
          </button>
        </div>
      </aside>

      {sidebarOpen && <button className="scrim" onClick={() => setSidebarOpen(false)} aria-label="Close menu" />}

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Open menu">
              <Menu size={21} />
            </button>
            <div>
              <h1>{pageTitle}</h1>
              <p>Operator console · {data.status.dry_run ? 'Dry-run environment' : 'Live environment'}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <span className="updated desktop-only">
              <span className="online-dot" /> Updated {lastUpdated ? timeAgo(lastUpdated.toISOString()) : 'now'}
            </span>
            <button className="icon-button" onClick={() => void refresh(true)} aria-label="Refresh data" disabled={refreshing}>
              <RefreshCw className={refreshing ? 'spin' : ''} size={18} />
            </button>
            <button
              className={`button ${data.status.killswitch_enabled ? 'resume' : 'danger'}`}
              onClick={() => setKillDialog(true)}
            >
              {data.status.killswitch_enabled ? <Play size={17} /> : <Octagon size={17} />}
              {data.status.killswitch_enabled ? 'Resume trading' : 'Stop trading'}
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <AlertTriangle size={18} />
            <span>{error}. Showing the last successfully loaded snapshot.</span>
            <button onClick={() => setError(null)} aria-label="Dismiss"><X size={16} /></button>
          </div>
        )}

        <div className="page-content">
          {page === 'overview' && <Overview data={data} onTrace={(id) => setDetail({ kind: 'trace', id })} onNavigate={setPage} />}
          {page === 'signals' && <SignalsPage data={data} onDetail={setDetail} />}
          {page === 'trades' && <TradesPage data={data} onTrace={(id) => setDetail({ kind: 'trace', id })} />}
          {page === 'performance' && (
            <PerformancePage apiKey={apiKey} symbols={Object.keys(data.status.pairs)} />
          )}
          {page === 'risk' && (
            <RiskPage
              apiKey={apiKey}
              data={data}
              onUpdated={(config) => setData({ ...data, config })}
              onRefresh={() => void refresh(true)}
            />
          )}
          {page === 'llm' && (
            <LLMConfigPage
              apiKey={apiKey}
              data={data}
              onUpdated={(llmConfig) => setData({ ...data, llmConfig })}
            />
          )}
        </div>
      </main>

      {killDialog && (
        <KillSwitchDialog
          apiKey={apiKey}
          currentlyEnabled={data.status.killswitch_enabled}
          onClose={() => setKillDialog(false)}
          onChanged={() => {
            setKillDialog(false)
            void refresh(true)
          }}
        />
      )}
      {detail && (
        <DetailDrawer apiKey={apiKey} detail={detail} onClose={() => setDetail(null)} />
      )}
    </div>
  )
}

function Brand() {
  return (
    <div className="brand">
      <div className="brand-mark"><TrendingUp size={23} /></div>
      <div><strong>TradeMind</strong><span>Operator console</span></div>
    </div>
  )
}

function Login({ onConnect, error }: { onConnect: (key: string) => void; error: string | null }) {
  const [key, setKey] = useState('')
  const [visible, setVisible] = useState(false)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    onConnect(key)
  }
  return (
    <main className="login-page">
      <section className="login-story">
        <Brand />
        <div className="story-copy">
          <span className="eyebrow"><span className="online-dot" /> PRIVATE OPERATOR ACCESS</span>
          <h1>Your trading system,<br /><em>in one clear view.</em></h1>
          <p>Monitor every signal, risk decision, order, position, and dollar—without opening an SSH session.</p>
          <div className="trust-row">
            <span><ShieldCheck size={17} /> Risk engine enforced</span>
            <span><Bot size={17} /> LLM has no execution access</span>
          </div>
        </div>
        <p className="login-foot">TradeMind is self-hosted and designed for a single trusted operator.</p>
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <div className="login-icon"><KeyRound size={25} /></div>
          <h2>Open operator console</h2>
          <p>Enter the <code>ADMIN_API_KEY</code> configured on this TradeMind deployment.</p>
          <label htmlFor="api-key">Admin API key</label>
          <div className="secret-field">
            <input
              id="api-key"
              type={visible ? 'text' : 'password'}
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Paste your API key"
              autoComplete="current-password"
              autoFocus
            />
            <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? 'Hide API key' : 'Show API key'}>
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {error && <div className="form-error"><AlertTriangle size={16} /> {error}</div>}
          <button className="button primary login-button" type="submit" disabled={!key.trim()}>
            Connect securely <ArrowRight size={17} />
          </button>
          <div className="session-note"><LockKeyhole size={15} /> The key stays in this browser tab and is cleared when the session ends.</div>
        </form>
      </section>
    </main>
  )
}

function Overview({ data, onTrace, onNavigate }: { data: DashboardData; onTrace: (id: string) => void; onNavigate: (page: Page) => void }) {
  const { status, positions, signals, decisions, orders } = data
  const closed = positions.filter((position) => position.status === 'CLOSED')
  const totalPnl = closed.reduce((sum, position) => sum + Number(position.pnl_usdt ?? 0), 0)
  const exposure = positions
    .filter((position) => position.status === 'OPEN')
    .reduce((sum, position) => sum + Number(position.entry_price) * Number(position.amount), 0)
  const approved = decisions.filter((decision) => decision.approved).length
  const signalApproval = decisions.length ? approved / decisions.length : 0
  const latestSignals = signals.slice(0, 5)
  const failedOrders = orders.filter((order) => order.status === 'FAILED').length
  const chartData = buildPnlChart(closed)

  return (
    <div className="page-stack">
      <section className={`system-banner ${status.killswitch_enabled ? 'paused' : ''}`}>
        <div>
          <span className="status-orb">{status.killswitch_enabled ? <ShieldOff /> : <ShieldCheck />}</span>
          <div>
            <strong>{status.killswitch_enabled ? 'New entries are paused' : 'All systems monitoring'}</strong>
            <p>{status.killswitch_enabled ? 'The global kill switch is blocking all new entries.' : 'Risk controls are active and every trade remains independently evaluated.'}</p>
          </div>
        </div>
        <span className={`mode-pill ${status.dry_run ? '' : 'live'}`}>{status.dry_run ? 'DRY RUN' : 'LIVE FUNDS'}</span>
      </section>

      <section className="metric-grid">
        <MetricCard label="Portfolio equity" value={money(status.equity_usdt)} note={`${money(status.free_balance_usdt)} free USDT · live Freqtrade balance`} icon={<CircleDollarSign />} />
        <MetricCard
          label="Today’s P&L"
          value={percent(status.daily_pnl_pct)}
          note={`${money(totalPnl)} all-time realized`}
          icon={Number(status.daily_pnl_pct) >= 0 ? <ArrowUpRight /> : <ArrowDownRight />}
          tone={Number(status.daily_pnl_pct) >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard label="Open exposure" value={money(exposure)} note={`${status.open_positions} of ${data.config.max_open_positions} positions`} icon={<WalletCards />} />
        <MetricCard label="Risk approvals" value={percent(signalApproval)} note={`${approved} approved · ${failedOrders} failed orders`} icon={<Gauge />} tone={failedOrders ? 'negative' : undefined} />
      </section>

      <section className="overview-grid">
        <Panel className="pnl-panel" title="Realized performance" subtitle="Cumulative closed-position P&L">
          {chartData.length > 0 ? (
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 10, right: 8, left: -18, bottom: 0 }}>
                  <defs>
                    <linearGradient id="pnlFill" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#30d39a" stopOpacity={0.34} />
                      <stop offset="95%" stopColor="#30d39a" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="#21302c" strokeDasharray="4 6" vertical={false} />
                  <XAxis dataKey="label" stroke="#778982" tickLine={false} axisLine={false} fontSize={11} />
                  <YAxis stroke="#778982" tickLine={false} axisLine={false} fontSize={11} tickFormatter={(value) => `$${value}`} />
                  <Tooltip contentStyle={{ background: '#111d1a', border: '1px solid #2a3b36', borderRadius: 10 }} formatter={(value) => [money(Number(value)), 'Cumulative P&L']} />
                  <Area type="monotone" dataKey="pnl" stroke="#30d39a" strokeWidth={2.2} fill="url(#pnlFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : <EmptyState icon={<TrendingUp />} title="Performance begins after a position closes" text="Closed-position profit and loss will chart here automatically." />}
        </Panel>

        <Panel title="Trading pairs" subtitle="Latest 1-hour analysis cycles">
          <div className="pair-list">
            {Object.entries(status.pairs).map(([symbol, pair]) => {
              const latest = signals.find((signal) => signal.symbol === symbol)
              return (
                <div className="pair-row" key={symbol}>
                  <Coin symbol={symbol} />
                  <div className="pair-main"><strong>{symbol}</strong><span>{timeAgo(pair.last_cycle_at)}</span></div>
                  <div className="pair-price"><strong>{latest ? money(latest.price, 2) : '—'}</strong><ActionBadge action={pair.last_action} /></div>
                </div>
              )
            })}
          </div>
        </Panel>
      </section>

      <Panel
        title="Latest signal flow"
        subtitle="From LLM opinion to deterministic risk decision"
        action={<button className="text-button" onClick={() => onNavigate('signals')}>View all <ChevronRight size={15} /></button>}
      >
        <div className="table-scroll">
          <table>
            <thead><tr><th>Market</th><th>LLM signal</th><th>Confidence</th><th>Risk outcome</th><th>Price</th><th>Received</th><th /></tr></thead>
            <tbody>
              {latestSignals.map((signal) => {
                const decision = decisions.find((item) => item.signal_id === signal.id)
                return (
                  <tr key={signal.id}>
                    <td><div className="market-cell"><Coin symbol={signal.symbol} small /><strong>{signal.symbol}</strong></div></td>
                    <td><ActionBadge action={signal.action} /></td>
                    <td><Confidence value={Number(signal.confidence)} /></td>
                    <td><DecisionBadge decision={decision} /></td>
                    <td className="mono">{money(signal.price)}</td>
                    <td>{dateTime(signal.created_at)}</td>
                    <td><button className="row-button" onClick={() => onTrace(signal.trace_id)} aria-label="Open trace"><ChevronRight size={17} /></button></td>
                  </tr>
                )
              })}
              {latestSignals.length === 0 && <tr><td colSpan={7}><EmptyTable text="No signals have been generated yet." /></td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function SignalsPage({ data, onDetail }: { data: DashboardData; onDetail: (detail: Detail) => void }) {
  const [symbol, setSymbol] = useState('ALL')
  const [action, setAction] = useState('ALL')
  // Derived from the loaded signals rather than a fixed list, so the filter
  // always matches whichever symbols SYMBOLS actually has configured.
  const symbolOptions = ['ALL', ...Array.from(new Set(data.signals.map((signal) => signal.symbol))).sort()]
  const signals = data.signals.filter((signal) => (symbol === 'ALL' || signal.symbol === symbol) && (action === 'ALL' || signal.action === action))
  return (
    <div className="page-stack">
      <section className="section-intro"><div><h2>AI signals and risk decisions</h2><p>Every model opinion is recorded. Only deterministic risk approval can produce an order.</p></div><div className="legend"><span><i className="legend-dot llm" /> LLM opinion</span><ArrowRight size={14} /><span><i className="legend-dot risk" /> Risk authority</span></div></section>
      <div className="filters">
        <Filter label="Market" value={symbol} onChange={setSymbol} options={symbolOptions} />
        <Filter label="Action" value={action} onChange={setAction} options={['ALL', 'BUY', 'SELL', 'HOLD']} />
        <span className="result-count">{signals.length} signals</span>
      </div>
      <div className="signal-cards">
        {signals.map((signal) => {
          const decision = data.decisions.find((item) => item.signal_id === signal.id)
          const order = decision ? data.orders.find((item) => item.risk_decision_id === decision.id) : undefined
          return <SignalCard key={signal.id} signal={signal} decision={decision} order={order} onDetail={onDetail} />
        })}
        {signals.length === 0 && <Panel><EmptyState icon={<SignalIcon />} title="No matching signals" text="Adjust the filters or wait for the next closed candle." /></Panel>}
      </div>
    </div>
  )
}

function SignalCard({ signal, decision, order, onDetail }: { signal: Signal; decision?: Decision; order?: Order; onDetail: (detail: Detail) => void }) {
  return (
    <article className="signal-card">
      <div className="signal-card-head">
        <div className="signal-market"><Coin symbol={signal.symbol} /><div><strong>{signal.symbol}</strong><span>{dateTime(signal.created_at)} · {signal.timeframe}</span></div></div>
        <ActionBadge action={signal.action} large />
      </div>
      <div className="signal-body">
        <div className="opinion-block">
          <span className="block-label"><Bot size={15} /> LLM OPINION</span>
          <div className="confidence-line"><strong>{Math.round(Number(signal.confidence) * 100)}% confidence</strong><Confidence value={Number(signal.confidence)} /></div>
          <p>“{signal.reasoning}”</p>
          <div className="signal-meta"><span>Model <strong>{signal.model_name}</strong></span><span>Price <strong>{money(signal.price)}</strong></span><span>ATR(14) <strong>{money(signal.atr_14)}</strong></span></div>
        </div>
        <div className="flow-arrow"><ArrowRight /></div>
        <div className={`decision-block ${decision?.approved ? 'approved' : 'rejected'}`}>
          <span className="block-label"><ShieldCheck size={15} /> RISK DECISION</span>
          <DecisionBadge decision={decision} large />
          {decision ? (
            decision.approved ? <div className="decision-facts"><span>Size <strong>{money(decision.position_size_usdt)}</strong></span><span>Stop <strong>{money(decision.stop_loss_price)}</strong></span><span>Order <strong>{order ? readable(order.status) : 'Pending'}</strong></span></div>
              : <p className="reject-reason">{readable(decision.rejection_reason)}</p>
          ) : <p className="muted">Waiting for risk engine evaluation</p>}
        </div>
      </div>
      <footer className="signal-footer"><button className="text-button" onClick={() => onDetail({ kind: 'signal', id: signal.id })}>Raw LLM detail</button><button className="text-button" onClick={() => onDetail({ kind: 'trace', id: signal.trace_id })}>Full audit trail <ChevronRight size={15} /></button></footer>
    </article>
  )
}

interface Trade {
  position: Position
  entryOrder?: Order
  exitOrder?: Order
  decision?: Decision
  signal?: Signal
  orders: Order[]
  setupRegime: string | null
  volatilityRegime: string | null
  tradeScore: number | null
  scoreBreakdown: Record<string, number> | null
  traceId?: string
}

// One row per position — the complete trading lifecycle. Each Position is
// joined back through its entry Order -> RiskDecision -> Signal so setup
// score, risk sizing and execution detail all hang off a single record.
// M2 fields denormalized onto the Position win over the Signal's copy; a
// position opened before that migration (or whose signal has scrolled out
// of the loaded window) simply has nulls, which every cell renders as "—".
function buildTrades(data: DashboardData): Trade[] {
  const orderById = new Map(data.orders.map((order) => [order.id, order]))
  const decisionById = new Map(data.decisions.map((decision) => [decision.id, decision]))
  const signalById = new Map(data.signals.map((signal) => [signal.id, signal]))
  return data.positions.map((position) => {
    const entryOrder = orderById.get(position.entry_order_id)
    const exitOrder = position.exit_order_id ? orderById.get(position.exit_order_id) : undefined
    const decision = entryOrder ? decisionById.get(entryOrder.risk_decision_id) : undefined
    const signal = decision ? signalById.get(decision.signal_id) : undefined
    const orders = [entryOrder, exitOrder].filter((order): order is Order => Boolean(order))
    return {
      position,
      entryOrder,
      exitOrder,
      decision,
      signal,
      orders,
      setupRegime: position.market_regime ?? signal?.setup_regime ?? null,
      volatilityRegime: signal?.volatility_regime ?? null,
      tradeScore: position.trade_score ?? signal?.trade_score ?? null,
      scoreBreakdown: signal?.score_breakdown ?? null,
      traceId: entryOrder?.trace_id ?? signal?.trace_id,
    }
  })
}

function TradesPage({ data, onTrace }: { data: DashboardData; onTrace: (id: string) => void }) {
  const [symbol, setSymbol] = useState('ALL')
  const [statusFilter, setStatusFilter] = useState('ALL')
  const [regime, setRegime] = useState('ALL')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const trades = buildTrades(data)
  const symbolOptions = ['ALL', ...Array.from(new Set(trades.map((trade) => trade.position.symbol))).sort()]
  const regimeOptions = [
    'ALL',
    ...Array.from(new Set(trades.map((trade) => trade.setupRegime).filter((value): value is string => Boolean(value)))).sort(),
  ]
  const filtered = trades.filter(
    (trade) =>
      (symbol === 'ALL' || trade.position.symbol === symbol) &&
      (statusFilter === 'ALL' || trade.position.status === statusFilter) &&
      (regime === 'ALL' || trade.setupRegime === regime),
  )

  const open = trades.filter((trade) => trade.position.status === 'OPEN')
  const closed = trades.filter((trade) => trade.position.status === 'CLOSED')
  const realized = closed.reduce((sum, trade) => sum + Number(trade.position.pnl_usdt ?? 0), 0)
  const winners = closed.filter((trade) => Number(trade.position.pnl_usdt ?? 0) > 0).length
  const activeTrade = selectedId ? trades.find((trade) => trade.position.id === selectedId) ?? null : null

  return (
    <div className="page-stack">
      <section className="section-intro">
        <div>
          <h2>Trades</h2>
          <p>One row per position — the complete lifecycle from setup score through risk sizing, execution and outcome. Aggregate statistics live on the Performance page.</p>
        </div>
      </section>

      <section className="mini-metrics">
        <MetricCard label="Open positions" value={String(open.length)} note="Long-only spot positions" icon={<Activity />} />
        <MetricCard
          label="Realized P&L"
          value={money(realized)}
          note={`${closed.length} closed trade${closed.length === 1 ? '' : 's'}`}
          icon={<CircleDollarSign />}
          tone={realized >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard
          label="Win rate"
          value={closed.length ? percent(winners / closed.length) : '—'}
          note={`${winners} profitable close${winners === 1 ? '' : 's'}`}
          icon={<TrendingUp />}
        />
      </section>

      <div className="filters">
        <Filter label="Market" value={symbol} onChange={setSymbol} options={symbolOptions} />
        <Filter label="Status" value={statusFilter} onChange={setStatusFilter} options={['ALL', 'OPEN', 'CLOSED']} />
        <Filter label="Setup regime" value={regime} onChange={setRegime} options={regimeOptions} />
        <span className="result-count">{filtered.length} trade{filtered.length === 1 ? '' : 's'}</span>
      </div>

      <Panel title="Trade ledger" subtitle="Newest first · select a row for the full setup, risk and execution breakdown">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Market / Side</th>
                <th>Setup regime</th>
                <th>Score</th>
                <th>Entry → Exit</th>
                <th>P&L</th>
                <th>R multiple</th>
                <th>Exit reason</th>
                <th>Opened</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade) => (
                <TradeRow key={trade.position.id} trade={trade} onOpen={() => setSelectedId(trade.position.id)} />
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={9}><EmptyTable text="No trades match these filters." /></td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>

      {activeTrade && (
        <TradeDrawer
          trade={activeTrade}
          onClose={() => setSelectedId(null)}
          onTrace={(id) => {
            setSelectedId(null)
            onTrace(id)
          }}
        />
      )}
    </div>
  )
}

function TradeRow({ trade, onOpen }: { trade: Trade; onOpen: () => void }) {
  const { position } = trade
  const isOpen = position.status === 'OPEN'
  const pnl = isOpen ? position.unrealized_pnl_usdt : position.pnl_usdt
  const pnlPct = isOpen ? position.unrealized_pnl_pct : position.pnl_pct
  const pnlTone = pnl == null ? '' : Number(pnl) >= 0 ? 'positive-text' : 'negative-text'
  return (
    <tr className="clickable-row" onClick={onOpen}>
      <td>
        <div className="market-cell">
          <Coin symbol={position.symbol} small />
          <div>
            <strong>{position.symbol}</strong>
            <small className="sub-cell">Long</small>
          </div>
        </div>
      </td>
      <td>{trade.setupRegime ? readable(trade.setupRegime) : '—'}</td>
      <td className="mono">{trade.tradeScore ?? '—'}</td>
      <td className="mono">{money(position.entry_price)} → {isOpen ? '—' : money(position.exit_price)}</td>
      <td className={pnlTone}>
        {pnl == null ? '—' : money(pnl)}
        {pnlPct != null && <small className="sub-cell">{percent(pnlPct)}{isOpen ? ' unreal.' : ''}</small>}
      </td>
      <td className={`mono ${perfToneClass(position.r_multiple ?? null)}`}>{rMultiple(position.r_multiple ?? null)}</td>
      <td>{isOpen ? <span className="badge hold">Open</span> : position.exit_reason ? readable(position.exit_reason) : '—'}</td>
      <td>{dateTime(position.opened_at)}</td>
      <td>
        <button
          className="row-button"
          onClick={(event) => {
            event.stopPropagation()
            onOpen()
          }}
          aria-label="Open trade detail"
        >
          <ChevronRight size={17} />
        </button>
      </td>
    </tr>
  )
}

function TradeDrawer({ trade, onClose, onTrace }: { trade: Trade; onClose: () => void; onTrace: (id: string) => void }) {
  const { position, decision, signal, orders } = trade
  const isOpen = position.status === 'OPEN'
  const pnl = isOpen ? position.unrealized_pnl_usdt : position.pnl_usdt
  const pnlPct = isOpen ? position.unrealized_pnl_pct : position.pnl_pct
  const pnlNum = pnl == null ? null : Number(pnl)
  const entryValue = Number(position.entry_price) * Number(position.amount)
  const hasSetup = Boolean(signal) || trade.setupRegime !== null || trade.tradeScore !== null

  return (
    <div className="drawer-layer">
      <button className="drawer-scrim" onClick={onClose} aria-label="Close trade detail" />
      <aside className="drawer">
        <header>
          <div>
            <span className="eyebrow">TRADE DETAIL</span>
            <h2>{position.symbol} · Long</h2>
          </div>
          <button className="icon-button" onClick={onClose}><X size={20} /></button>
        </header>
        <div className="drawer-body">
          <div className="detail-hero">
            <Coin symbol={position.symbol} />
            <div>
              <strong>{position.symbol} · {isOpen ? 'Open position' : 'Closed'}</strong>
              <span>
                Opened {dateTime(position.opened_at)}
                {position.closed_at ? ` · closed ${dateTime(position.closed_at)}` : ''}
              </span>
            </div>
            <span className={`badge ${isOpen ? 'hold' : pnlNum !== null && pnlNum < 0 ? 'rejected' : 'approved'} large`}>
              {isOpen ? 'Open' : pnlNum === null ? 'Closed' : `${pnlNum >= 0 ? '+' : ''}${money(pnlNum)}`}
            </span>
          </div>

          <h3>Setup &amp; score</h3>
          {hasSetup ? (
            <>
              <dl className="detail-list">
                <div><dt>Setup regime</dt><dd>{trade.setupRegime ? readable(trade.setupRegime) : '—'}</dd></div>
                <div><dt>Volatility regime</dt><dd>{trade.volatilityRegime ? readable(trade.volatilityRegime) : '—'}</dd></div>
                <div><dt>Trade score</dt><dd>{trade.tradeScore ?? '—'}{trade.tradeScore != null ? ' / 100' : ''}</dd></div>
                <div><dt>LLM confidence</dt><dd>{signal ? `${Math.round(Number(signal.confidence) * 100)}%` : '—'}</dd></div>
                <div><dt>LLM action</dt><dd>{signal ? signal.action : '—'}</dd></div>
                <div><dt>Model</dt><dd>{signal?.model_name ?? '—'}</dd></div>
              </dl>
              {trade.scoreBreakdown && Object.keys(trade.scoreBreakdown).length > 0 && (
                <dl className="detail-list">
                  {Object.entries(trade.scoreBreakdown).map(([key, value]) => (
                    <div key={key}><dt>{readable(key)}</dt><dd>{value}</dd></div>
                  ))}
                </dl>
              )}
              {signal?.reasoning && <div className="reason-box">{signal.reasoning}</div>}
            </>
          ) : (
            <p className="muted">No setup data for this trade — it opened before setup scoring, or its signal is outside the loaded history.</p>
          )}

          <h3>Risk</h3>
          {decision ? (
            <dl className="detail-list">
              <div><dt>Position size</dt><dd>{money(decision.position_size_usdt)}</dd></div>
              <div><dt>Stop loss</dt><dd>{money(decision.stop_loss_price)}</dd></div>
              <div><dt>Stop distance</dt><dd>{decision.stop_distance_pct == null ? '—' : percent(decision.stop_distance_pct)}</dd></div>
              <div><dt>Nominal risk budget</dt><dd>{money(decision.nominal_risk_amount_usdt ?? null)}</dd></div>
              <div><dt>Actual risk (1R)</dt><dd>{money(decision.actual_risk_usdt ?? null)}</dd></div>
              <div><dt>Risk % applied</dt><dd>{decision.risk_pct_applied == null ? '—' : percent(decision.risk_pct_applied)}</dd></div>
              <div><dt>Equity snapshot</dt><dd>{money(decision.equity_snapshot_usdt)}</dd></div>
            </dl>
          ) : (
            <p className="muted">The risk decision for this trade is outside the loaded history.</p>
          )}

          <h3>Execution</h3>
          <dl className="detail-list">
            <div><dt>Entry price</dt><dd>{money(position.entry_price)}</dd></div>
            <div><dt>Exit price</dt><dd>{isOpen ? '—' : money(position.exit_price)}</dd></div>
            <div><dt>Amount</dt><dd>{compactNumber(position.amount)}</dd></div>
            <div><dt>Entry value</dt><dd>{money(entryValue)}</dd></div>
            {isOpen && <div><dt>Current price</dt><dd>{money(position.current_price)}</dd></div>}
            {isOpen && position.price_updated_at && <div><dt>Market mark</dt><dd>{dateTime(position.price_updated_at)}</dd></div>}
          </dl>

          <h3>Orders</h3>
          {orders.length > 0 ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr><th>Order</th><th>Side</th><th>Status</th><th>Filled</th><th>Avg price</th><th>Time</th></tr>
                </thead>
                <tbody>
                  {orders.map((order) => (
                    <tr key={order.id}>
                      <td className="mono">
                        {shortId(order.id)}
                        {order.freqtrade_trade_id && <small className="sub-cell">FT #{order.freqtrade_trade_id}</small>}
                      </td>
                      <td><ActionBadge action={order.side} /></td>
                      <td><OrderBadge status={order.status} /></td>
                      <td className="mono">{compactNumber(order.filled_amount)}</td>
                      <td className="mono">{money(order.avg_price)}</td>
                      <td>{dateTime(order.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">The order records for this trade are outside the loaded history.</p>
          )}

          <h3>Outcome</h3>
          <dl className="detail-list">
            <div><dt>Status</dt><dd>{readable(position.status)}</dd></div>
            <div><dt>{isOpen ? 'Unrealized P&L' : 'Realized P&L'}</dt><dd>{pnl == null ? '—' : money(pnl)}</dd></div>
            <div><dt>Return</dt><dd>{pnlPct == null ? '—' : percent(pnlPct)}</dd></div>
            <div><dt>R multiple</dt><dd>{rMultiple(position.r_multiple ?? null)}</dd></div>
            <div><dt>Exit reason</dt><dd>{isOpen ? 'Position open' : position.exit_reason ? readable(position.exit_reason) : '—'}</dd></div>
            <div><dt>Fees{position.fees_estimated ? ' (est.)' : ''}</dt><dd>{money(position.fees_usdt ?? null)}</dd></div>
            <div><dt>Opened</dt><dd>{dateTime(position.opened_at)}</dd></div>
            <div><dt>Closed</dt><dd>{position.closed_at ? dateTime(position.closed_at) : '—'}</dd></div>
            <div><dt>Duration</dt><dd>{position.closed_at ? duration(new Date(position.closed_at).getTime() - new Date(position.opened_at).getTime()) : 'Still open'}</dd></div>
          </dl>

          {trade.traceId && (
            <button className="text-button" onClick={() => onTrace(trade.traceId!)}>
              Full audit trail <ChevronRight size={15} />
            </button>
          )}
        </div>
      </aside>
    </div>
  )
}

const PERF_REGIMES = ['ALL', 'trend_following', 'trend_pullback', 'momentum_continuation', 'mean_reversion']
const PERF_SCORE_BUCKETS: Record<string, { score_min?: number; score_max?: number }> = {
  ALL: {},
  '0–39': { score_min: 0, score_max: 39 },
  '40–69': { score_min: 40, score_max: 69 },
  '70–100': { score_min: 70, score_max: 100 },
}

function PerformancePage({ apiKey, symbols }: { apiKey: string; symbols: string[] }) {
  const [symbol, setSymbol] = useState('ALL')
  const [regime, setRegime] = useState('ALL')
  const [scoreBucket, setScoreBucket] = useState('ALL')
  const [summary, setSummary] = useState<PerformanceSummary | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    getPerformance(apiKey, {
      symbol: symbol === 'ALL' ? undefined : symbol,
      regime: regime === 'ALL' ? undefined : regime,
      ...PERF_SCORE_BUCKETS[scoreBucket],
    })
      .then((value) => {
        if (!active) return
        setSummary(value)
        setError(null)
      })
      .catch((caught) => {
        if (!active) return
        setError(caught instanceof Error ? caught.message : 'Could not load performance')
      })
    return () => {
      active = false
    }
  }, [apiKey, symbol, regime, scoreBucket])

  const tone = (value: string | null | undefined): 'positive' | 'negative' | undefined =>
    value == null ? undefined : Number(value) >= 0 ? 'positive' : 'negative'
  const drawdown = (value: string | null) =>
    value === null ? '—' : `-${(Number(value) * 100).toFixed(2)}%`
  const legacyExcluded = summary ? summary.trades - summary.trades_with_r : 0

  return (
    <div className="page-stack">
      <section className="section-intro">
        <div>
          <h2>Trading performance</h2>
          <p>R-normalized expectancy over the closed-position journal. 1R = the risk actually taken on each trade (RiskDecision.actual_risk_usdt).</p>
        </div>
      </section>

      <div className="filters">
        <Filter label="Market" value={symbol} onChange={setSymbol} options={['ALL', ...symbols]} />
        <Filter label="Setup regime" value={regime} onChange={setRegime} options={PERF_REGIMES} />
        <Filter label="Trade score" value={scoreBucket} onChange={setScoreBucket} options={Object.keys(PERF_SCORE_BUCKETS)} />
      </div>

      {error && <div className="form-error"><AlertTriangle size={16} /> {error}</div>}
      {!summary && !error && <Panel><div className="drawer-loading"><LoaderCircle className="spin" /> Computing metrics…</div></Panel>}

      {summary && summary.trades === 0 && (
        <Panel><EmptyState icon={<BarChart3 />} title="No closed trades match these filters" text="Expectancy metrics appear once positions close under the selected filters." /></Panel>
      )}

      {summary && summary.trades > 0 && (
        <>
          <section className="metric-grid">
            <MetricCard
              label="Win rate"
              value={summary.win_rate === null ? '—' : percent(summary.win_rate)}
              note={`${summary.wins}W · ${summary.losses}L · ${summary.breakeven}BE`}
              icon={<Gauge />}
            />
            <MetricCard
              label="Expectancy (R / trade)"
              value={rMultiple(summary.expectancy_r)}
              note={`over ${summary.trades_with_r} R-tracked trade${summary.trades_with_r === 1 ? '' : 's'}`}
              icon={<TrendingUp />}
              tone={tone(summary.expectancy_r)}
            />
            <MetricCard
              label="Total R"
              value={rMultiple(summary.total_r)}
              note={`${money(summary.total_pnl_usdt)} realized P&L`}
              icon={<CircleDollarSign />}
              tone={tone(summary.total_r)}
            />
            <MetricCard
              label="Profit factor"
              value={summary.profit_factor === null ? '—' : compactNumber(summary.profit_factor, 2)}
              note={summary.profit_factor === null ? 'no losing trade yet' : 'gross profit ÷ gross loss'}
              icon={<Activity />}
            />
          </section>

          <Panel title="Breakdown" subtitle="Every figure over the filtered closed-trade cohort">
            <dl className="detail-list">
              <div><dt>Closed trades</dt><dd>{summary.trades}</dd></div>
              <div><dt>Wins / Losses / Breakeven</dt><dd>{summary.wins} / {summary.losses} / {summary.breakeven}</dd></div>
              <div><dt>Avg win</dt><dd>{rMultiple(summary.avg_win_r)}</dd></div>
              <div><dt>Avg loss</dt><dd>{rMultiple(summary.avg_loss_r)}</dd></div>
              <div><dt>Total realized P&L</dt><dd>{money(summary.total_pnl_usdt)}</dd></div>
              <div><dt>Total fees (estimated)</dt><dd>{money(summary.total_fees_usdt)}</dd></div>
              <div><dt>Max drawdown</dt><dd>{drawdown(summary.max_drawdown_pct)}</dd></div>
              <div><dt>Avg drawdown</dt><dd>{drawdown(summary.avg_drawdown_pct)}</dd></div>
              <div><dt>R-tracked trades</dt><dd>{summary.trades_with_r} of {summary.trades}</dd></div>
              <div><dt>Slippage</dt><dd>not measured</dd></div>
            </dl>
            {legacyExcluded > 0 && (
              <p className="muted">{legacyExcluded} trade{legacyExcluded === 1 ? '' : 's'} opened before M1 have no R and are excluded from every R metric.</p>
            )}
            {summary.max_drawdown_pct === null && (
              <p className="muted">Drawdown needs a live account-equity anchor, which was unavailable at compute time.</p>
            )}
          </Panel>

          <BreakdownTable
            title="Expectancy by setup regime"
            subtitle="Strategy Selector classification, within the current filter"
            rows={summary.breakdowns?.by_regime ?? []}
          />
          <BreakdownTable
            title="Expectancy by volatility regime"
            subtitle="ATR-relative-to-price bucket at entry, within the current filter"
            rows={summary.breakdowns?.by_volatility ?? []}
          />
          <BreakdownTable
            title="Expectancy by trade-score bucket"
            subtitle="0–100 setup-quality rubric at entry, within the current filter"
            rows={summary.breakdowns?.by_score_bucket ?? []}
          />
        </>
      )}
    </div>
  )
}

function perfToneClass(value: string | null): string {
  if (value == null) return ''
  return Number(value) >= 0 ? 'positive-text' : 'negative-text'
}

function BreakdownTable({ title, subtitle, rows }: { title: string; subtitle: string; rows: PerformanceCohort[] }) {
  return (
    <Panel title={title} subtitle={subtitle}>
      {rows.length === 0 ? (
        <EmptyTable text="No closed trades carry this dimension yet." />
      ) : (
        <div className="table-scroll">
          <table>
            <thead><tr><th>Cohort</th><th>Trades</th><th>Win rate</th><th>Expectancy R</th><th>Total R</th><th>P&L</th><th>Profit factor</th></tr></thead>
            <tbody>
              {rows.map((cohort) => (
                <tr key={cohort.key}>
                  <td>{cohort.key}</td>
                  <td className="mono">{cohort.trades}</td>
                  <td className="mono">{cohort.win_rate === null ? '—' : percent(cohort.win_rate)}</td>
                  <td className={`mono ${perfToneClass(cohort.expectancy_r)}`}>{rMultiple(cohort.expectancy_r)}</td>
                  <td className={`mono ${perfToneClass(cohort.total_r)}`}>{rMultiple(cohort.total_r)}</td>
                  <td className={`mono ${perfToneClass(cohort.total_pnl_usdt)}`}>{money(cohort.total_pnl_usdt)}</td>
                  <td className="mono">{cohort.profit_factor === null ? '—' : compactNumber(cohort.profit_factor, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}

function RiskPage({ apiKey, data, onUpdated, onRefresh }: { apiKey: string; data: DashboardData; onUpdated: (config: RiskConfig) => void; onRefresh: () => void }) {
  const [draft, setDraft] = useState(data.config)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const [cycleLoading, setCycleLoading] = useState<string | null>(null)
  const fields: { key: keyof RiskConfig; label: string; help: string; suffix?: string }[] = [
    { key: 'risk_per_trade_pct', label: 'Risk per trade', help: 'Maximum equity risked on one entry', suffix: '%' },
    { key: 'max_position_pct', label: 'Maximum position', help: 'Cap for any single position', suffix: '%' },
    { key: 'max_total_exposure_pct', label: 'Total exposure cap', help: 'Maximum combined open exposure', suffix: '%' },
    { key: 'max_daily_loss_pct', label: 'Daily loss circuit breaker', help: 'Auto-enables the kill switch', suffix: '%' },
    { key: 'min_confidence', label: 'Minimum LLM confidence', help: 'Lower signals are rejected', suffix: '%' },
    { key: 'atr_stop_multiplier', label: 'ATR stop multiplier', help: 'Volatility-based stop distance' },
    { key: 'min_stop_loss_pct', label: 'Minimum stop loss', help: 'Lower bound for every stop', suffix: '%' },
    { key: 'max_stop_loss_pct', label: 'Maximum stop loss', help: 'Upper bound for every stop', suffix: '%' },
    { key: 'max_open_positions', label: 'Maximum open positions', help: 'Across both supported pairs' },
    { key: 'consecutive_loss_limit', label: 'Consecutive loss limit', help: 'Pauses entries after this many losses' },
    { key: 'cooldown_minutes', label: 'Pair cooldown', help: 'Minutes after a position closes' },
    { key: 'signal_max_age_minutes', label: 'Maximum signal age', help: 'Minutes before a signal is stale' },
  ]
  const percentageKeys = new Set<keyof RiskConfig>(['risk_per_trade_pct', 'max_position_pct', 'max_total_exposure_pct', 'max_daily_loss_pct', 'min_confidence', 'min_stop_loss_pct', 'max_stop_loss_pct'])
  const save = async () => {
    setSaving(true); setMessage(null)
    try {
      const patch: Partial<RiskConfig> = {}
      fields.forEach(({ key }) => {
        if (draft[key] !== data.config[key]) Object.assign(patch, { [key]: draft[key] })
      })
      if (!Object.keys(patch).length) { setMessage('No settings changed.'); return }
      const updated = await updateRiskConfig(apiKey, patch)
      onUpdated(updated); setMessage('Risk settings saved and added to the audit trail.')
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Could not save risk settings') }
    finally { setSaving(false) }
  }
  const runCycle = async (symbol: string) => {
    setCycleLoading(symbol); setMessage(null)
    try {
      const result = await triggerCycle(apiKey, symbol)
      setMessage(result.skipped ? `${symbol} cycle was already running or processed.` : `${symbol} cycle started. Trace ${result.trace_id?.slice(0, 8)}…`)
      window.setTimeout(onRefresh, 1500)
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Could not trigger cycle') }
    finally { setCycleLoading(null) }
  }
  return (
    <div className="risk-layout">
      <div className="page-stack">
        <section className="section-intro"><div><h2>Deterministic risk limits</h2><p>These values govern the Risk Engine. Changes are persisted and audited immediately.</p></div><span className="safety-chip"><ShieldCheck size={16} /> Kill switch remains the first gate</span></section>
        <Panel title="Entry and portfolio limits" subtitle="Percentage fields are shown as human-readable percentages">
          <div className="settings-grid">
            {fields.map((field) => {
              const raw = draft[field.key]
              const shown = percentageKeys.has(field.key) ? Number(raw) * 100 : raw
              return <label className="setting-field" key={field.key}><span>{field.label}</span><small>{field.help}</small><div><input type="number" min="0" step={percentageKeys.has(field.key) ? '0.1' : '1'} value={String(shown)} onChange={(event) => {
                const value = percentageKeys.has(field.key) ? String(Number(event.target.value) / 100) : (typeof raw === 'number' ? Number(event.target.value) : event.target.value)
                setDraft({ ...draft, [field.key]: value })
              }} />{field.suffix && <b>{field.suffix}</b>}</div></label>
            })}
          </div>
          <div className="settings-footer">{message && <span className="save-message">{message}</span>}<button className="button primary" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Save risk settings</button></div>
        </Panel>
      </div>
      <aside className="risk-aside">
        <Panel title="Safety invariants"><ul className="check-list"><li><Check /> Every signal passes Risk Engine</li><li><Check /> Position size is deterministic</li><li><Check /> Every approved entry has a stop</li><li><Check /> Failures default to HOLD</li><li><Check /> Changes create audit events</li></ul></Panel>
        <Panel title="Manual analysis cycle" subtitle="Debug only; normal risk rules still apply">
          <div className="cycle-buttons">{Object.keys(data.status.pairs).map((symbol) => <button className="button secondary" key={symbol} onClick={() => void runCycle(symbol)} disabled={Boolean(cycleLoading)}>{cycleLoading === symbol ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />} Analyze {symbol}</button>)}</div>
        </Panel>
        <div className="warning-card"><AlertTriangle size={20} /><div><strong>Dry-run cannot be changed here</strong><p>Switching to live funds requires an explicit deployment-level human review. This console does not expose that path.</p></div></div>
      </aside>
    </div>
  )
}

function LLMConfigPage({ apiKey, data, onUpdated }: { apiKey: string; data: DashboardData; onUpdated: (config: LLMConfig) => void }) {
  const [draft, setDraft] = useState(data.llmConfig)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState<string | null>(null)
  const save = async () => {
    setSaving(true); setMessage(null)
    try {
      const patch: Partial<LLMConfig> = {}
      ;(Object.keys(draft) as (keyof LLMConfig)[]).forEach((key) => {
        if (draft[key] !== data.llmConfig[key]) Object.assign(patch, { [key]: draft[key] })
      })
      if (!Object.keys(patch).length) { setMessage('No settings changed.'); return }
      const updated = await updateLLMConfig(apiKey, patch)
      onUpdated(updated); setMessage('LLM settings saved and added to the audit trail.')
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Could not save LLM settings') }
    finally { setSaving(false) }
  }
  return (
    <div className="risk-layout">
      <div className="page-stack">
        <section className="section-intro">
          <div><h2>LLM decision engine</h2><p>Controls which model classifies each signal as BUY, SELL, or HOLD. Changes are persisted and audited immediately.</p></div>
          <span className="safety-chip"><ShieldCheck size={16} /> Risk Engine still governs every trade</span>
        </section>
        <Panel title="Provider and model" subtitle="Applies on the Scheduler's next cycle — no service restart required">
          <div className="settings-grid">
            <label className="setting-field">
              <span>Provider</span>
              <small>Which LLM backend analyzes each candle</small>
              <div>
                <select value={draft.llm_provider} onChange={(event) => setDraft({ ...draft, llm_provider: event.target.value as LLMConfig['llm_provider'] })}>
                  <option value="anthropic">Anthropic (hosted)</option>
                  <option value="ollama">Ollama (self-hosted)</option>
                </select>
              </div>
            </label>
            <label className="setting-field">
              <span>Anthropic model</span>
              <small>Used only when provider is Anthropic</small>
              <div><input type="text" value={draft.anthropic_model} onChange={(event) => setDraft({ ...draft, anthropic_model: event.target.value })} /></div>
            </label>
            <label className="setting-field">
              <span>Ollama model</span>
              <small>Used only when provider is Ollama</small>
              <div><input type="text" value={draft.ollama_model} onChange={(event) => setDraft({ ...draft, ollama_model: event.target.value })} /></div>
            </label>
            <label className="setting-field">
              <span>Ollama temperature</span>
              <small>Higher values reduce repetitive/boilerplate answers</small>
              <div><input type="number" min="0" max="2" step="0.1" value={draft.ollama_temperature} onChange={(event) => setDraft({ ...draft, ollama_temperature: Number(event.target.value) })} /></div>
            </label>
          </div>
          <div className="settings-footer">{message && <span className="save-message">{message}</span>}<button className="button primary" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Save LLM settings</button></div>
        </Panel>
      </div>
      <aside className="risk-aside">
        <Panel title="Safety invariants"><ul className="check-list"><li><Check /> LLM output has no sizing field</li><li><Check /> Failures default to HOLD</li><li><Check /> Every signal still passes Risk Engine</li><li><Check /> Changes create audit events</li></ul></Panel>
        <div className="warning-card"><AlertTriangle size={20} /><div><strong>The LLM never sees account data</strong><p>Balance, API keys, and position size are excluded from every request by design — switching provider or model here cannot grant execution access.</p></div></div>
      </aside>
    </div>
  )
}

function KillSwitchDialog({ apiKey, currentlyEnabled, onClose, onChanged }: { apiKey: string; currentlyEnabled: boolean; onClose: () => void; onChanged: () => void }) {
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const enable = !currentlyEnabled
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!reason.trim()) return; setSaving(true)
    try { await setKillSwitch(apiKey, enable, reason.trim()); onChanged() }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Could not change kill switch'); setSaving(false) }
  }
  return <div className="modal-layer" role="presentation"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="kill-title"><button className="modal-close" onClick={onClose}><X size={19} /></button><div className={`modal-icon ${enable ? 'danger' : 'safe'}`}>{enable ? <Octagon /> : <Play />}</div><h2 id="kill-title">{enable ? 'Stop all new trading?' : 'Resume normal operation?'}</h2><p>{enable ? 'The global kill switch will immediately reject every new entry. Existing positions remain managed by Freqtrade safety rules.' : 'New signals will once again be eligible for deterministic risk evaluation. This does not guarantee an order.'}</p><form onSubmit={submit}><label htmlFor="reason">Reason for audit trail</label><textarea id="reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder={enable ? 'e.g. Reviewing unexpected market volatility' : 'e.g. Review complete, controls verified'} autoFocus maxLength={500} />{error && <div className="form-error">{error}</div>}<div className="modal-actions"><button type="button" className="button ghost" onClick={onClose}>Cancel</button><button type="submit" className={`button ${enable ? 'danger' : 'resume'}`} disabled={!reason.trim() || saving}>{saving && <LoaderCircle className="spin" size={17} />}{enable ? 'Enable kill switch' : 'Resume risk evaluation'}</button></div></form></div></div>
}

function DetailDrawer({ apiKey, detail, onClose }: { apiKey: string; detail: Exclude<Detail, null>; onClose: () => void }) {
  const [signal, setSignal] = useState<Signal | null>(null)
  const [timeline, setTimeline] = useState<AuditTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    const promise = detail.kind === 'signal' ? getSignal(apiKey, detail.id) : getAudit(apiKey, detail.id)
    promise.then((value) => { if (!active) return; if (detail.kind === 'signal') setSignal(value as Signal); else setTimeline(value as AuditTimeline) }).catch((caught) => active && setError(caught instanceof Error ? caught.message : 'Could not load detail'))
    return () => { active = false }
  }, [apiKey, detail])
  const events = timeline ? buildTimeline(timeline) : []
  return <div className="drawer-layer"><button className="drawer-scrim" onClick={onClose} aria-label="Close details" /><aside className="drawer"><header><div><span className="eyebrow">{detail.kind === 'signal' ? 'SIGNAL DETAIL' : 'AUDIT TIMELINE'}</span><h2>{detail.kind === 'signal' ? 'Raw model response' : `Trace ${shortId(detail.id)}`}</h2></div><button className="icon-button" onClick={onClose}><X size={20} /></button></header><div className="drawer-body">{error && <div className="form-error">{error}</div>}{!signal && !timeline && !error && <div className="drawer-loading"><LoaderCircle className="spin" /> Loading detail…</div>}{signal && <><div className="detail-hero"><Coin symbol={signal.symbol} /><div><strong>{signal.symbol} · {signal.timeframe}</strong><span>{dateTime(signal.created_at)}</span></div><ActionBadge action={signal.action} large /></div><dl className="detail-list"><div><dt>Confidence</dt><dd>{Math.round(Number(signal.confidence) * 100)}%</dd></div><div><dt>Price</dt><dd>{money(signal.price)}</dd></div><div><dt>ATR (14)</dt><dd>{money(signal.atr_14)}</dd></div><div><dt>Status</dt><dd>{readable(signal.status)}</dd></div><div><dt>Model</dt><dd>{signal.model_name}</dd></div><div><dt>Trace ID</dt><dd className="mono">{signal.trace_id}</dd></div></dl><h3>Validated reasoning</h3><div className="reason-box">{signal.reasoning}</div><ModelInputSection input={signal.model_input} /><h3>Raw provider response</h3><pre>{JSON.stringify(signal.raw_response, null, 2)}</pre></>}{timeline && <>{events.length ? <div className="timeline">{events.map((event, index) => <div className="timeline-item" key={`${event.type}-${index}`}><div className={`timeline-marker ${event.tone}`}>{event.icon}</div><div><span>{dateTime(event.at)}</span><strong>{event.title}</strong><p>{event.detail}</p>{event.payload && <details><summary>Event payload</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}</div></div>)}</div> : <EmptyState icon={<Clock3 />} title="No events for this trace" text="The audit timeline is currently empty." />}</>}</div></aside></div>
}

interface ModelInputShape {
  ohlcv?: { t: string; o: number; h: number; l: number; c: number; v: number }[]
  indicators?: {
    rsi_14?: number
    ema_50?: number
    ema_200?: number
    atr_14?: number
    volume_sma_20?: number
    macd?: { macd?: number; signal?: number; histogram?: number }
  }
  sentiment?: { score?: number; state?: string; confidence?: number; reasons?: string[] }
  position_context?: { has_open_position?: boolean; unrealized_pnl_pct?: number | null }
}

function ModelInputSection({ input }: { input?: Record<string, unknown> | null }) {
  if (!input) return <><h3>Model input</h3><p className="muted">Not captured for this signal.</p></>
  const { indicators, sentiment, position_context: position, ohlcv } = input as ModelInputShape
  return (
    <>
      <h3>Model input</h3>
      <dl className="detail-list">
        <div><dt>RSI (14)</dt><dd>{indicators?.rsi_14?.toFixed(1) ?? '—'}</dd></div>
        <div><dt>EMA 50</dt><dd>{indicators?.ema_50 !== undefined ? money(indicators.ema_50) : '—'}</dd></div>
        <div><dt>EMA 200</dt><dd>{indicators?.ema_200 !== undefined ? money(indicators.ema_200) : '—'}</dd></div>
        <div><dt>MACD histogram</dt><dd>{indicators?.macd?.histogram?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>ATR (14)</dt><dd>{indicators?.atr_14 !== undefined ? money(indicators.atr_14) : '—'}</dd></div>
        <div><dt>Volume SMA (20)</dt><dd>{indicators?.volume_sma_20?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>Sentiment</dt><dd>{sentiment ? `${readable(sentiment.state ?? null)} (${sentiment.score})` : '—'}</dd></div>
        <div><dt>Open position</dt><dd>{position?.has_open_position ? 'Yes' : 'No'}</dd></div>
      </dl>
      <details><summary>Full input payload ({ohlcv?.length ?? 0} candles)</summary><pre>{JSON.stringify(input, null, 2)}</pre></details>
    </>
  )
}

function buildTimeline(timeline: AuditTimeline) {
  const rows: { at: string; type: string; title: string; detail: string; tone: string; icon: ReactNode; payload?: Record<string, unknown> }[] = []
  timeline.signals.forEach((signal) => rows.push({ at: signal.created_at, type: 'signal', title: `${signal.action} signal received`, detail: `${Math.round(Number(signal.confidence) * 100)}% confidence · ${signal.symbol}`, tone: 'info', icon: <Bot size={15} /> }))
  timeline.risk_decisions.forEach((decision) => rows.push({ at: decision.created_at, type: 'decision', title: decision.approved ? 'Risk approved' : 'Risk did not approve', detail: decision.approved ? `${money(decision.position_size_usdt)} position · stop ${money(decision.stop_loss_price)}` : readable(decision.rejection_reason), tone: decision.approved ? 'success' : 'neutral', icon: decision.approved ? <Check size={15} /> : <X size={15} /> }))
  timeline.orders.forEach((order) => rows.push({ at: order.updated_at, type: 'order', title: `Order ${readable(order.status)}`, detail: `${order.symbol} · ${compactNumber(order.filled_amount ?? order.requested_amount)} base units`, tone: order.status === 'FAILED' ? 'danger' : 'success', icon: <ClipboardList size={15} /> }))
  timeline.audit_events.forEach((event) => rows.push({ at: event.created_at, type: event.event_type, title: readable(event.event_type), detail: 'Immutable audit event', tone: event.event_type.includes('FAILED') ? 'danger' : 'info', icon: <Activity size={15} />, payload: event.payload }))
  return rows.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
}

function buildPnlChart(positions: Position[]) {
  let running = 0
  return [...positions].sort((a, b) => new Date(a.closed_at ?? 0).getTime() - new Date(b.closed_at ?? 0).getTime()).map((position) => { running += Number(position.pnl_usdt ?? 0); return { label: new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(position.closed_at!)), pnl: Number(running.toFixed(2)) } })
}

function Panel({ title, subtitle, action, children, className = '' }: { title?: string; subtitle?: string; action?: ReactNode; children?: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}>{(title || action) && <header className="panel-head"><div>{title && <h3>{title}</h3>}{subtitle && <p>{subtitle}</p>}</div>{action}</header>}<div className="panel-body">{children}</div></section>
}

function MetricCard({ label, value, note, icon, tone }: { label: string; value: string; note: string; icon: ReactNode; tone?: 'positive' | 'negative' }) {
  return <article className={`metric-card ${tone ?? ''}`}><div className="metric-top"><span>{label}</span><i>{icon}</i></div><strong>{value}</strong><p>{note}</p></article>
}

const COIN_GLYPHS: Record<string, string> = { BTC: '₿', ETH: 'Ξ', BNB: 'B', XRP: 'X', SOL: 'S' }

function Coin({ symbol, small = false }: { symbol: string; small?: boolean }) {
  const base = symbol.split('/')[0]
  return <span className={`coin ${base.toLowerCase()} ${small ? 'small' : ''}`}>{COIN_GLYPHS[base] ?? base.slice(0, 1)}</span>
}

function ActionBadge({ action, large = false }: { action: Action | null; large?: boolean }) {
  return <span className={`badge action ${(action ?? 'NONE').toLowerCase()} ${large ? 'large' : ''}`}>{action ?? '—'}</span>
}

function DecisionBadge({ decision, large = false }: { decision?: Decision; large?: boolean }) {
  if (!decision) return <span className={`badge pending ${large ? 'large' : ''}`}><Clock3 size={12} /> Pending</span>
  return <span className={`badge ${decision.approved ? 'approved' : 'rejected'} ${large ? 'large' : ''}`}>{decision.approved ? <Check size={12} /> : <X size={12} />}{decision.approved ? 'Approved' : readable(decision.rejection_reason)}</span>
}

function OrderBadge({ status }: { status: Order['status'] }) {
  return <span className={`badge order ${status.toLowerCase()}`}>{status === 'FILLED' ? <Check size={12} /> : status === 'FAILED' ? <X size={12} /> : <Clock3 size={12} />}{readable(status)}</span>
}

function Confidence({ value }: { value: number }) {
  return <div className="confidence"><span><i style={{ width: `${Math.min(100, value * 100)}%` }} /></span><b>{Math.round(value * 100)}%</b></div>
}

function Filter({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return <label className="filter"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option}>{option}</option>)}</select></label>
}

function EmptyState({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="empty-state"><i>{icon}</i><strong>{title}</strong><p>{text}</p></div>
}

function EmptyTable({ text }: { text: string }) {
  return <div className="empty-table">{text}</div>
}
