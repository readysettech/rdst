// Scene 1: the add-database dialog, twice. A connection string types itself in and
// connects; then the same dialog hands the job to a coding agent, whose steps tick through
// while the new database lands in the sidebar.
import { cn } from '@rs/tailwind-base'
import { Icon } from '@rs/ui-new/icon'
import { Spinner } from '@rs/ui-new/spinner'
import { hold, outBack, ramp, rise, typed } from '../sceneClock'
import {
  MiniSidebar,
  Pointer,
  pointerAt,
  pressAt,
  StageCaption,
  Window,
} from './parts'

export const CONNECT_DURATION = 10

const URL_TEXT = 'postgresql://app@db.internal:5432/shop'
const PROMPT_TEXT =
  "RDST is a local database monitoring app from Readyset that I just installed. It runs on this machine at http://127.0.0.1:8787. I'm asking you to connect my database to it so I don't have to type it in."
const STEPS = [
  { label: 'Connected to RDST', at: 6.7 },
  { label: 'Database added', at: 7.4 },
  { label: 'Connection tested', at: 8.1 },
  { label: 'Monitoring on', at: 8.8 },
]

const HOME_STEPS = [
  'Connect your database',
  'Turn on monitoring',
  'Sign in (optional)',
]

const PRESS_ADD = 3.45
const PRESS_AGENT = 5.15
const PRESS_COPY = 6.05

function MiniStep({
  label,
  index,
  t,
  at,
}: {
  label: string
  index: number
  t: number
  at: number
}) {
  const done = ramp(t, at, at + 0.35, outBack)
  const active = t > at - 0.7 && t < at
  return (
    <div className="flex h-6 items-center gap-2.5">
      <span className="relative grid size-5 place-items-center">
        <span
          className={cn(
            'absolute inset-0 grid place-items-center rounded-pill border border-border-layout-2 text-label-extra-small font-normal text-content-layout-3 tabular-nums',
            active && 'bg-surface-layout-2'
          )}
          style={{ opacity: 1 - Math.min(1, done) }}
        >
          {active ? <Spinner size="base" color="primary-soft" /> : index + 1}
        </span>
        <span
          className="absolute inset-0 grid place-items-center rounded-pill bg-surface-positive-soft text-content-positive-soft"
          style={{
            opacity: Math.min(1, done * 1.4),
            transform: `scale(${done})`,
          }}
        >
          <Icon name="tick" label="" size="small" />
        </span>
      </span>
      <span
        className={cn(
          'text-caption',
          done > 0.2 || active
            ? 'text-content-layout-1'
            : 'text-content-layout-3'
        )}
      >
        {label}
      </span>
    </div>
  )
}

