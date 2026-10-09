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
import { viLabel } from './labels'
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
  { id: 'overview', label: 'Tổng quan', icon: LayoutDashboard },
  { id: 'signals', label: 'Tín hiệu & quyết định', icon: SignalIcon },
  { id: 'trades', label: 'Giao dịch', icon: WalletCards },
  { id: 'performance', label: 'Hiệu quả', icon: BarChart3 },
  { id: 'risk', label: 'Kiểm soát rủi ro', icon: SlidersHorizontal },
  { id: 'llm', label: 'Cấu hình LLM', icon: Bot },
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
          setError('API key không hợp lệ. Kiểm tra ADMIN_API_KEY rồi thử lại.')
        } else {
          setError(caught instanceof Error ? caught.message : 'Không kết nối được tới TradeMind')
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
        <p>Đang mở bảng điều khiển…</p>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="loading-screen">
        <XCircle size={34} />
        <h2>Không mở được bảng điều khiển</h2>
        <p>{error}</p>
        <button className="button primary" onClick={() => void refresh()}>
          Thử lại
        </button>
        <button className="button ghost" onClick={logout}>Dùng API key khác</button>
      </div>
    )
  }

  const pageTitle = nav.find((item) => item.id === page)?.label ?? 'Tổng quan'

  return (
    <div className="app-shell">
      <aside className={`sidebar ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-head">
          <Brand />
          <button className="icon-button mobile-only" onClick={() => setSidebarOpen(false)} aria-label="Đóng menu">
            <X size={20} />
          </button>
        </div>
        <nav className="nav-list" aria-label="Điều hướng chính">
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
              <strong>{data.status.killswitch_enabled ? 'Đang tạm dừng giao dịch' : 'Bộ kiểm soát rủi ro đang chạy'}</strong>
              <span>{data.status.dry_run ? 'Chạy thử · tiền giả lập' : 'Giao dịch thật'}</span>
            </div>
          </div>
          <button className="nav-item logout" onClick={logout}>
            <LogOut size={18} /> Đăng xuất
          </button>
        </div>
      </aside>

      {sidebarOpen && <button className="scrim" onClick={() => setSidebarOpen(false)} aria-label="Đóng menu" />}

      <main className="main-content">
        <header className="topbar">
          <div className="topbar-title">
            <button className="icon-button mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Mở menu">
              <Menu size={21} />
            </button>
            <div>
              <h1>{pageTitle}</h1>
              <p>Bảng điều khiển · {data.status.dry_run ? 'Môi trường chạy thử' : 'Môi trường thật'}</p>
            </div>
          </div>
          <div className="topbar-actions">
            <span className="updated desktop-only">
              <span className="online-dot" /> Cập nhật {lastUpdated ? timeAgo(lastUpdated.toISOString()) : 'vừa xong'}
            </span>
            <button className="icon-button" onClick={() => void refresh(true)} aria-label="Làm mới dữ liệu" disabled={refreshing}>
              <RefreshCw className={refreshing ? 'spin' : ''} size={18} />
            </button>
            <button
              className={`button ${data.status.killswitch_enabled ? 'resume' : 'danger'}`}
              onClick={() => setKillDialog(true)}
            >
              {data.status.killswitch_enabled ? <Play size={17} /> : <Octagon size={17} />}
              {data.status.killswitch_enabled ? 'Tiếp tục giao dịch' : 'Dừng giao dịch'}
            </button>
          </div>
        </header>

        {error && (
          <div className="error-banner">
            <AlertTriangle size={18} />
            <span>{error}. Đang hiển thị dữ liệu tải thành công gần nhất.</span>
            <button onClick={() => setError(null)} aria-label="Đóng"><X size={16} /></button>
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
      <div><strong>TradeMind</strong><span>Bảng điều khiển</span></div>
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
          <span className="eyebrow"><span className="online-dot" /> TRUY CẬP RIÊNG CHO NGƯỜI VẬN HÀNH</span>
          <h1>Hệ thống giao dịch của bạn,<br /><em>gói gọn trong một màn hình.</em></h1>
          <p>Theo dõi mọi tín hiệu, quyết định rủi ro, lệnh, vị thế và từng đồng tiền—không cần SSH vào máy chủ.</p>
          <div className="trust-row">
            <span><ShieldCheck size={17} /> Luôn qua kiểm soát rủi ro</span>
            <span><Bot size={17} /> LLM không có quyền đặt lệnh</span>
          </div>
        </div>
        <p className="login-foot">TradeMind tự vận hành và dành cho một người vận hành tin cậy duy nhất.</p>
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <div className="login-icon"><KeyRound size={25} /></div>
          <h2>Mở bảng điều khiển</h2>
          <p>Nhập <code>ADMIN_API_KEY</code> đã cấu hình cho hệ thống TradeMind này.</p>
          <label htmlFor="api-key">API key quản trị</label>
          <div className="secret-field">
            <input
              id="api-key"
              type={visible ? 'text' : 'password'}
              value={key}
              onChange={(event) => setKey(event.target.value)}
              placeholder="Dán API key của bạn"
              autoComplete="current-password"
              autoFocus
            />
            <button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? 'Ẩn API key' : 'Hiện API key'}>
              {visible ? <EyeOff size={18} /> : <Eye size={18} />}
            </button>
          </div>
          {error && <div className="form-error"><AlertTriangle size={16} /> {error}</div>}
          <button className="button primary login-button" type="submit" disabled={!key.trim()}>
            Kết nối an toàn <ArrowRight size={17} />
          </button>
          <div className="session-note"><LockKeyhole size={15} /> Key chỉ lưu trong tab trình duyệt này và sẽ bị xóa khi kết thúc phiên.</div>
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
            <strong>{status.killswitch_enabled ? 'Đang tạm dừng mở lệnh mới' : 'Hệ thống đang theo dõi bình thường'}</strong>
            <p>{status.killswitch_enabled ? 'Công tắc dừng khẩn cấp đang chặn mọi lệnh mua mới.' : 'Kiểm soát rủi ro đang bật, mọi giao dịch đều được đánh giá độc lập.'}</p>
          </div>
        </div>
        <span className={`mode-pill ${status.dry_run ? '' : 'live'}`}>{status.dry_run ? 'CHẠY THỬ' : 'TIỀN THẬT'}</span>
      </section>

      <section className="metric-grid">
        <MetricCard label="Tổng tài sản" value={money(status.equity_usdt)} note={`${money(status.free_balance_usdt)} USDT khả dụng · số dư Freqtrade`} icon={<CircleDollarSign />} />
        <MetricCard
          label="Lãi/lỗ hôm nay"
          value={percent(status.daily_pnl_pct)}
          note={`${money(totalPnl)} lãi/lỗ đã chốt từ trước tới nay`}
          icon={Number(status.daily_pnl_pct) >= 0 ? <ArrowUpRight /> : <ArrowDownRight />}
          tone={Number(status.daily_pnl_pct) >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard label="Vốn đang dùng" value={money(exposure)} note={`${status.open_positions} / ${data.config.max_open_positions} vị thế`} icon={<WalletCards />} />
        <MetricCard label="Tỷ lệ được duyệt" value={percent(signalApproval)} note={`${approved} được duyệt · ${failedOrders} lệnh lỗi`} icon={<Gauge />} tone={failedOrders ? 'negative' : undefined} />
      </section>

      <section className="overview-grid">
        <Panel className="pnl-panel" title="Lãi/lỗ đã chốt" subtitle="Lãi/lỗ lũy kế của các vị thế đã đóng">
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
                  <Tooltip contentStyle={{ background: '#111d1a', border: '1px solid #2a3b36', borderRadius: 10 }} formatter={(value) => [money(Number(value)), 'Lãi/lỗ lũy kế']} />
                  <Area type="monotone" dataKey="pnl" stroke="#30d39a" strokeWidth={2.2} fill="url(#pnlFill)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          ) : <EmptyState icon={<TrendingUp />} title="Biểu đồ sẽ hiện sau khi có vị thế đóng" text="Lãi/lỗ của các vị thế đã đóng sẽ tự động vẽ ở đây." />}
        </Panel>

        <Panel title="Cặp giao dịch" subtitle="Chu kỳ phân tích 1 giờ gần nhất">
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
        title="Tín hiệu mới nhất"
        subtitle="Từ nhận định của LLM tới quyết định rủi ro"
        action={<button className="text-button" onClick={() => onNavigate('signals')}>Xem tất cả <ChevronRight size={15} /></button>}
      >
        <div className="table-scroll">
          <table>
            <thead><tr><th>Cặp</th><th>Tín hiệu LLM</th><th>Độ tin cậy</th><th>Kết quả rủi ro</th><th>Giá</th><th>Thời gian</th><th /></tr></thead>
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
                    <td><button className="row-button" onClick={() => onTrace(signal.trace_id)} aria-label="Mở chi tiết"><ChevronRight size={17} /></button></td>
                  </tr>
                )
              })}
              {latestSignals.length === 0 && <tr><td colSpan={7}><EmptyTable text="Chưa có tín hiệu nào." /></td></tr>}
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
      <section className="section-intro"><div><h2>Tín hiệu AI và quyết định rủi ro</h2><p>Mọi nhận định của mô hình đều được lưu lại. Chỉ khi bộ kiểm soát rủi ro duyệt mới được đặt lệnh.</p></div><div className="legend"><span><i className="legend-dot llm" /> Nhận định LLM</span><ArrowRight size={14} /><span><i className="legend-dot risk" /> Kiểm soát rủi ro</span></div></section>
      <div className="filters">
        <Filter label="Cặp" value={symbol} onChange={setSymbol} options={symbolOptions} />
        <Filter label="Hành động" value={action} onChange={setAction} options={['ALL', 'BUY', 'SELL', 'HOLD']} />
        <span className="result-count">{signals.length} tín hiệu</span>
      </div>
      <div className="signal-cards">
        {signals.map((signal) => {
          const decision = data.decisions.find((item) => item.signal_id === signal.id)
          const order = decision ? data.orders.find((item) => item.risk_decision_id === decision.id) : undefined
          return <SignalCard key={signal.id} signal={signal} decision={decision} order={order} onDetail={onDetail} />
        })}
        {signals.length === 0 && <Panel><EmptyState icon={<SignalIcon />} title="Không có tín hiệu phù hợp" text="Thử đổi bộ lọc hoặc chờ nến tiếp theo đóng." /></Panel>}
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
          <span className="block-label"><Bot size={15} /> NHẬN ĐỊNH LLM</span>
          <div className="confidence-line"><strong>Độ tin cậy {Math.round(Number(signal.confidence) * 100)}%</strong><Confidence value={Number(signal.confidence)} /></div>
          <p>“{signal.reasoning}”</p>
          <div className="signal-meta"><span>Mô hình <strong>{signal.model_name}</strong></span><span>Giá <strong>{money(signal.price)}</strong></span><span>ATR(14) <strong>{money(signal.atr_14)}</strong></span></div>
        </div>
        <div className="flow-arrow"><ArrowRight /></div>
        <div className={`decision-block ${decision?.approved ? 'approved' : 'rejected'}`}>
          <span className="block-label"><ShieldCheck size={15} /> QUYẾT ĐỊNH RỦI RO</span>
          <DecisionBadge decision={decision} large />
          {decision ? (
            decision.approved ? <div className="decision-facts"><span>Khối lượng <strong>{money(decision.position_size_usdt)}</strong></span><span>Cắt lỗ <strong>{money(decision.stop_loss_price)}</strong></span><span>Lệnh <strong>{order ? readable(order.status) : 'Đang chờ'}</strong></span></div>
              : <p className="reject-reason">{readable(decision.rejection_reason)}</p>
          ) : <p className="muted">Đang chờ bộ kiểm soát rủi ro đánh giá</p>}
        </div>
      </div>
      <footer className="signal-footer"><button className="text-button" onClick={() => onDetail({ kind: 'signal', id: signal.id })}>Chi tiết LLM</button><button className="text-button" onClick={() => onDetail({ kind: 'trace', id: signal.trace_id })}>Xem toàn bộ nhật ký <ChevronRight size={15} /></button></footer>
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
          <h2>Giao dịch</h2>
          <p>Mỗi dòng là một vị thế — toàn bộ vòng đời từ điểm thiết lập, tính khối lượng theo rủi ro, khớp lệnh tới kết quả. Thống kê tổng hợp xem ở trang Hiệu quả.</p>
        </div>
      </section>

      <section className="mini-metrics">
        <MetricCard label="Vị thế đang mở" value={String(open.length)} note="Chỉ mua spot (không bán khống)" icon={<Activity />} />
        <MetricCard
          label="Lãi/lỗ đã chốt"
          value={money(realized)}
          note={`${closed.length} lệnh đã đóng`}
          icon={<CircleDollarSign />}
          tone={realized >= 0 ? 'positive' : 'negative'}
        />
        <MetricCard
          label="Tỷ lệ thắng"
          value={closed.length ? percent(winners / closed.length) : '—'}
          note={`${winners} lệnh có lãi`}
          icon={<TrendingUp />}
        />
      </section>

      <div className="filters">
        <Filter label="Cặp" value={symbol} onChange={setSymbol} options={symbolOptions} />
        <Filter label="Trạng thái" value={statusFilter} onChange={setStatusFilter} options={['ALL', 'OPEN', 'CLOSED']} />
        <Filter label="Kiểu thiết lập" value={regime} onChange={setRegime} options={regimeOptions} />
        <span className="result-count">{filtered.length} giao dịch</span>
      </div>

      <Panel title="Sổ giao dịch" subtitle="Mới nhất trước · bấm vào một dòng để xem chi tiết thiết lập, rủi ro và khớp lệnh">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Cặp / Chiều</th>
                <th>Kiểu thiết lập</th>
                <th>Điểm</th>
                <th>Giá vào → Giá ra</th>
                <th>Lãi/lỗ</th>
                <th>Bội số R</th>
                <th>Lý do thoát</th>
                <th>Mở lúc</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade) => (
                <TradeRow key={trade.position.id} trade={trade} onOpen={() => setSelectedId(trade.position.id)} />
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={9}><EmptyTable text="Không có giao dịch nào khớp bộ lọc." /></td>
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
            <small className="sub-cell">Mua</small>
          </div>
        </div>
      </td>
      <td>{trade.setupRegime ? readable(trade.setupRegime) : '—'}</td>
      <td className="mono">{trade.tradeScore ?? '—'}</td>
      <td className="mono">{money(position.entry_price)} → {isOpen ? '—' : money(position.exit_price)}</td>
      <td className={pnlTone}>
        {pnl == null ? '—' : money(pnl)}
        {pnlPct != null && <small className="sub-cell">{percent(pnlPct)}{isOpen ? ' tạm tính' : ''}</small>}
      </td>
      <td className={`mono ${perfToneClass(position.r_multiple ?? null)}`}>{rMultiple(position.r_multiple ?? null)}</td>
      <td>{isOpen ? <span className="badge hold">Đang mở</span> : position.exit_reason ? readable(position.exit_reason) : '—'}</td>
      <td>{dateTime(position.opened_at)}</td>
      <td>
        <button
          className="row-button"
          onClick={(event) => {
            event.stopPropagation()
            onOpen()
          }}
          aria-label="Mở chi tiết giao dịch"
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
      <button className="drawer-scrim" onClick={onClose} aria-label="Đóng chi tiết giao dịch" />
      <aside className="drawer">
        <header>
          <div>
            <span className="eyebrow">CHI TIẾT GIAO DỊCH</span>
            <h2>{position.symbol} · Mua</h2>
          </div>
          <button className="icon-button" onClick={onClose}><X size={20} /></button>
        </header>
        <div className="drawer-body">
          <div className="detail-hero">
            <Coin symbol={position.symbol} />
            <div>
              <strong>{position.symbol} · {isOpen ? 'Vị thế đang mở' : 'Đã đóng'}</strong>
              <span>
                Mở lúc {dateTime(position.opened_at)}
                {position.closed_at ? ` · đóng lúc ${dateTime(position.closed_at)}` : ''}
              </span>
            </div>
            <span className={`badge ${isOpen ? 'hold' : pnlNum !== null && pnlNum < 0 ? 'rejected' : 'approved'} large`}>
              {isOpen ? 'Đang mở' : pnlNum === null ? 'Đã đóng' : `${pnlNum >= 0 ? '+' : ''}${money(pnlNum)}`}
            </span>
          </div>

          <h3>Thiết lập &amp; điểm</h3>
          {hasSetup ? (
            <>
              <dl className="detail-list">
                <div><dt>Kiểu thiết lập</dt><dd>{trade.setupRegime ? readable(trade.setupRegime) : '—'}</dd></div>
                <div><dt>Mức biến động</dt><dd>{trade.volatilityRegime ? readable(trade.volatilityRegime) : '—'}</dd></div>
                <div><dt>Điểm giao dịch</dt><dd>{trade.tradeScore ?? '—'}{trade.tradeScore != null ? ' / 100' : ''}</dd></div>
                <div><dt>Độ tin cậy LLM</dt><dd>{signal ? `${Math.round(Number(signal.confidence) * 100)}%` : '—'}</dd></div>
                <div><dt>Hành động LLM</dt><dd>{signal ? readable(signal.action) : '—'}</dd></div>
                <div><dt>Mô hình</dt><dd>{signal?.model_name ?? '—'}</dd></div>
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
            <p className="muted">Không có dữ liệu thiết lập — lệnh mở trước khi có chấm điểm, hoặc tín hiệu nằm ngoài lịch sử đã tải.</p>
          )}

          <h3>Rủi ro</h3>
          {decision ? (
            <dl className="detail-list">
              <div><dt>Khối lượng vị thế</dt><dd>{money(decision.position_size_usdt)}</dd></div>
              <div><dt>Giá cắt lỗ</dt><dd>{money(decision.stop_loss_price)}</dd></div>
              <div><dt>Khoảng cách cắt lỗ</dt><dd>{decision.stop_distance_pct == null ? '—' : percent(decision.stop_distance_pct)}</dd></div>
              <div><dt>Ngân sách rủi ro danh nghĩa</dt><dd>{money(decision.nominal_risk_amount_usdt ?? null)}</dd></div>
              <div><dt>Rủi ro thực tế (1R)</dt><dd>{money(decision.actual_risk_usdt ?? null)}</dd></div>
              <div><dt>% rủi ro áp dụng</dt><dd>{decision.risk_pct_applied == null ? '—' : percent(decision.risk_pct_applied)}</dd></div>
              <div><dt>Tài sản lúc vào lệnh</dt><dd>{money(decision.equity_snapshot_usdt)}</dd></div>
            </dl>
          ) : (
            <p className="muted">Quyết định rủi ro của giao dịch này nằm ngoài lịch sử đã tải.</p>
          )}

          <h3>Khớp lệnh</h3>
          <dl className="detail-list">
            <div><dt>Giá vào</dt><dd>{money(position.entry_price)}</dd></div>
            <div><dt>Giá ra</dt><dd>{isOpen ? '—' : money(position.exit_price)}</dd></div>
            <div><dt>Số lượng</dt><dd>{compactNumber(position.amount)}</dd></div>
            <div><dt>Giá trị vào lệnh</dt><dd>{money(entryValue)}</dd></div>
            {isOpen && <div><dt>Giá hiện tại</dt><dd>{money(position.current_price)}</dd></div>}
            {isOpen && position.price_updated_at && <div><dt>Cập nhật giá lúc</dt><dd>{dateTime(position.price_updated_at)}</dd></div>}
          </dl>

          <h3>Lệnh</h3>
          {orders.length > 0 ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr><th>Lệnh</th><th>Chiều</th><th>Trạng thái</th><th>Đã khớp</th><th>Giá TB</th><th>Thời gian</th></tr>
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
            <p className="muted">Các lệnh của giao dịch này nằm ngoài lịch sử đã tải.</p>
          )}

          <h3>Kết quả</h3>
          <dl className="detail-list">
            <div><dt>Trạng thái</dt><dd>{readable(position.status)}</dd></div>
            <div><dt>{isOpen ? 'Lãi/lỗ tạm tính' : 'Lãi/lỗ đã chốt'}</dt><dd>{pnl == null ? '—' : money(pnl)}</dd></div>
            <div><dt>Tỷ suất</dt><dd>{pnlPct == null ? '—' : percent(pnlPct)}</dd></div>
            <div><dt>Bội số R</dt><dd>{rMultiple(position.r_multiple ?? null)}</dd></div>
            <div><dt>Lý do thoát</dt><dd>{isOpen ? 'Vị thế đang mở' : position.exit_reason ? readable(position.exit_reason) : '—'}</dd></div>
            <div><dt>Phí{position.fees_estimated ? ' (ước tính)' : ''}</dt><dd>{money(position.fees_usdt ?? null)}</dd></div>
            <div><dt>Mở lúc</dt><dd>{dateTime(position.opened_at)}</dd></div>
            <div><dt>Đóng lúc</dt><dd>{position.closed_at ? dateTime(position.closed_at) : '—'}</dd></div>
            <div><dt>Thời gian giữ</dt><dd>{position.closed_at ? duration(new Date(position.closed_at).getTime() - new Date(position.opened_at).getTime()) : 'Vẫn đang mở'}</dd></div>
          </dl>

          {trade.traceId && (
            <button className="text-button" onClick={() => onTrace(trade.traceId!)}>
              Xem toàn bộ nhật ký <ChevronRight size={15} />
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
        setError(caught instanceof Error ? caught.message : 'Không tải được số liệu hiệu quả')
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
          <h2>Hiệu quả giao dịch</h2>
          <p>Kỳ vọng chuẩn hóa theo R trên các vị thế đã đóng. 1R = mức rủi ro thực tế của mỗi lệnh (RiskDecision.actual_risk_usdt).</p>
        </div>
      </section>

      <div className="filters">
        <Filter label="Cặp" value={symbol} onChange={setSymbol} options={['ALL', ...symbols]} />
        <Filter label="Kiểu thiết lập" value={regime} onChange={setRegime} options={PERF_REGIMES} />
        <Filter label="Điểm giao dịch" value={scoreBucket} onChange={setScoreBucket} options={Object.keys(PERF_SCORE_BUCKETS)} />
      </div>

      {error && <div className="form-error"><AlertTriangle size={16} /> {error}</div>}
      {!summary && !error && <Panel><div className="drawer-loading"><LoaderCircle className="spin" /> Đang tính toán…</div></Panel>}

      {summary && summary.trades === 0 && (
        <Panel><EmptyState icon={<BarChart3 />} title="Không có lệnh đã đóng khớp bộ lọc" text="Số liệu kỳ vọng sẽ hiện khi có vị thế đóng theo bộ lọc đã chọn." /></Panel>
      )}

      {summary && summary.trades > 0 && (
        <>
          <section className="metric-grid">
            <MetricCard
              label="Tỷ lệ thắng"
              value={summary.win_rate === null ? '—' : percent(summary.win_rate)}
              note={`${summary.wins} thắng · ${summary.losses} thua · ${summary.breakeven} hòa`}
              icon={<Gauge />}
            />
            <MetricCard
              label="Kỳ vọng (R / lệnh)"
              value={rMultiple(summary.expectancy_r)}
              note={`trên ${summary.trades_with_r} lệnh có tính R`}
              icon={<TrendingUp />}
              tone={tone(summary.expectancy_r)}
            />
            <MetricCard
              label="Tổng R"
              value={rMultiple(summary.total_r)}
              note={`${money(summary.total_pnl_usdt)} lãi/lỗ đã chốt`}
              icon={<CircleDollarSign />}
              tone={tone(summary.total_r)}
            />
            <MetricCard
              label="Hệ số lợi nhuận"
              value={summary.profit_factor === null ? '—' : compactNumber(summary.profit_factor, 2)}
              note={summary.profit_factor === null ? 'chưa có lệnh thua' : 'tổng lãi ÷ tổng lỗ'}
              icon={<Activity />}
            />
          </section>

          <Panel title="Chi tiết" subtitle="Mọi số liệu tính trên các lệnh đã đóng theo bộ lọc">
            <dl className="detail-list">
              <div><dt>Số lệnh đã đóng</dt><dd>{summary.trades}</dd></div>
              <div><dt>Thắng / Thua / Hòa</dt><dd>{summary.wins} / {summary.losses} / {summary.breakeven}</dd></div>
              <div><dt>Lãi trung bình</dt><dd>{rMultiple(summary.avg_win_r)}</dd></div>
              <div><dt>Lỗ trung bình</dt><dd>{rMultiple(summary.avg_loss_r)}</dd></div>
              <div><dt>Tổng lãi/lỗ đã chốt</dt><dd>{money(summary.total_pnl_usdt)}</dd></div>
              <div><dt>Tổng phí (ước tính)</dt><dd>{money(summary.total_fees_usdt)}</dd></div>
              <div><dt>Sụt giảm tối đa</dt><dd>{drawdown(summary.max_drawdown_pct)}</dd></div>
              <div><dt>Sụt giảm trung bình</dt><dd>{drawdown(summary.avg_drawdown_pct)}</dd></div>
              <div><dt>Lệnh có tính R</dt><dd>{summary.trades_with_r} / {summary.trades}</dd></div>
              <div><dt>Trượt giá</dt><dd>chưa đo</dd></div>
            </dl>
            {legacyExcluded > 0 && (
              <p className="muted">{legacyExcluded} lệnh mở trước M1 không có R nên bị loại khỏi mọi chỉ số R.</p>
            )}
            {summary.max_drawdown_pct === null && (
              <p className="muted">Cần số dư tài khoản thực để tính sụt giảm, nhưng lúc tính không lấy được.</p>
            )}
          </Panel>

          <BreakdownTable
            title="Kỳ vọng theo kiểu thiết lập"
            subtitle="Phân loại của Strategy Selector, trong bộ lọc hiện tại"
            rows={summary.breakdowns?.by_regime ?? []}
          />
          <BreakdownTable
            title="Kỳ vọng theo mức biến động"
            subtitle="Nhóm ATR so với giá lúc vào lệnh, trong bộ lọc hiện tại"
            rows={summary.breakdowns?.by_volatility ?? []}
          />
          <BreakdownTable
            title="Kỳ vọng theo nhóm điểm giao dịch"
            subtitle="Thang điểm chất lượng thiết lập 0–100 lúc vào lệnh, trong bộ lọc hiện tại"
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
        <EmptyTable text="Chưa có lệnh đã đóng nào có dữ liệu này." />
      ) : (
        <div className="table-scroll">
          <table>
            <thead><tr><th>Nhóm</th><th>Số lệnh</th><th>Tỷ lệ thắng</th><th>Kỳ vọng R</th><th>Tổng R</th><th>Lãi/lỗ</th><th>Hệ số lợi nhuận</th></tr></thead>
            <tbody>
              {rows.map((cohort) => (
                <tr key={cohort.key}>
                  <td>{readable(cohort.key)}</td>
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
    { key: 'risk_per_trade_pct', label: 'Rủi ro mỗi lệnh', help: 'Phần tài sản tối đa chịu rủi ro cho một lệnh', suffix: '%' },
    { key: 'max_position_pct', label: 'Vị thế tối đa', help: 'Giới hạn cho một vị thế', suffix: '%' },
    { key: 'max_total_exposure_pct', label: 'Tổng vốn sử dụng tối đa', help: 'Tổng vốn tối đa của các vị thế đang mở', suffix: '%' },
    { key: 'max_daily_loss_pct', label: 'Ngắt khi lỗ trong ngày', help: 'Tự động bật dừng khẩn cấp', suffix: '%' },
    { key: 'min_confidence', label: 'Độ tin cậy LLM tối thiểu', help: 'Tín hiệu thấp hơn sẽ bị từ chối', suffix: '%' },
    { key: 'atr_stop_multiplier', label: 'Hệ số cắt lỗ ATR', help: 'Khoảng cắt lỗ theo biến động' },
    { key: 'min_stop_loss_pct', label: 'Cắt lỗ tối thiểu', help: 'Mức cắt lỗ thấp nhất cho mọi lệnh', suffix: '%' },
    { key: 'max_stop_loss_pct', label: 'Cắt lỗ tối đa', help: 'Mức cắt lỗ cao nhất cho mọi lệnh', suffix: '%' },
    { key: 'max_open_positions', label: 'Số vị thế mở tối đa', help: 'Tính trên tất cả các cặp' },
    { key: 'consecutive_loss_limit', label: 'Giới hạn thua liên tiếp', help: 'Tạm dừng mua sau số lần thua này' },
    { key: 'consecutive_loss_cluster_minutes', label: 'Gộp lệnh thua trong', help: 'Số phút: các lệnh thua đóng trong khoảng này tính là một lần thua' },
    { key: 'cooldown_minutes', label: 'Thời gian chờ mỗi cặp', help: 'Số phút sau khi đóng vị thế' },
    { key: 'signal_max_age_minutes', label: 'Tuổi tín hiệu tối đa', help: 'Số phút trước khi tín hiệu bị coi là cũ' },
  ]
  const percentageKeys = new Set<keyof RiskConfig>(['risk_per_trade_pct', 'max_position_pct', 'max_total_exposure_pct', 'max_daily_loss_pct', 'min_confidence', 'min_stop_loss_pct', 'max_stop_loss_pct'])
  const save = async () => {
    setSaving(true); setMessage(null)
    try {
      const patch: Partial<RiskConfig> = {}
      fields.forEach(({ key }) => {
        if (draft[key] !== data.config[key]) Object.assign(patch, { [key]: draft[key] })
      })
      if (!Object.keys(patch).length) { setMessage('Không có thay đổi nào.'); return }
      const updated = await updateRiskConfig(apiKey, patch)
      onUpdated(updated); setMessage('Đã lưu cấu hình rủi ro và ghi vào nhật ký.')
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Không lưu được cấu hình rủi ro') }
    finally { setSaving(false) }
  }
  const runCycle = async (symbol: string) => {
    setCycleLoading(symbol); setMessage(null)
    try {
      const result = await triggerCycle(apiKey, symbol)
      setMessage(result.skipped ? `Chu kỳ ${symbol} đang chạy hoặc đã xử lý rồi.` : `Đã chạy chu kỳ ${symbol}. Trace ${result.trace_id?.slice(0, 8)}…`)
      window.setTimeout(onRefresh, 1500)
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Không chạy được chu kỳ') }
    finally { setCycleLoading(null) }
  }
  return (
    <div className="risk-layout">
      <div className="page-stack">
        <section className="section-intro"><div><h2>Giới hạn rủi ro</h2><p>Các giá trị này điều khiển bộ kiểm soát rủi ro. Thay đổi được lưu và ghi nhật ký ngay.</p></div><span className="safety-chip"><ShieldCheck size={16} /> Dừng khẩn cấp luôn là chốt chặn đầu tiên</span></section>
        <Panel title="Giới hạn vào lệnh và danh mục" subtitle="Các trường phần trăm được hiển thị dạng %">
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
          <div className="settings-footer">{message && <span className="save-message">{message}</span>}<button className="button primary" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Lưu cấu hình rủi ro</button></div>
        </Panel>
      </div>
      <aside className="risk-aside">
        <Panel title="Nguyên tắc an toàn"><ul className="check-list"><li><Check /> Mọi tín hiệu đều qua kiểm soát rủi ro</li><li><Check /> Khối lượng lệnh được tính cố định theo quy tắc</li><li><Check /> Mọi lệnh được duyệt đều có cắt lỗ</li><li><Check /> Lỗi thì mặc định là GIỮ</li><li><Check /> Mọi thay đổi đều được ghi nhật ký</li></ul></Panel>
        <Panel title="Chạy phân tích thủ công" subtitle="Chỉ để kiểm tra; quy tắc rủi ro vẫn áp dụng">
          <div className="cycle-buttons">{Object.keys(data.status.pairs).map((symbol) => <button className="button secondary" key={symbol} onClick={() => void runCycle(symbol)} disabled={Boolean(cycleLoading)}>{cycleLoading === symbol ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />} Phân tích {symbol}</button>)}</div>
        </Panel>
        <div className="warning-card"><AlertTriangle size={20} /><div><strong>Không thể đổi chế độ chạy thử ở đây</strong><p>Chuyển sang tiền thật cần người kiểm tra ở cấp triển khai. Bảng điều khiển này không cho phép làm việc đó.</p></div></div>
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
      if (!Object.keys(patch).length) { setMessage('Không có thay đổi nào.'); return }
      const updated = await updateLLMConfig(apiKey, patch)
      onUpdated(updated); setMessage('Đã lưu cấu hình LLM và ghi vào nhật ký.')
    } catch (caught) { setMessage(caught instanceof Error ? caught.message : 'Không lưu được cấu hình LLM') }
    finally { setSaving(false) }
  }
  return (
    <div className="risk-layout">
      <div className="page-stack">
        <section className="section-intro">
          <div><h2>Bộ phân tích LLM</h2><p>Chọn mô hình dùng để phân loại tín hiệu MUA, BÁN hoặc GIỮ. Thay đổi được lưu và ghi nhật ký ngay.</p></div>
          <span className="safety-chip"><ShieldCheck size={16} /> Mọi giao dịch vẫn qua kiểm soát rủi ro</span>
        </section>
        <Panel title="Nhà cung cấp và mô hình" subtitle="Áp dụng từ chu kỳ tiếp theo — không cần khởi động lại">
          <div className="settings-grid">
            <label className="setting-field">
              <span>Nhà cung cấp</span>
              <small>Dịch vụ LLM dùng để phân tích từng nến</small>
              <div>
                <select value={draft.llm_provider} onChange={(event) => setDraft({ ...draft, llm_provider: event.target.value as LLMConfig['llm_provider'] })}>
                  <option value="anthropic">Anthropic (dịch vụ đám mây)</option>
                  <option value="ollama">Ollama (tự chạy)</option>
                </select>
              </div>
            </label>
            <label className="setting-field">
              <span>Mô hình Anthropic</span>
              <small>Chỉ dùng khi nhà cung cấp là Anthropic</small>
              <div><input type="text" value={draft.anthropic_model} onChange={(event) => setDraft({ ...draft, anthropic_model: event.target.value })} /></div>
            </label>
            <label className="setting-field">
              <span>Mô hình Ollama</span>
              <small>Chỉ dùng khi nhà cung cấp là Ollama</small>
              <div><input type="text" value={draft.ollama_model} onChange={(event) => setDraft({ ...draft, ollama_model: event.target.value })} /></div>
            </label>
            <label className="setting-field">
              <span>Nhiệt độ Ollama</span>
              <small>Giá trị cao hơn giúp câu trả lời ít lặp lại hơn</small>
              <div><input type="number" min="0" max="2" step="0.1" value={draft.ollama_temperature} onChange={(event) => setDraft({ ...draft, ollama_temperature: Number(event.target.value) })} /></div>
            </label>
          </div>
          <div className="settings-footer">{message && <span className="save-message">{message}</span>}<button className="button primary" onClick={() => void save()} disabled={saving}>{saving ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />} Lưu cấu hình LLM</button></div>
        </Panel>
      </div>
      <aside className="risk-aside">
        <Panel title="Nguyên tắc an toàn"><ul className="check-list"><li><Check /> LLM không được quyết định khối lượng lệnh</li><li><Check /> Lỗi thì mặc định là GIỮ</li><li><Check /> Mọi tín hiệu vẫn qua kiểm soát rủi ro</li><li><Check /> Mọi thay đổi đều được ghi nhật ký</li></ul></Panel>
        <div className="warning-card"><AlertTriangle size={20} /><div><strong>LLM không bao giờ thấy dữ liệu tài khoản</strong><p>Số dư, API key và khối lượng lệnh luôn bị loại khỏi mọi yêu cầu — đổi nhà cung cấp hay mô hình ở đây cũng không thể cho LLM quyền đặt lệnh.</p></div></div>
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
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Không đổi được trạng thái dừng khẩn cấp'); setSaving(false) }
  }
  return <div className="modal-layer" role="presentation"><div className="modal" role="dialog" aria-modal="true" aria-labelledby="kill-title"><button className="modal-close" onClick={onClose}><X size={19} /></button><div className={`modal-icon ${enable ? 'danger' : 'safe'}`}>{enable ? <Octagon /> : <Play />}</div><h2 id="kill-title">{enable ? 'Dừng mọi giao dịch mới?' : 'Tiếp tục hoạt động bình thường?'}</h2><p>{enable ? 'Công tắc dừng khẩn cấp sẽ lập tức từ chối mọi lệnh mua mới. Các vị thế đang mở vẫn được Freqtrade quản lý theo quy tắc an toàn.' : 'Tín hiệu mới sẽ lại được đưa vào đánh giá rủi ro. Điều này không đảm bảo sẽ có lệnh.'}</p><form onSubmit={submit}><label htmlFor="reason">Lý do (ghi vào nhật ký)</label><textarea id="reason" value={reason} onChange={(event) => setReason(event.target.value)} placeholder={enable ? 'VD: Đang xem xét biến động thị trường bất thường' : 'VD: Đã kiểm tra xong, mọi thứ ổn'} autoFocus maxLength={500} />{error && <div className="form-error">{error}</div>}<div className="modal-actions"><button type="button" className="button ghost" onClick={onClose}>Hủy</button><button type="submit" className={`button ${enable ? 'danger' : 'resume'}`} disabled={!reason.trim() || saving}>{saving && <LoaderCircle className="spin" size={17} />}{enable ? 'Bật dừng khẩn cấp' : 'Tiếp tục giao dịch'}</button></div></form></div></div>
}

function DetailDrawer({ apiKey, detail, onClose }: { apiKey: string; detail: Exclude<Detail, null>; onClose: () => void }) {
  const [signal, setSignal] = useState<Signal | null>(null)
  const [timeline, setTimeline] = useState<AuditTimeline | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    const promise = detail.kind === 'signal' ? getSignal(apiKey, detail.id) : getAudit(apiKey, detail.id)
    promise.then((value) => { if (!active) return; if (detail.kind === 'signal') setSignal(value as Signal); else setTimeline(value as AuditTimeline) }).catch((caught) => active && setError(caught instanceof Error ? caught.message : 'Không tải được chi tiết'))
    return () => { active = false }
  }, [apiKey, detail])
  const events = timeline ? buildTimeline(timeline) : []
  return <div className="drawer-layer"><button className="drawer-scrim" onClick={onClose} aria-label="Đóng chi tiết" /><aside className="drawer"><header><div><span className="eyebrow">{detail.kind === 'signal' ? 'CHI TIẾT TÍN HIỆU' : 'DÒNG THỜI GIAN'}</span><h2>{detail.kind === 'signal' ? 'Phản hồi gốc của mô hình' : `Trace ${shortId(detail.id)}`}</h2></div><button className="icon-button" onClick={onClose}><X size={20} /></button></header><div className="drawer-body">{error && <div className="form-error">{error}</div>}{!signal && !timeline && !error && <div className="drawer-loading"><LoaderCircle className="spin" /> Đang tải chi tiết…</div>}{signal && <><div className="detail-hero"><Coin symbol={signal.symbol} /><div><strong>{signal.symbol} · {signal.timeframe}</strong><span>{dateTime(signal.created_at)}</span></div><ActionBadge action={signal.action} large /></div><dl className="detail-list"><div><dt>Độ tin cậy</dt><dd>{Math.round(Number(signal.confidence) * 100)}%</dd></div><div><dt>Giá</dt><dd>{money(signal.price)}</dd></div><div><dt>ATR (14)</dt><dd>{money(signal.atr_14)}</dd></div><div><dt>Trạng thái</dt><dd>{readable(signal.status)}</dd></div><div><dt>Mô hình</dt><dd>{signal.model_name}</dd></div><div><dt>Trace ID</dt><dd className="mono">{signal.trace_id}</dd></div></dl><h3>Lập luận (đã kiểm tra)</h3><div className="reason-box">{signal.reasoning}</div><ModelInputSection input={signal.model_input} /><h3>Phản hồi gốc từ nhà cung cấp</h3><pre>{JSON.stringify(signal.raw_response, null, 2)}</pre></>}{timeline && <>{events.length ? <div className="timeline">{events.map((event, index) => <div className="timeline-item" key={`${event.type}-${index}`}><div className={`timeline-marker ${event.tone}`}>{event.icon}</div><div><span>{dateTime(event.at)}</span><strong>{event.title}</strong><p>{event.detail}</p>{event.payload && <details><summary>Dữ liệu sự kiện</summary><pre>{JSON.stringify(event.payload, null, 2)}</pre></details>}</div></div>)}</div> : <EmptyState icon={<Clock3 />} title="Không có sự kiện nào" text="Dòng thời gian hiện đang trống." />}</>}</div></aside></div>
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
  if (!input) return <><h3>Dữ liệu đầu vào mô hình</h3><p className="muted">Không lưu cho tín hiệu này.</p></>
  const { indicators, sentiment, position_context: position, ohlcv } = input as ModelInputShape
  return (
    <>
      <h3>Dữ liệu đầu vào mô hình</h3>
      <dl className="detail-list">
        <div><dt>RSI (14)</dt><dd>{indicators?.rsi_14?.toFixed(1) ?? '—'}</dd></div>
        <div><dt>EMA 50</dt><dd>{indicators?.ema_50 !== undefined ? money(indicators.ema_50) : '—'}</dd></div>
        <div><dt>EMA 200</dt><dd>{indicators?.ema_200 !== undefined ? money(indicators.ema_200) : '—'}</dd></div>
        <div><dt>MACD histogram</dt><dd>{indicators?.macd?.histogram?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>ATR (14)</dt><dd>{indicators?.atr_14 !== undefined ? money(indicators.atr_14) : '—'}</dd></div>
        <div><dt>SMA khối lượng (20)</dt><dd>{indicators?.volume_sma_20?.toFixed(2) ?? '—'}</dd></div>
        <div><dt>Tâm lý thị trường</dt><dd>{sentiment ? `${readable(sentiment.state ?? null)} (${sentiment.score})` : '—'}</dd></div>
        <div><dt>Đang có vị thế</dt><dd>{position?.has_open_position ? 'Có' : 'Không'}</dd></div>
      </dl>
      <details><summary>Toàn bộ dữ liệu đầu vào ({ohlcv?.length ?? 0} nến)</summary><pre>{JSON.stringify(input, null, 2)}</pre></details>
    </>
  )
}

function buildTimeline(timeline: AuditTimeline) {
  const rows: { at: string; type: string; title: string; detail: string; tone: string; icon: ReactNode; payload?: Record<string, unknown> }[] = []
  timeline.signals.forEach((signal) => rows.push({ at: signal.created_at, type: 'signal', title: `Nhận tín hiệu ${readable(signal.action)}`, detail: `Độ tin cậy ${Math.round(Number(signal.confidence) * 100)}% · ${signal.symbol}`, tone: 'info', icon: <Bot size={15} /> }))
  timeline.risk_decisions.forEach((decision) => rows.push({ at: decision.created_at, type: 'decision', title: decision.approved ? 'Rủi ro: được duyệt' : 'Rủi ro: không duyệt', detail: decision.approved ? `Vị thế ${money(decision.position_size_usdt)} · cắt lỗ ${money(decision.stop_loss_price)}` : readable(decision.rejection_reason), tone: decision.approved ? 'success' : 'neutral', icon: decision.approved ? <Check size={15} /> : <X size={15} /> }))
  timeline.orders.forEach((order) => rows.push({ at: order.updated_at, type: 'order', title: `Lệnh: ${readable(order.status)}`, detail: `${order.symbol} · ${compactNumber(order.filled_amount ?? order.requested_amount)} đơn vị coin`, tone: order.status === 'FAILED' ? 'danger' : 'success', icon: <ClipboardList size={15} /> }))
  timeline.audit_events.forEach((event) => rows.push({ at: event.created_at, type: event.event_type, title: readable(event.event_type), detail: 'Sự kiện nhật ký (không thể sửa)', tone: event.event_type.includes('FAILED') ? 'danger' : 'info', icon: <Activity size={15} />, payload: event.payload }))
  return rows.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime())
}

function buildPnlChart(positions: Position[]) {
  let running = 0
  return [...positions].sort((a, b) => new Date(a.closed_at ?? 0).getTime() - new Date(b.closed_at ?? 0).getTime()).map((position) => { running += Number(position.pnl_usdt ?? 0); return { label: new Intl.DateTimeFormat('vi-VN', { month: 'short', day: 'numeric' }).format(new Date(position.closed_at!)), pnl: Number(running.toFixed(2)) } })
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
  return <span className={`badge action ${(action ?? 'NONE').toLowerCase()} ${large ? 'large' : ''}`}>{action ? readable(action) : '—'}</span>
}

function DecisionBadge({ decision, large = false }: { decision?: Decision; large?: boolean }) {
  if (!decision) return <span className={`badge pending ${large ? 'large' : ''}`}><Clock3 size={12} /> Đang chờ</span>
  return <span className={`badge ${decision.approved ? 'approved' : 'rejected'} ${large ? 'large' : ''}`}>{decision.approved ? <Check size={12} /> : <X size={12} />}{decision.approved ? 'Được duyệt' : readable(decision.rejection_reason)}</span>
}

function OrderBadge({ status }: { status: Order['status'] }) {
  return <span className={`badge order ${status.toLowerCase()}`}>{status === 'FILLED' ? <Check size={12} /> : status === 'FAILED' ? <X size={12} /> : <Clock3 size={12} />}{readable(status)}</span>
}

function Confidence({ value }: { value: number }) {
  return <div className="confidence"><span><i style={{ width: `${Math.min(100, value * 100)}%` }} /></span><b>{Math.round(value * 100)}%</b></div>
}

function Filter({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return <label className="filter"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option} value={option}>{viLabel(option) ?? option}</option>)}</select></label>
}

function EmptyState({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return <div className="empty-state"><i>{icon}</i><strong>{title}</strong><p>{text}</p></div>
}

function EmptyTable({ text }: { text: string }) {
  return <div className="empty-table">{text}</div>
}
