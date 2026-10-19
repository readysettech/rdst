// Whether the first-run tour still has to show for this installation, and a way for any
// screen to replay or resume it. Its flags are keyed to the installation, so a new data
// directory shows the tour again; the older unscoped keys are ignored.
import { useSyncExternalStore } from 'react'
import { scopedKey } from '../../lib/installationScope'

const DONE_KEY = 'rdst_onboarding_tour_done'
const OPEN_EVENT = 'rdst_open_onboarding_tour'

function readDone(): boolean {
  try {
    return localStorage.getItem(scopedKey(DONE_KEY)) === '1'
  } catch {
    return false
  }
}

/** The tour was finished or skipped here, or a database already exists. */
export function markTourDone(): void {
  try {
    localStorage.setItem(scopedKey(DONE_KEY), '1')
  } catch {
    /* without storage the tour shows once per visit */
  }
}

/**
 * True while a new install hasn't seen the tour. Browser automation drives the app step
 * by step, so the tour stays out of its way unless a test asks for it.
 */
export function tourPending(): boolean {
  if (typeof window === 'undefined') return false
  const optIn = (window as { __RDST_ONBOARDING_TOUR__?: boolean })
    .__RDST_ONBOARDING_TOUR__
  if (navigator.webdriver && !optIn) return false
  return !readDone()
}

/**
 * Opens the tour over whatever is on screen, at `step` (0-based) or from the start. The
 * resume entry passes `savedTourStep()`.
 */
export function openTour(step?: number): void {
  window.dispatchEvent(
    new CustomEvent<TourRequest>(OPEN_EVENT, { detail: { step: step ?? 0 } })
  )
}

interface TourRequest {
  step: number
}

/** Subscribe to open requests, with the step to open at; returns the unsubscribe. */
export function onOpenTour(listener: (step: number) => void): () => void {
  const handle = (event: Event) =>
    listener((event as CustomEvent<TourRequest>).detail?.step ?? 0)
  window.addEventListener(OPEN_EVENT, handle)
  return () => window.removeEventListener(OPEN_EVENT, handle)
}

const STEP_KEY = 'rdst_onboarding_tour_step'
const STEP_EVENT = 'rdst_onboarding_tour_step_changed'

/** The tour was closed part way, at `step`: the get started screen and Help offer to resume. */
export function saveTourStep(step: number): void {
  try {
    localStorage.setItem(scopedKey(STEP_KEY), String(step))
  } catch {
    /* without storage there's nothing to resume */
  }
  window.dispatchEvent(new Event(STEP_EVENT))
}

/** The tour was finished or skipped: nothing left to resume. */
export function clearTourStep(): void {
  try {
    localStorage.removeItem(scopedKey(STEP_KEY))
  } catch {
    /* nothing stored */
  }
  window.dispatchEvent(new Event(STEP_EVENT))
}

/** Where a tour closed part way left off (0-based), or null. */
export function savedTourStep(): number | null {
  try {
    const raw = localStorage.getItem(scopedKey(STEP_KEY))
    const step = raw == null ? Number.NaN : Number(raw)
    return Number.isInteger(step) && step >= 0 ? step : null
  } catch {
    return null
  }
}

function subscribeTourStep(onChange: () => void): () => void {
  window.addEventListener(STEP_EVENT, onChange)
  window.addEventListener('storage', onChange)
  return () => {
    window.removeEventListener(STEP_EVENT, onChange)
    window.removeEventListener('storage', onChange)
  }
}

/** `savedTourStep()`, kept current as the tour saves and clears it. */
export function useSavedTourStep(): number | null {
  return useSyncExternalStore(subscribeTourStep, savedTourStep, () => null)
}

/** The desktop app's page on what RDST stores, sends and keeps. */
export const PRIVACY_DOC_URL =
  'https://readyset.io/docs/readyset-ai/rdst/desktop/privacy'
export const PRIVACY_POLICY_URL = 'https://readyset.io/privacy-policy'
