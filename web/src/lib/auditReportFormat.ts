/**
 * Pure presentation helpers for the audit report: value formatters plus the
 * severity/verdict-to-variant maps every report section shares.
 */

import { formatSecondsShort } from './formatters'

export function formatSizeMb(mb: number | undefined): string {
  if (mb === undefined || mb === null) return '-'
  if (mb >= 1024) return `${(mb / 1024).toFixed(1)} GB`
  return `${Math.round(mb)} MB`
}

export function formatUptime(seconds: number | undefined): string {
  if (!seconds) return '-'
  const days = Math.floor(seconds / 86400)
  if (days >= 1) return `${days}d ${Math.floor((seconds % 86400) / 3600)}h`
  const hours = Math.floor(seconds / 3600)
  if (hours >= 1) return `${hours}h ${Math.floor((seconds % 3600) / 60)}m`
  return `${Math.floor(seconds / 60)}m`
}

export function formatMoney(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '-'
  return value.toLocaleString(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

export function formatReportNumber(
  value: number | null | undefined,
  maximumFractionDigits = 1
): string {
  if (value == null || !Number.isFinite(value)) return '-'
  return value.toLocaleString(undefined, { maximumFractionDigits })
}

export function formatPercent(value: number | null | undefined): string {
  return value == null ? '-' : `${formatReportNumber(value)}%`
}

export function formatScore(value: number | null | undefined): string {
  return value == null ? '-' : `${formatReportNumber(value, 0)}/100`
}

export function shortEngineVersion(
  engine: string | undefined,
  serverVersion: string | undefined
): string {
  const canonicalEngines: Record<string, string> = {
    postgresql: 'PostgreSQL',
    postgres: 'PostgreSQL',
    mysql: 'MySQL',
    mariadb: 'MariaDB',
  }
  const cleanEngine = engine
    ? canonicalEngines[engine.toLowerCase()] ||
      `${engine.charAt(0).toUpperCase()}${engine.slice(1).toLowerCase()}`
    : 'Database'
  if (!serverVersion) return cleanEngine
  const match = serverVersion.match(
    /\b(PostgreSQL|MySQL|MariaDB)\s+([0-9]+(?:\.[0-9]+){0,2})/i
  )
  if (match) {
    const name = canonicalEngines[match[1].toLowerCase()] || cleanEngine
    return `${name} ${match[2]}`
  }
  const version = serverVersion.match(/\b([0-9]+(?:\.[0-9]+){0,2})\b/)?.[1]
  return version ? `${cleanEngine} ${version}` : cleanEngine
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '-'
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

export function severityVariant(
  severity: string | undefined
): 'negative' | 'warning' | 'positive' | 'informative' {
  switch (severity) {
    case 'crit':
    case 'critical':
    case 'error':
      return 'negative'
    case 'warn':
      return 'warning'
    case 'ok':
      return 'positive'
    default:
      return 'informative'
  }
}

export function healthScoreColor(score: number): string {
  if (score >= 75) return 'text-content-positive-soft'
  if (score >= 60) return 'text-content-warning-soft'
  return 'text-content-negative-soft'
}

export function reportStatusLabel(status: unknown): string {
  switch (String(status ?? '').toLowerCase()) {
    case 'warn':
    case 'warning':
      return 'Needs attention'
    case 'crit':
    case 'critical':
    case 'error':
      return 'Critical'
    case 'ok':
      return 'Healthy'
    case 'info':
      return 'Information'
    default:
      return status == null || status === '' ? '-' : String(status)
  }
}

export const VERDICT_LABELS: Record<
  string,
  {
    label: string
    variant: 'positive' | 'warning' | 'negative' | 'informative'
  }
> = {
  right_sized: { label: 'Right-sized', variant: 'positive' },
  oversized: { label: 'Oversized', variant: 'warning' },
  under_provisioned: { label: 'Under-provisioned', variant: 'negative' },
  unknown: { label: 'Unknown', variant: 'informative' },
}

export function formatBytes(value: unknown): string {
  if (typeof value !== 'number') return '-'
  if (value >= 1024 ** 3) return `${(value / 1024 ** 3).toFixed(1)} GB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MB`
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`
  return `${formatReportNumber(value)} B`
}

export function recordValue(
  record: Record<string, unknown>,
  key: string
): string {
  const value = record[key]
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'boolean') return value ? 'Yes' : 'No'
  if (typeof value === 'number') return formatReportNumber(value, 2)
  if (Array.isArray(value)) return value.join(', ')
  return String(value)
}

/** Capture-window duration, with a dash for a missing or zero window. */
export function formatDuration(seconds: number | undefined): string {
  return seconds ? formatSecondsShort(seconds) : '-'
}

export function formatStatisticsWindow(seconds: number | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) {
    return 'Window unavailable'
  }
  let remaining = Math.floor(seconds)
  const days = Math.floor(remaining / 86_400)
  remaining %= 86_400
  const hours = Math.floor(remaining / 3_600)
  remaining %= 3_600
  const minutes = Math.floor(remaining / 60)
  const secs = remaining % 60
  const parts = [
    days > 0 ? `${days}d` : '',
    hours > 0 ? `${hours}h` : '',
    minutes > 0 ? `${minutes}m` : '',
    secs > 0 || partsAreEmpty(days, hours, minutes) ? `${secs}s` : '',
  ].filter(Boolean)
  return `Window ${parts.join(' ')}`
}

function partsAreEmpty(days: number, hours: number, minutes: number): boolean {
  return days === 0 && hours === 0 && minutes === 0
}

export function effortVariant(
  effort: string
): 'positive' | 'warning' | 'negative' | 'informative' {
  if (effort.toLowerCase() === 'low') return 'positive'
  if (effort.toLowerCase() === 'high') return 'negative'
  if (effort.toLowerCase() === 'medium') return 'warning'
  return 'informative'
}

export function impactVariant(
  impact: string
): 'positive' | 'warning' | 'informative' {
  if (impact.toLowerCase() === 'high') return 'positive'
  if (impact.toLowerCase() === 'medium') return 'warning'
  return 'informative'
}
