import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  __resetAuditSessionForTests,
  beginAuditSession,
  cancelActiveAudit,
  finishActiveAuditSession,
  getActiveAuditSession,
} from './auditSession'

function begin(cancel: () => void) {
  return beginAuditSession({
    kind: 'capture',
    targetLabel: 'prod',
    targetNames: ['prod'],
    durationSeconds: 60,
    startedAt: 0,
    phase: 'config',
    statusMessage: 'Starting capture...',
    cancel,
  })
}

afterEach(() => {
  __resetAuditSessionForTests()
})

describe('auditSession', () => {
  it('holds one session at a time and routes cancel to its owner', () => {
    const cancel = vi.fn()
    expect(begin(cancel)).toBe(1)
    expect(begin(vi.fn())).toBeNull()

    cancelActiveAudit()

    expect(cancel).toHaveBeenCalledTimes(1)
  })

  it('clears the active session without knowing its id', () => {
    begin(vi.fn())

    finishActiveAuditSession()

    expect(getActiveAuditSession()).toBeNull()
    // Idempotent: a cancel that arrives after the follower already settled the
    // session must not leave the launcher blocked.
    finishActiveAuditSession()
    expect(getActiveAuditSession()).toBeNull()
  })
})
