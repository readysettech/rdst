import { describe, expect, it } from 'vitest'
import type {
  ContainerProgress,
  LoadEvent,
  PatternRow,
  PreflightChecks,
} from '../../lib/useDemo'
import {
  canStart,
  chartMarkers,
  dockerState,
  downloadDetail,
  formatDownloadSize,
  formatMs,
  parameterizeSql,
  provisionSteps,
  runningVerdict,
  spanLabel,
  sqlLine,
  tryItems,
} from './platformModel'

function row(overrides: Partial<PatternRow>): PatternRow {
  return {
    key: 'Q',
    title: 'Query',
    sql: 'SELECT 1',
    group: 'workload',
    status: 'pass_through',
    hits: 0,
    postgres_hits: 0,
    readyset_hits: 0,
    direct_avg_ms: null,
    router_avg_ms: null,
    reason: { kind: 'below_rank' },
    log_reason: null,
    ...overrides,
  }
}

function container(
  name: string,
  state: ContainerProgress['state'],
  detail?: string
): ContainerProgress {
  return { name, label: name, state, percent: 0, detail }
}

const checks: PreflightChecks = {
  docker_installed: true,
  docker_running: true,
  images_present: false,
  missing_images: ['a'],
  download_mb: 1100,
  disk_space_ok: true,
  disk_free_gb: 40,
  disk_required_gb: 2,
  amd64_emulation: 'not_applicable',
}

describe('parameterizeSql', () => {
  it('replaces comparison literals and strings but keeps group-by ordinals', () => {
    expect(
      parameterizeSql(
        "SELECT date_trunc('day', placed_at) FROM orders WHERE status = 'paid' AND total > 100 GROUP BY 1"
      ).text
    ).toBe(
      'SELECT date_trunc($1, placed_at) FROM orders WHERE status = $2 AND total > $3 GROUP BY 1'
    )
  })

  it('collapses whitespace for the one-line row shape', () => {
    expect(sqlLine('SELECT *\n  FROM orders\n  WHERE id = 7')).toBe(
      'SELECT * FROM orders WHERE id = $1'
    )
  })
})

describe('formatting', () => {
  it('formats latency for a cell with its unit beside it', () => {
    expect(formatMs(41.4)).toBe('41')
    expect(formatMs(2.13)).toBe('2.1')
    expect(formatMs(0.44)).toBe('0.4')
    expect(formatMs(0.04)).toBe('0.04')
    expect(formatMs(null)).toBeNull()
  })

  it('sizes the download in GB or MB and defaults to the full set', () => {
    expect(formatDownloadSize(1100)).toBe('1.1 GB')
    expect(formatDownloadSize(640)).toBe('640 MB')
    expect(formatDownloadSize(0)).toBe('1.1 GB')
  })

  it('says how much history the chart shows', () => {
    expect(spanLabel(600)).toBe('last 10 minutes')
    expect(spanLabel(40)).toBe('last 40 s')
  })
})

describe('preflight', () => {
  it('reads the Docker state and whether the demo can start', () => {
    expect(dockerState(null)).toBe('checking')
    expect(dockerState(checks)).toBe('ok')
    expect(dockerState({ ...checks, docker_running: false })).toBe(
      'not_running'
    )
    expect(dockerState({ ...checks, docker_installed: false })).toBe(
      'not_installed'
    )
    expect(canStart(checks)).toBe(true)
    expect(canStart(null)).toBe(false)
    expect(canStart({ ...checks, disk_space_ok: false })).toBe(false)
    expect(canStart({ ...checks, amd64_emulation: 'unavailable' })).toBe(false)
  })
})

describe('formatRows', () => {
  it('reads millions and thousands the way the card does', async () => {
    const { formatRows } = await import('./platformModel')
    expect(formatRows(1_500_000)).toEqual({ value: '1.5', unit: 'M' })
    expect(formatRows(1_000_000)).toEqual({ value: '1', unit: 'M' })
    expect(formatRows(850_000)).toEqual({ value: '850', unit: 'k' })
    expect(formatRows(512)).toEqual({ value: '512' })
  })
})

describe('downloadDetail', () => {
  it('names the component for a registry pull and keeps the count and size', () => {
    expect(
      downloadDetail(
        'Pulling public.ecr.aws/i9r8k6s4/readyset:1.2.0 (2/4, ~300 MB)...'
      )
    ).toEqual({
      label: 'Downloading the Readyset cache engine',
      count: '2 of 4 images',
      size: '~300 MB',
    })
  })

  it('passes plain narration through as not a download', () => {
    expect(
      downloadDetail('Starting the pre-built Orders database...')
    ).toBeNull()
  })
})

