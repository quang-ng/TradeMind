export function money(value: string | number | null, digits = 2): string {
  if (value === null) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(Number(value))
}

export function percent(value: string | number | null, ratio = true): string {
  if (value === null) return '—'
  const amount = Number(value) * (ratio ? 100 : 1)
  return `${amount >= 0 ? '+' : ''}${amount.toFixed(2)}%`
}

export function compactNumber(value: string | number | null, digits = 6): string {
  if (value === null) return '—'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: digits }).format(Number(value))
}

export function dateTime(value: string | null): string {
  if (!value) return 'Never'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(value))
}

export function timeAgo(value: string | null): string {
  if (!value) return 'No cycles yet'
  const seconds = Math.round((new Date(value).getTime() - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })
  if (Math.abs(seconds) < 60) return formatter.format(seconds, 'second')
  const minutes = Math.round(seconds / 60)
  if (Math.abs(minutes) < 60) return formatter.format(minutes, 'minute')
  const hours = Math.round(minutes / 60)
  if (Math.abs(hours) < 24) return formatter.format(hours, 'hour')
  return formatter.format(Math.round(hours / 24), 'day')
}

export function shortId(value: string): string {
  return `${value.slice(0, 8)}…`
}

export function readable(value: string | null): string {
  if (!value) return 'None'
  return value.toLowerCase().replaceAll('_', ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}