export function ConnectScene({ t }: { t: number }) {
  const dialog = ramp(t, 0.1, 0.7)
  const phaseA = 1 - ramp(t, 4.95, 5.35)
  const phaseB = ramp(t, 5.3, 5.75)
  const url = typed(
    URL_TEXT,
    ramp(t, 1.0, 2.8, (x) => x)
  )
  const addBusy = t > PRESS_ADD + 0.1 && t < 4.2
  const connected = ramp(t, 4.2, 4.6)
  const copied = t > PRESS_COPY + 0.1
  const caret = Math.floor(t * 2.4) % 2 === 0 && t > 0.8 && t < 3.3
  const pointer = pointerAt(t, [
    { t: 2.7, x: 560, y: 420 },
    { t: 3.3, x: 478, y: 366 },
    { t: 4.6, x: 478, y: 366 },
    { t: 5.0, x: 330, y: 122 },
    { t: 5.4, x: 330, y: 122 },
    { t: 5.9, x: 484, y: 366 },
    { t: 6.4, x: 484, y: 366 },
    { t: 7.2, x: 590, y: 430 },
  ])
  const pointerOn = Math.min(ramp(t, 2.7, 3.0), 1 - ramp(t, 6.6, 7.1))
  const press = pressAt(t, [PRESS_ADD, PRESS_AGENT, PRESS_COPY])
  const shop = ramp(t, 8.95, 9.4, outBack)

  return (
    <>
      <Window style={{ left: 24, top: 24, width: 552, height: 392 }}>
        <div className="flex h-full">
          <MiniSidebar
            active="Home"
            databases={[
              {
                name: 'shop',
                state: 'watching',
                appear: shop,
              },
            ]}
            selected={shop > 0.5 ? 'shop' : undefined}
          />
          <div className="flex-1 border-l border-border-layout-2 bg-surface-layout-1 px-5 pt-4">
            <div className="text-label-medium text-content-layout-1">Home</div>
            <div className="mt-6 text-headline-5 text-content-layout-1">
              Find out what's slowing your database.
            </div>
            <div className="mt-4 flex flex-col gap-1.5">
              {HOME_STEPS.map((label, index) => {
                const done = index < 2 && shop > 0.3
                const active = index === (shop > 0.3 ? 2 : 0)
                return (
                  <div
                    key={label}
                    className={cn(
                      'flex items-center gap-3 rounded-panel border px-3 py-2.5',
                      active
                        ? 'border-border-layout-2 bg-surface-layout-2'
                        : 'border-transparent'
                    )}
                  >
                    <span
                      className={cn(
                        'grid size-6 place-items-center rounded-pill text-label-extra-small tabular-nums',
                        done
                          ? 'bg-surface-positive-soft text-content-positive-soft'
                          : active
                            ? 'bg-surface-primary-solid text-content-primary-solid'
                            : 'bg-surface-layout-2 text-content-layout-3'
                      )}
                    >
                      {done ? (
                        <Icon name="tick" label="" size="small" />
                      ) : (
                        index + 1
                      )}
                    </span>
                    <span
                      className={cn(
                        'text-caption',
                        done || active
                          ? 'text-content-layout-1'
                          : 'text-content-layout-3'
                      )}
                    >
                      {label}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
        <div
          className="absolute inset-0 bg-surface-scrim"
          style={{ opacity: dialog * 0.55 * (1 - shop) }}
        />
      </Window>

      <div
        className="absolute overflow-hidden rounded-card border border-border-layout-2 bg-surface-primary-soft shadow-elevation-3"
        style={{
          left: 150,
          top: 46,
          width: 410,
          height: 352,
          opacity: dialog * (1 - shop),
          transform: `translateY(${(1 - dialog) * 14}px) scale(${0.97 + 0.03 * dialog - 0.03 * shop})`,
        }}
      >
        {/* The connection string path. */}
        <div
          className="absolute inset-0 flex flex-col"
          style={{ opacity: phaseA, pointerEvents: 'none' }}
        >
          <div className="px-5 pt-4 text-headline-5 text-content-layout-1">
            Add a database
          </div>
          <div className="px-5 pt-3">
            <div
              className={cn(
                'flex items-center gap-2.5 rounded-panel border bg-surface-layout-2 px-3 py-2.5',
                t > 4.7
                  ? 'border-border-primary-soft'
                  : 'border-border-layout-2'
              )}
            >
              <span className="grid size-7 place-items-center rounded-control bg-surface-rising-soft text-content-rising-soft">
                <Icon name="sparkles" label="" size="small" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-caption text-content-layout-1">
                  Connect with Claude (or any coding agent)
                </span>
                <span className="block text-label-extra-small font-normal text-content-layout-3">
                  No password to type
                </span>
              </span>
              <Icon
                name="chevron-right"
                label=""
                size="small"
                className="text-content-layout-3"
              />
            </div>
          </div>
          <div className="mt-3.5 mb-2.5 flex items-center gap-2 px-5 text-label-extra-small font-normal text-content-layout-3">
            <span className="h-px flex-1 bg-border-layout-2" />
            or paste a connection string
            <span className="h-px flex-1 bg-border-layout-2" />
          </div>
          <div className="px-5">
            <div className="text-label-extra-small text-content-layout-2">
              Connection string
            </div>
            <div
              className={cn(
                'mt-1 flex h-9 items-center rounded-control border bg-surface-layout-2 px-3 font-mono text-mono-small text-content-layout-1',
                t > 0.8 && t < 3.3
                  ? 'border-border-primary-soft'
                  : 'border-border-layout-2'
              )}
            >
              <span className="truncate">{url}</span>
              <span
                className="ml-px h-4 w-px bg-content-layout-1"
                style={{ opacity: caret ? 1 : 0 }}
              />
            </div>
            <div
              className="mt-3 flex items-center gap-2 text-caption text-content-positive-soft"
              style={rise(connected, 4)}
            >
              <Icon name="tick" label="" size="small" />
              Connected to PostgreSQL 16.3. Query statistics are readable.
            </div>
          </div>
          <div className="mt-auto flex items-center gap-2 border-t border-border-layout-2 px-5 py-3">
            <span className="flex-1 text-label-extra-small font-normal text-content-layout-3">
              Monitoring only reads.
            </span>
            <span
              className="inline-flex h-8 items-center gap-2 rounded-xl bg-surface-primary-solid px-3 text-button-small text-content-primary-solid"
              style={{
                transform: `scale(${1 - 0.05 * pressAt(t, [PRESS_ADD])})`,
              }}
            >
              {addBusy && <Spinner size="base" color="primary-solid" />}
              Add and monitor
            </span>
          </div>
        </div>

        {/* The coding agent path. */}
        <div
          className="absolute inset-0 flex flex-col"
          style={{
            opacity: phaseB,
            transform: `translateX(${(1 - phaseB) * 16}px)`,
          }}
        >
          <div className="flex items-center gap-2 px-5 pt-4 text-headline-5 text-content-layout-1">
            <Icon
              name="arrow-left"
              label=""
              size="small"
              className="text-content-layout-3"
            />
            Connect with your coding agent
          </div>
          <div className="px-5 pt-3">
            <div className="relative h-16 overflow-hidden rounded-panel border border-border-layout-2 bg-surface-layout-2 px-3 py-2 font-mono text-mono-small text-content-layout-2">
              {PROMPT_TEXT}
              <span className="absolute inset-x-0 bottom-0 h-8 bg-linear-to-t from-surface-layout-2 to-transparent" />
            </div>
            <div className="mt-1.5 text-label-extra-small font-normal text-content-layout-3">
              It has RDST's local address.
            </div>
          </div>
          <div className="mx-5 mt-3 rounded-panel border border-border-layout-2 px-3 py-1.5">
            {STEPS.map((step, index) => (
              <MiniStep
                key={step.label}
                label={step.label}
                index={index}
                t={t}
                at={step.at}
              />
            ))}
          </div>
          <div className="mt-auto flex items-center gap-2 border-t border-border-layout-2 px-5 py-3">
            <span className="flex-1 text-label-extra-small font-normal text-content-layout-3">
              {t > 8.8
                ? 'shop is connected and monitored.'
                : t > 6.7
                  ? 'Your agent is on it.'
                  : 'Waiting for your agent.'}
            </span>
            <span
              className="inline-flex h-8 items-center gap-1.5 rounded-xl bg-surface-primary-solid px-3 text-button-small text-content-primary-solid"
              style={{
                transform: `scale(${1 - 0.05 * pressAt(t, [PRESS_COPY])})`,
              }}
            >
              <Icon name={copied ? 'tick' : 'copy'} label="" size="small" />
              {copied ? 'Copied' : 'Copy prompt'}
            </span>
          </div>
        </div>
      </div>

      <Pointer x={pointer.x} y={pointer.y} press={press} opacity={pointerOn} />
      <StageCaption opacity={hold(t, 0.4, 4.9)} style={{ left: 24, top: 404 }}>
        Paste a connection string
      </StageCaption>
      <StageCaption opacity={hold(t, 5.2, 10.4)} style={{ left: 24, top: 404 }}>
        Or hand it to your coding agent
      </StageCaption>
    </>
  )
}
