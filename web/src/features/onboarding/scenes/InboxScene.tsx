// Scene 3: the slowdown lands in the Inbox, opens into the fix with its SQL, the SQL is
// copied, and once it's run the query is back to normal.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { SqlTokens } from '../../../components/SqlTokens'
import { hold, outBack, ramp, rise } from '../sceneClock'
import {
  MiniSidebar,
  Pointer,
  pointerAt,
  pressAt,
  StageCaption,
  Window,
} from './parts'

export const INBOX_DURATION = 10

const SQL_LINES = [
  'CREATE INDEX CONCURRENTLY orders_customer_created_idx',
  '  ON orders (customer_id, created_at DESC);',
]

const OLDER = [
  { title: 'Cart lookup is back to normal', meta: 'p95 12 ms', time: '8:41' },
  {
    title: 'New query on shop: Product page',
    meta: '2.4K calls a minute',
    time: '8:12',
  },
]

const PRESS_ROW = 2.55
const PRESS_COPY = 6.2
const PRESS_DONE = 7.6

export function InboxScene({ t }: { t: number }) {
  const arrive = ramp(t, 0.8, 1.3, outBack)
  const badge = ramp(t, 0.9, 1.25, outBack)
  const fresh = 1 - ramp(t, 1.6, 2.6)
  const detail = ramp(t, 2.85, 3.35)
  const copied = t > PRESS_COPY + 0.1
  const applied = ramp(t, PRESS_DONE + 0.1, PRESS_DONE + 0.45, outBack)
  const fixed = ramp(t, 8.3, 8.7)
  const pointer = pointerAt(t, [
    { t: 1.6, x: 560, y: 300 },
    { t: 2.4, x: 330, y: 100 },
    { t: 2.8, x: 330, y: 100 },
    { t: 5.6, x: 330, y: 100 },
    { t: 6.05, x: 236, y: 340 },
    { t: 6.9, x: 236, y: 340 },
    { t: 7.45, x: 350, y: 340 },
    { t: 7.9, x: 350, y: 340 },
    { t: 8.6, x: 590, y: 430 },
  ])
  const pointerOn = Math.min(
    ramp(t, 1.6, 1.9),
    1 - ramp(t, 2.9, 3.1) + ramp(t, 5.5, 5.8),
    1 - ramp(t, 8.1, 8.6)
  )
  const press = pressAt(t, [PRESS_ROW, PRESS_COPY, PRESS_DONE])

  return (
    <>
      <Window style={{ left: 24, top: 24, width: 552, height: 392 }}>
        <div className="flex h-full">
          <MiniSidebar
            active="Inbox"
            inbox={t > 0.9 && applied < 0.5 ? 1 : 0}
            inboxPop={badge}
            databases={[
              {
                name: 'shop',
                state: 'watching',
                toDo:
                  applied > 0.5 ? undefined : t > 0.9 ? '1 to do' : undefined,
              },
            ]}
          />
          <div className="relative min-w-0 flex-1 overflow-hidden border-l border-border-layout-2 bg-surface-layout-1">
            {/* The queue. */}
            <div
              className="absolute inset-0 px-2 pt-4"
              style={{
                opacity: 1 - detail,
                transform: `translateX(${-detail * 16}px)`,
              }}
            >
              <div className="px-3 text-label-medium text-content-layout-1">
                Inbox
              </div>
              <div className="mt-3 flex flex-col">
                <div
                  className={cn(
                    'grid grid-cols-[1rem_1fr_auto] items-start gap-2.5 rounded-panel px-3 py-2.5',
                    pressAt(t, [PRESS_ROW]) > 0 || t > PRESS_ROW
                      ? 'bg-surface-primary-soft'
                      : undefined
                  )}
                  style={{
                    opacity: Math.min(1, arrive * 1.2),
                    transform: `translateY(${(1 - arrive) * -14}px)`,
                    boxShadow:
                      fresh > 0 && arrive > 0
                        ? 'inset 0 0 0 1px var(--color-border-negative-soft)'
                        : undefined,
                  }}
                >
                  <span className="mt-1.5 size-2 justify-self-center rounded-pill bg-content-negative-plain" />
                  <span className="min-w-0">
                    <span className="block truncate text-caption font-medium text-content-layout-1">
                      Order history on shop is 30x slower than usual
                    </span>
                    <span className="mt-0.5 flex items-center gap-1.5 text-label-extra-small font-normal text-content-negative-soft">
                      <span className="size-1.5 rounded-pill bg-content-negative-plain" />
                      p95 184 ms, usually 6 ms
                    </span>
                  </span>
                  <span className="text-right text-label-extra-small font-normal tabular-nums text-content-layout-3">
                    9:08
                    <span className="block">shop</span>
                  </span>
                </div>
                {OLDER.map((item, index) => (
                  <div
                    key={item.title}
                    className="grid grid-cols-[1rem_1fr_auto] items-start gap-2.5 rounded-panel px-3 py-2.5"
                    style={{
                      transform: `translateY(${(1 - arrive) * -(50 - index * 0)}px)`,
                    }}
                  >
                    <Icon
                      name="tick"
                      label=""
                      size="small"
                      className="mt-0.5 text-content-positive-soft"
                    />
                    <span className="min-w-0">
                      <span className="block truncate text-caption text-content-layout-2">
                        {item.title}
                      </span>
                      <span className="mt-0.5 block text-label-extra-small font-normal text-content-layout-3">
                        {item.meta}
                      </span>
                    </span>
                    <span className="text-right text-label-extra-small font-normal tabular-nums text-content-layout-3">
                      {item.time}
                      <span className="block">shop</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* The item, opened. */}
            <div
              className="absolute inset-0 px-5 pt-4"
              style={{
                opacity: detail,
                transform: `translateX(${(1 - detail) * 24}px)`,
              }}
            >
              <div className="flex items-center gap-2 text-label-extra-small">
                <span className="size-2 rounded-pill bg-content-negative-plain" />
                <span className="text-content-negative-soft">Act now</span>
                <span className="font-normal text-content-layout-3">
                  shop, 9:08
                </span>
              </div>
              <div className="mt-1.5 text-headline-5 text-content-layout-1">
                Order history is 30x slower than usual
              </div>
              <div className="text-caption text-content-layout-3">
                p95 is 184 ms. It's usually 6 ms.
              </div>

              <div
                className={cn(
                  'mt-4 rounded-panel border bg-surface-layout-2 px-4 py-3.5 transition-colors duration-slow',
                  applied > 0.5
                    ? 'border-border-positive-soft'
                    : 'border-border-layout-2'
                )}
                style={rise(ramp(t, 3.2, 3.7), 8)}
              >
                <div className="text-overline uppercase text-content-layout-3">
                  The fix
                </div>
                <div className="mt-1 text-caption font-medium text-content-layout-1">
                  Add an index on orders (customer_id, created_at DESC)
                </div>
                <div className="text-label-extra-small font-normal text-content-layout-3">
                  Order history reads all 2.4M rows in orders to find one
                  customer's.
                </div>
                <div className="ph-no-capture mt-2.5 rounded-control bg-surface-layout-1 px-3 py-2">
                  {SQL_LINES.map((line, index) => {
                    const p = ramp(t, 3.8 + index * 0.45, 4.4 + index * 0.45)
                    return (
                      <div
                        key={line}
                        style={{ clipPath: `inset(0 ${(1 - p) * 100}% 0 0)` }}
                      >
                        <SqlTokens
                          sql={line}
                          className="text-mono-small whitespace-pre"
                        />
                      </div>
                    )
                  })}
                </div>
                <div
                  className="mt-3 flex items-center gap-2"
                  style={rise(ramp(t, 4.9, 5.3), 4)}
                >
                  <span
                    className="inline-flex h-7 items-center gap-1.5 rounded-xl bg-surface-primary-solid px-3 text-button-small text-content-primary-solid"
                    style={{
                      transform: `scale(${1 - 0.06 * pressAt(t, [PRESS_COPY])})`,
                    }}
                  >
                    <Icon
                      name={copied ? 'tick' : 'copy'}
                      label=""
                      size="small"
                    />
                    {copied ? 'Copied' : 'Copy SQL'}
                  </span>
                  <span
                    className={cn(
                      'inline-flex h-7 items-center gap-1.5 rounded-xl border px-3 text-button-small',
                      applied > 0
                        ? 'border-border-positive-soft bg-surface-positive-soft text-content-positive-soft'
                        : 'border-border-primary-soft text-content-primary-soft'
                    )}
                    style={{
                      transform: `scale(${(1 - 0.06 * pressAt(t, [PRESS_DONE])) * (applied > 0 ? 0.92 + 0.08 * applied : 1)})`,
                    }}
                  >
                    {applied > 0 && <Icon name="tick" label="" size="small" />}
                    {applied > 0 ? 'Applied' : 'I did this'}
                  </span>
                  <span className="ml-auto text-right text-label-extra-small font-normal text-content-layout-3">
                    Saves
                    <span className="ml-1 text-label-small text-content-layout-1 tabular-nums">
                      7.4 s
                    </span>{' '}
                    a min
                  </span>
                </div>
              </div>
              <div
                className="mt-3 flex items-center gap-2 text-caption text-content-positive-soft"
                style={rise(fixed, 6)}
              >
                <Icon name="tick-double" label="" size="small" />
                Checked at 9:14: p95 is back to 3.8 ms.
              </div>
            </div>
          </div>
        </div>
      </Window>
      <Pointer x={pointer.x} y={pointer.y} press={press} opacity={pointerOn} />
      <StageCaption opacity={hold(t, 0.5, 3.3)} style={{ left: 24, top: 404 }}>
        A finding lands in your Inbox
      </StageCaption>
      <StageCaption opacity={hold(t, 3.5, 7.7)} style={{ left: 24, top: 404 }}>
        With the fix and the SQL to run
      </StageCaption>
      <StageCaption
        opacity={hold(t, 7.9, INBOX_DURATION + 0.4)}
        style={{ left: 24, top: 404 }}
      >
        Then RDST checks that it worked
      </StageCaption>
    </>
  )
}
