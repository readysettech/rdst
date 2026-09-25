// Small hooks shared by the monitor charts: container measurement and cross-chart
// hover sync (`syncId`).
import {
  type RefObject,
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react'

/** Measures an element's content width with ResizeObserver (fallback for tests). */
export function useElementWidth<T extends HTMLElement>(
  fallback = 640
): [RefObject<T | null>, number] {
  const ref = useRef<T | null>(null)
  const [width, setWidth] = useState(0)
  useLayoutEffect(() => {
    const element = ref.current
    if (!element) return
    const measure = () => {
      const next = Math.round(element.getBoundingClientRect().width)
      setWidth(next > 0 ? next : fallback)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [fallback])
  return [ref, width]
}

// Hovered x per syncId. Charts with the same syncId share a crosshair.
const hovered = new Map<string, number | null>()
const listeners = new Map<string, Set<() => void>>()

function setSynced(syncId: string, x: number | null) {
  if (hovered.get(syncId) === x) return
  hovered.set(syncId, x)
  for (const listener of listeners.get(syncId) ?? []) listener()
}

function subscribe(syncId: string, listener: () => void) {
  const set = listeners.get(syncId) ?? new Set()
  set.add(listener)
  listeners.set(syncId, set)
  return () => {
    set.delete(listener)
  }
}

/** Hover x shared across every chart with the same `syncId`; local when absent. */
export function useHoverX(
  syncId?: string
): [number | null, (x: number | null) => void] {
  const [local, setLocal] = useState<number | null>(null)
  const sub = useCallback(
    (listener: () => void) =>
      syncId ? subscribe(syncId, listener) : () => undefined,
    [syncId]
  )
  const synced = useSyncExternalStore(
    sub,
    () => (syncId ? (hovered.get(syncId) ?? null) : null),
    () => null
  )
  const set = useCallback(
    (x: number | null) => (syncId ? setSynced(syncId, x) : setLocal(x)),
    [syncId]
  )
  return [syncId ? synced : local, set]
}

/** True for `ms` after mount; drives one-time entrance animations. */
export function useEntrance(active: boolean, ms = 700): boolean {
  const [entering, setEntering] = useState(true)
  useLayoutEffect(() => {
    if (!active) return
    const timer = setTimeout(() => setEntering(false), ms)
    return () => clearTimeout(timer)
  }, [active, ms])
  return entering
}
