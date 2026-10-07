// TanStack Query keys for /api/monitor. Every per-target key starts with
// ['monitor', target] so switching or invalidating a target is one prefix, and every
// ranged read carries its range.
import type { ApiRange, InboxQuery, MonitorRange, TimeWindow } from './types'

export const monitorKeys = {
  all: ['monitor'] as const,
  target: (target: string) => ['monitor', target] as const,
  health: (target: string, range: MonitorRange) =>
    ['monitor', target, 'health', range] as const,
  pulse: (target: string) => ['monitor', target, 'pulse'] as const,
  queries: (target: string, range: MonitorRange) =>
    ['monitor', target, 'queries', range] as const,
  /** A brushed window replaces the range in the key, so zooms cache separately. */
  queriesIn: (target: string, range: ApiRange, window?: TimeWindow) =>
    [
      'monitor',
      target,
      'queries',
      window ? `${window.from}-${window.to}` : range,
    ] as const,
  query: (target: string, key: string, range: ApiRange, window?: TimeWindow) =>
    [
      'monitor',
      target,
      'query',
      key,
      window ? `${window.from}-${window.to}` : range,
    ] as const,
  queryAnalysis: (target: string, key: string) =>
    ['monitor', target, 'query-analysis', key] as const,
  load: (target: string, range: ApiRange, window?: TimeWindow) =>
    [
      'monitor',
      target,
      'load',
      window ? `${window.from}-${window.to}` : range,
    ] as const,
  loadLatency: (
    target: string,
    range: ApiRange,
    keys: string[],
    window?: TimeWindow
  ) =>
    [
      'monitor',
      target,
      'load-latency',
      window ? `${window.from}-${window.to}` : range,
      keys.join(','),
    ] as const,
  compareRuns: (target: string, queryHash?: string) =>
    ['monitor', target, 'compare-runs', queryHash ?? 'all'] as const,
  series: (target: string, key: string, range: MonitorRange) =>
    ['monitor', target, 'series', key, range] as const,
  analyzeJobs: (target: string) => ['monitor', target, 'analyze-jobs'] as const,
  queryAnalyzeJobs: (target: string, key: string) =>
    ['monitor', target, 'analyze-jobs', key] as const,
  settings: (target: string) => ['monitor', target, 'settings'] as const,
  usage: (target: string, days: number) =>
    ['monitor', target, 'usage', days] as const,
  reports: (target: string) => ['monitor', target, 'reports'] as const,
  reportHistory: (target: string, limit: number, daily = false) =>
    ['monitor', target, 'reports', 'history', limit, daily] as const,
  report: (target: string, id: number | 'latest' | 'daily') =>
    ['monitor', target, 'reports', id] as const,
  inboxAll: ['monitor', 'inbox'] as const,
  inboxItem: (id: number) => ['monitor', 'inbox', 'item', id] as const,
  inbox: (query: InboxQuery = {}) =>
    [
      'monitor',
      'inbox',
      query.state ?? 'open',
      query.target ?? 'all',
      Boolean(query.include_muted),
    ] as const,
  muteRules: (target?: string) =>
    ['monitor', 'mute-rules', target ?? 'all'] as const,
  muteRulesAll: ['monitor', 'mute-rules'] as const,
  limit: (target?: string) => ['monitor', 'limit', target ?? 'all'] as const,
  limitAll: ['monitor', 'limit'] as const,
  work: ['monitor', 'work'] as const,
}
