import type { AuditTimeline, DashboardData, LLMConfig, RiskConfig, Signal } from './types'

const API_ROOT = '/api'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, apiKey: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ROOT}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${apiKey}`,
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...init?.headers,
    },
  })
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = (await response.json()) as { detail?: string }
      if (body.detail) message = body.detail
    } catch {
      // Keep the status-based message when an upstream proxy returns text.
    }
    throw new ApiError(message, response.status)
  }
  return response.json() as Promise<T>
}

export async function loadDashboard(apiKey: string): Promise<DashboardData> {
  const [status, signals, decisions, orders, positions, config, llmConfig] = await Promise.all([
    request<DashboardData['status']>('/status', apiKey),
    request<DashboardData['signals']>('/signals?limit=100', apiKey),
    request<DashboardData['decisions']>('/decisions?limit=100', apiKey),
    request<DashboardData['orders']>('/orders?limit=100', apiKey),
    request<DashboardData['positions']>('/positions', apiKey),
    request<DashboardData['config']>('/config', apiKey),
    request<DashboardData['llmConfig']>('/config/llm', apiKey),
  ])
  return { status, signals, decisions, orders, positions, config, llmConfig }
}

export function getSignal(apiKey: string, signalId: string): Promise<Signal> {
  return request(`/signals/${signalId}`, apiKey)
}

export function getAudit(apiKey: string, traceId: string): Promise<AuditTimeline> {
  return request(`/audit?trace_id=${encodeURIComponent(traceId)}`, apiKey)
}

export function setKillSwitch(apiKey: string, enabled: boolean, reason: string) {
  return request(`/killswitch/${enabled ? 'enable' : 'disable'}`, apiKey, {
    method: 'POST',
    body: JSON.stringify({ reason }),
  })
}

export function triggerCycle(apiKey: string, symbol: string) {
  return request<{ trace_id: string | null; skipped: boolean }>(
    `/cycles/${encodeURIComponent(symbol.replace('/', '-'))}/trigger`,
    apiKey,
    { method: 'POST' },
  )
}

export function updateRiskConfig(apiKey: string, patch: Partial<RiskConfig>) {
  return request<RiskConfig>('/config', apiKey, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function updateLLMConfig(apiKey: string, patch: Partial<LLMConfig>) {
  return request<LLMConfig>('/config/llm', apiKey, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}
