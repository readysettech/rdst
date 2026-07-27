import { useSyncExternalStore } from 'react'

export type AuditSessionKind = 'snapshot' | 'capture' | 'fleet'

export interface ActiveAuditSession {
  id: number
  kind: AuditSessionKind
  targetLabel: string
  targetNames: string[]
  durationSeconds: number
  startedAt: number
  phase: string
  statusMessage: string
  /** Background run backing this session, once the server has assigned one. */
  runId: string | null
}

export interface CompletedAuditSession {
  id: number
  kind: AuditSessionKind
  targetLabel: string
  runId: string
}

interface AuditPresentation {
  runViewVisible: boolean
  viewRequestId: number
  completed: CompletedAuditSession | null
}

type SessionInput = Omit<ActiveAuditSession, 'id' | 'runId'> & {
  runId?: string | null
  cancel: () => void
}

let active: ActiveAuditSession | null = null
let activeCancel: (() => void) | null = null
let nextId = 1
const listeners = new Set<() => void>()
let presentation: AuditPresentation = {
  runViewVisible: false,
  viewRequestId: 0,
  completed: null,
}

function emit() {
  listeners.forEach((listener) => listener())
}

export function beginAuditSession(input: SessionInput): number | null {
  if (active) return null
  const id = nextId++
  const { cancel, ...session } = input
  active = { runId: null, ...session, id }
  activeCancel = cancel
  presentation = { ...presentation, completed: null }
  emit()
  return id
}

export function completeAuditSession(id: number, runId: string) {
  if (active?.id !== id) return
  presentation = {
    ...presentation,
    completed: {
      id,
      kind: active.kind,
      targetLabel: active.targetLabel,
      runId,
    },
  }
  active = null
  activeCancel = null
  emit()
}

export function updateAuditSession(
  id: number,
  update: Partial<
    Pick<
      ActiveAuditSession,
      'phase' | 'statusMessage' | 'targetLabel' | 'targetNames' | 'runId'
    >
  >
) {
  if (active?.id !== id) return
  active = { ...active, ...update }
  emit()
}

export function finishAuditSession(id: number) {
  if (active?.id !== id) return
  active = null
  activeCancel = null
  emit()
}

export function cancelActiveAudit() {
  activeCancel?.()
}

export function setAuditRunViewVisible(visible: boolean) {
  if (presentation.runViewVisible === visible) return
  presentation = { ...presentation, runViewVisible: visible }
  emit()
}

export function requestAuditRunView() {
  presentation = {
    ...presentation,
    viewRequestId: presentation.viewRequestId + 1,
  }
  emit()
}

export function clearCompletedAudit(id?: number) {
  if (!presentation.completed) return
  if (id !== undefined && presentation.completed.id !== id) return
  presentation = { ...presentation, completed: null }
  emit()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

function getSnapshot() {
  return active
}

export function useAuditSession(): ActiveAuditSession | null {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

function getActiveSnapshot() {
  return active !== null
}

/**
 * Whether a health check is in flight, as a primitive snapshot: subscribers
 * that only need the flag stay put while the session's fields churn.
 */
export function useAuditSessionActive(): boolean {
  return useSyncExternalStore(subscribe, getActiveSnapshot, getActiveSnapshot)
}

export function useAuditPresentation(): AuditPresentation {
  return useSyncExternalStore(
    subscribe,
    () => presentation,
    () => presentation
  )
}

export function getActiveAuditSession(): ActiveAuditSession | null {
  return active
}

export function __resetAuditSessionForTests() {
  activeCancel?.()
  active = null
  activeCancel = null
  presentation = {
    runViewVisible: false,
    viewRequestId: 0,
    completed: null,
  }
  emit()
}