describe('provisionSteps', () => {
  it('marks the download active with the component name, never the registry path', () => {
    const steps = provisionSteps(
      [
        container(
          'pg',
          'starting',
          'Pulling public.ecr.aws/i9r8k6s4/sqp:2026 (3/4, ~75 MB)...'
        ),
        container('readyset', 'pending'),
        container('sqp', 'pending'),
        container('qp-cron', 'pending'),
      ],
      'provisioning',
      false
    )
    expect(steps.map((step) => step.state)).toEqual([
      'done',
      'active',
      'pending',
      'pending',
      'pending',
      'pending',
    ])
    expect(steps[1]).toMatchObject({
      label: 'Downloading the QueryPilot router',
      meta: '3 of 4 images',
      detail: '~75 MB',
    })
    expect(JSON.stringify(steps)).not.toContain('public.ecr.aws')
    expect(steps[5].detail).toBe('then the comparison opens')
  })

  it('walks the components in order and finishes with the workload', () => {
    const steps = provisionSteps(
      [
        container('pg', 'ready'),
        container('readyset', 'ready'),
        container('sqp', 'starting', 'Starting the QueryPilot router (4s)...'),
        container('qp-cron', 'pending'),
      ],
      'provisioning',
      false
    )
    expect(steps.map((step) => step.state)).toEqual([
      'done',
      'done',
      'done',
      'done',
      'active',
      'pending',
    ])
    expect(steps[4].detail).toBe('Starting the QueryPilot router (4s)...')
    const done = provisionSteps(
      ['pg', 'readyset', 'sqp', 'qp-cron'].map((name) =>
        container(name, 'ready')
      ),
      'ready',
      true
    )
    expect(done.every((step) => step.state === 'done')).toBe(true)
  })
})

describe('runningVerdict', () => {
  const patterns = [
    row({
      key: 'A',
      status: 'cached_manual',
      direct_avg_ms: 41,
      router_avg_ms: 0.4,
    }),
    row({
      key: 'B',
      status: 'cached_manual',
      direct_avg_ms: 27,
      router_avg_ms: 0.4,
    }),
    row({ key: 'C', direct_avg_ms: 18 }),
    row({ key: 'D', status: 'unsupported', direct_avg_ms: 2 }),
  ]

  it('counts the queries from memory and compares the two latencies', () => {
    expect(runningVerdict(patterns, false, 'count_star')).toEqual({
      lead: 'Readyset answers 2 of 4 queries from memory:',
      rest: '0.4 ms instead of 34 ms, fresh as orders change. Turn on QueryPilot to let it pick the rest.',
    })
  })

  it('names the policy once QueryPilot is on, and reads 0 before any cache', () => {
    expect(runningVerdict(patterns, true, 'sum_time').rest).toContain(
      'QueryPilot picks what to cache by policy: most expensive.'
    )
    expect(runningVerdict(patterns.slice(2), false, 'count_star')).toEqual({
      lead: 'Readyset answers 0 of 2 queries from memory.',
      rest: 'Turn on QueryPilot to let it pick the rest.',
    })
  })
})

describe('chartMarkers', () => {
  it('folds caches in the same minute into one marker and names the rest', () => {
    const base = new Date(2026, 9, 8, 9, 52, 10).getTime() / 1000
    const events: LoadEvent[] = [
      { t: base, type: 'manual_cache', label: 'you cached A' },
      { t: base + 20, type: 'manual_cache', label: 'you cached B' },
      { t: base + 90, type: 'qp_on', label: 'QueryPilot on' },
      { t: base + 150, type: 'mode_change', label: 'policy -> most expensive' },
    ]
    expect(chartMarkers(events).map((marker) => marker.label)).toEqual([
      '2 caches 9:52',
      'QueryPilot on 9:53',
      'policy: most expensive 9:54',
    ])
  })
})

describe('tryItems', () => {
  it('names the hand-cached query from the suggested row', () => {
    const items = tryItems([
      row({ key: 'H02', title: 'Cohort revenue' }),
      row({ key: 'H01', title: 'Revenue by category' }),
    ])
    expect(items.map((item) => item.id)).toEqual([
      'cache',
      'querypilot',
      'compare',
    ])
    expect(items[0].mono).toBe('Revenue by category')
    expect(tryItems([])[0].mono).toBe('a query')
  })
})
