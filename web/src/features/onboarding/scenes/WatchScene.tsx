// Scene 2: a database page in miniature while monitoring runs. Each minute a reading of the
// query statistics sweeps the top queries and the numbers move; on the third one Order
// history turns slow, which is where the next scene picks up.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { SqlTokens } from '../../../components/SqlTokens'
import { Radar } from '../../monitor-kit/Radar'
import { count, hold, linear, ramp, rise } from '../sceneClock'
import { MiniSidebar, StageCaption, Window } from './parts'

export const WATCH_DURATION = 9.5

/** Scene seconds at which a reading lands; each stands for one minute. */
const READINGS = [1.4, 3.6, 5.8, 8.0]
const SLOW_AT = READINGS[2]

const QUERIES = [
  {
    name: 'Order history',
    sql: 'SELECT id, total FROM orders WHERE customer_id = $1',
    calls: [1180, 1204, 1236, 1251, 1262],
    p95: [6, 6, 6, 184, 186],
  },
  {
    name: 'Product page',
    sql: 'SELECT * FROM products WHERE slug = $1',
    calls: [2410, 2433, 2470, 2491, 2512],
    p95: [9.2, 9.1, 9.3, 9.2, 9.2],
  },
  {
    name: 'Cart lookup',
    sql: 'SELECT * FROM cart_items WHERE cart_id = $1',
    calls: [860, 874, 881, 902, 915],
    p95: [12, 12, 11, 12, 12],
  },
]

const CHART_W = 364
const CHART_H = 58

function qpsAt(x: number) {
  return (
    0.55 +
    0.12 * Math.sin(x * 7.1) +
    0.07 * Math.sin(x * 17.3 + 1.2) +
    0.05 * Math.sin(x * 31.7 + 0.4)
  )
}

