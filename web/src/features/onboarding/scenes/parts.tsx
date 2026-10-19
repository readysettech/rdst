// Miniatures of the real app for the tour's scenes: the window, the sidebar with its
// databases and badges, and a pointer. Everything sits on a fixed 600 x 440 stage that
// the tour scales to fit, so positions here are stage pixels.
import { cn } from '@rs/tailwind-base'
import { Icon, type IconStrokeName } from '@rs/ui-new/icon'
import type { CSSProperties, ReactNode } from 'react'
import { Radar, type RadarState } from '../../monitor-kit/Radar'

export const STAGE_W = 600
export const STAGE_H = 440

export function Window({
  children,
  style,
  className,
}: {
  children: ReactNode
  style?: CSSProperties
  className?: string
}) {
  return (
    <div
      className={cn(
        'absolute overflow-hidden rounded-panel border border-border-layout-2 bg-surface-layout-2 shadow-elevation-3',
        className
      )}
      style={style}
    >
      {children}
    </div>
  )
}

const NAV: { label: string; icon: IconStrokeName }[] = [
  { label: 'Home', icon: 'dashboard' },
  { label: 'Inbox', icon: 'notification' },
  { label: 'Actions', icon: 'tick-double' },
  { label: 'Ask', icon: 'message-multiple' },
]

export interface MiniDatabase {
  name: string
  state: RadarState
  /** 0..1: how far the row has popped in. */
  appear?: number
  toDo?: string
}

/** The app's sidebar, at stage scale: nav with badges, then the databases. */
export function MiniSidebar({
  active,
  inbox = 0,
  inboxPop = 1,
  databases = [],
  selected,
}: {
  active: string
  inbox?: number
  /** 0..1 scale of the inbox badge as it lands. */
  inboxPop?: number
  databases?: MiniDatabase[]
  selected?: string
}) {
  return (
    <div className="flex h-full w-36 shrink-0 flex-col gap-0.5 bg-surface-layout-2 px-2 pt-3">
      {NAV.map((item) => (
        <div
          key={item.label}
          className={cn(
            'flex h-7 items-center gap-2 rounded-control px-2 text-caption',
            item.label === active
              ? 'bg-surface-layout-1 text-content-layout-1'
              : 'text-content-layout-2'
          )}
        >
          <Icon name={item.icon} label="" size="small" />
          {item.label}
          {item.label === 'Inbox' && inbox > 0 && (
            <span
              className="ml-auto inline-flex h-4 min-w-4 items-center justify-center rounded-pill bg-surface-negative-solid px-1 text-label-extra-small tabular-nums text-(--neutral-white)"
              style={{ transform: `scale(${inboxPop})` }}
            >
              {inbox}
            </span>
          )}
        </div>
      ))}
      <div className="mt-3 flex h-6 items-center px-2 text-label-extra-small font-normal text-content-layout-3">
        Databases
        <Icon name="add" label="" size="small" className="ml-auto" />
      </div>
      {databases.map((db) => {
        const p = db.appear ?? 1
        return (
          <div
            key={db.name}
            className={cn(
              'flex h-7 items-center gap-2 rounded-control px-2 text-caption text-content-layout-1',
              db.name === selected && 'bg-surface-layout-1'
            )}
            style={{
              opacity: p,
              transform: `translateX(${(1 - p) * -10}px)`,
            }}
          >
            <Radar state={db.state} name={db.name} tooltip={false} />
            {db.name}
            {db.toDo && (
              <span className="ml-auto text-label-extra-small font-normal text-content-layout-3">
                {db.toDo}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}

/** The pointer, pressing down while `press` is above 0. */
export function Pointer({
  x,
  y,
  press = 0,
  opacity = 1,
}: {
  x: number
  y: number
  press?: number
  opacity?: number
}) {
  return (
    <svg
      aria-hidden="true"
      width={20}
      height={22}
      viewBox="0 0 20 22"
      className="pointer-events-none absolute z-10 fill-content-layout-1 stroke-surface-layout-2 drop-shadow-md"
      style={{
        left: x,
        top: y,
        opacity,
        transform: `scale(${1 - 0.14 * press})`,
        transformOrigin: '3px 2px',
      }}
    >
      <path
        d="M3 2l13 11.2-6.1.6 3.6 6.6-2.6 1.4-3.6-6.7L3 19.4z"
        strokeWidth={1.4}
        strokeLinejoin="round"
      />
    </svg>
  )
}

/** A short label pinned to the stage that names what's happening. */
export function StageCaption({
  children,
  opacity,
  style,
}: {
  children: ReactNode
  opacity: number
  style?: CSSProperties
}) {
  return (
    <div
      className="absolute z-20 rounded-pill border border-border-layout-2 bg-surface-raised px-3 py-1 text-label-extra-small text-content-layout-1 shadow-elevation-2"
      style={{
        opacity,
        transform: `translateY(${(1 - opacity) * 6}px)`,
        ...style,
      }}
    >
      {children}
    </div>
  )
}

/** Where the pointer is at `t`, gliding between timed waypoints. */
export function pointerAt(
  t: number,
  points: { t: number; x: number; y: number }[]
): { x: number; y: number } {
  if (t <= points[0].t) return points[0]
  for (let i = 1; i < points.length; i += 1) {
    const to = points[i]
    if (t <= to.t) {
      const from = points[i - 1]
      const raw = (t - from.t) / (to.t - from.t)
      const k = raw < 0.5 ? 4 * raw ** 3 : 1 - (-2 * raw + 2) ** 3 / 2
      return {
        x: from.x + (to.x - from.x) * k,
        y: from.y + (to.y - from.y) * k,
      }
    }
  }
  return points[points.length - 1]
}

/** 1 for a beat around each moment in `at`, easing in and out: a press. */
export function pressAt(t: number, at: number[]) {
  return Math.max(
    0,
    ...at.map((moment) => {
      const d = Math.abs(t - moment)
      return d < 0.16 ? Math.cos((d / 0.16) * (Math.PI / 2)) : 0
    })
  )
}