const POINTS = Array.from({ length: 61 }, (_, i) => {
  const x = i / 60
  return [x * CHART_W, CHART_H - qpsAt(x) * CHART_H] as const
})
const LINE = POINTS.map(
  ([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
).join('')
const AREA = `${LINE}L${CHART_W},${CHART_H}L0,${CHART_H}Z`

function readingIndex(t: number) {
  return READINGS.filter((at) => t >= at).length
}

function fmtMs(value: number) {
  return value >= 100 ? `${Math.round(value)} ms` : `${value.toFixed(1)} ms`
}

export function WatchScene({ t }: { t: number }) {
  const n = readingIndex(t)
  const since = n ? t - READINGS[n - 1] : 99
  const sweep = since < 0.6 ? since / 0.6 : -1
  const reveal = ramp(t, 0.2, WATCH_DURATION - 0.6, linear)
  const slow = ramp(t, SLOW_AT, SLOW_AT + 0.5)
  const qps =
    count(t, 0.2, 1.4, 0, 41.2) + (n > 1 ? Math.sin(n * 2.1) * 1.4 : 0)
  const minute = 5 + Math.max(0, n - 1)

  return (
    <>
      <Window style={{ left: 24, top: 24, width: 552, height: 392 }}>
        <div className="flex h-full">
          <MiniSidebar
            active=""
            databases={[{ name: 'shop', state: 'watching' }]}
            selected="shop"
          />
          <div className="min-w-0 flex-1 border-l border-border-layout-2 bg-surface-layout-1 px-5 pt-4">
            <div className="flex items-center gap-2.5">
              <Radar
                state="watching"
                name="shop"
                tooltip={false}
                size="large"
              />
              <span className="text-label-medium text-content-layout-1">
                shop
              </span>
              <span className="text-label-extra-small font-normal text-content-layout-3">
                PostgreSQL 16
              </span>
              <span className="ml-auto text-label-extra-small font-normal tabular-nums text-content-layout-3">
                {n
                  ? `Last reading 9:0${minute}:01`
                  : 'First reading in a moment'}
              </span>
            </div>

            <div className="mt-3 grid grid-cols-3 gap-3">
              {[
                {
                  label: 'Queries',
                  value: qps.toFixed(1),
                  unit: 'a second',
                },
                {
                  label: 'Slowest p95',
                  value: n >= 3 ? '184' : '12',
                  unit: 'ms',
                  bad: n >= 3,
                },
                {
                  label: 'Queries seen',
                  value: String(
                    Math.round(count(t, 0.2, 1.4, 0, 37)) + Math.max(0, n - 1)
                  ),
                  unit: '',
                },
              ].map((metric) => (
                <div key={metric.label}>
                  <div className="text-label-extra-small font-normal text-content-layout-3">
                    {metric.label}
                  </div>
                  <div
                    className={cn(
                      'text-headline-4 tabular-nums',
                      metric.bad
                        ? 'text-content-negative-soft'
                        : 'text-content-layout-1'
                    )}
                  >
                    {metric.value}
                    {metric.unit && (
                      <span className="ml-1 text-caption text-content-layout-3">
                        {metric.unit}
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <svg
              aria-hidden="true"
              width={CHART_W}
              height={CHART_H}
              className="mt-2 block overflow-visible"
            >
              <defs>
                <clipPath id="tour-watch-reveal">
                  <rect width={CHART_W * reveal} height={CHART_H + 4} y={-2} />
                </clipPath>
              </defs>
              <line
                x1={0}
                x2={CHART_W}
                y1={CHART_H}
                y2={CHART_H}
                className="stroke-border-layout-2"
              />
              <g clipPath="url(#tour-watch-reveal)">
                <path d={AREA} className="fill-series-1" opacity={0.12} />
                <path
                  d={LINE}
                  className="fill-none stroke-series-1"
                  strokeWidth={1.6}
                  strokeLinejoin="round"
                />
              </g>
              <circle
                cx={CHART_W * reveal}
                cy={CHART_H - qpsAt(reveal) * CHART_H}
                r={3}
                className="fill-series-1"
                opacity={reveal > 0.01 && reveal < 0.999 ? 1 : 0}
              />
            </svg>

            <div className="mt-4 flex items-baseline justify-between text-label-extra-small font-normal text-content-layout-3">
              <span className="text-label-small text-content-layout-1">
                Top queries
              </span>
              <span className="flex gap-6">
                <span className="w-14 text-right">Calls</span>
                <span className="w-16 text-right">p95</span>
              </span>
            </div>
            <div className="relative mt-1.5">
              {QUERIES.map((query, row) => {
                const bad = row === 0 && slow > 0
                const appear = ramp(t, 0.3 + row * 0.15, 0.8 + row * 0.15)
                return (
                  <div
                    key={query.name}
                    className={cn(
                      'flex items-center gap-3 rounded-control border-t border-border-layout-2 px-1.5 py-2 transition-colors duration-slow',
                      bad && 'bg-surface-negative-soft'
                    )}
                    style={rise(appear, 6)}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="text-caption text-content-layout-1">
                        {query.name}
                      </div>
                      <SqlTokens
                        sql={query.sql}
                        className="truncate text-mono-small whitespace-nowrap"
                      />
                    </div>
                    <span className="w-14 text-right text-caption tabular-nums text-content-layout-2">
                      {query.calls[n].toLocaleString('en-US')}
                    </span>
                    <span
                      className={cn(
                        'flex w-16 items-center justify-end gap-1 text-caption whitespace-nowrap tabular-nums',
                        bad
                          ? 'text-content-negative-soft'
                          : 'text-content-layout-2'
                      )}
                    >
                      {bad && <Icon name="arrow-up" label="" size="small" />}
                      {fmtMs(query.p95[n])}
                    </span>
                  </div>
                )
              })}
              {sweep >= 0 && (
                <div
                  aria-hidden="true"
                  className="pointer-events-none absolute inset-x-0 h-10 rounded-control bg-linear-to-b from-transparent via-surface-primary-soft to-transparent"
                  style={{
                    top: `${sweep * 100}%`,
                    transform: 'translateY(-50%)',
                    opacity: 1 - sweep * 0.6,
                  }}
                />
              )}
            </div>
          </div>
        </div>
      </Window>
      <StageCaption
        opacity={hold(t, 0.4, SLOW_AT - 0.2)}
        style={{ left: 24, top: 404 }}
      >
        Every minute, RDST reads the query statistics
      </StageCaption>
      <StageCaption
        opacity={hold(t, SLOW_AT + 0.3, WATCH_DURATION + 0.4)}
        style={{ left: 24, top: 404 }}
      >
        Order history just got 30x slower
      </StageCaption>
    </>
  )
}
