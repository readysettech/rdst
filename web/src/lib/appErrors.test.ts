import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  AppErrorGate,
  installAppErrorCapture,
  reportAppError,
  scrubMessage,
  scrubEventProperties,
} from './appErrors'

describe('scrubMessage', () => {
  it('removes SQL, hosts, addresses, paths, emails and quoted values', () => {
    const text = scrubMessage(
      'connect to db.prod.example.com (10.1.2.3) as "admin" for bob@corp.com failed at /home/bob/.rdst/x.db'
    )
    expect(text).not.toMatch(
      /example\.com|10\.1\.2\.3|admin|bob@corp|\/home\/bob/
    )
    expect(
      scrubMessage(
        'connect to 2001:db8::1 hostaddr=db.example.com sslrootcert=/etc/certs/root.pem'
      )
    ).not.toMatch(/2001:db8|db.example|\/etc\/certs/)
    expect(scrubMessage('near SELECT id FROM orders WHERE id = 1')).toBe(
      'near [sql]'
    )
    expect(
      scrubMessage("Cannot read properties of undefined (reading 'map')")
    ).toBe('Cannot read properties of undefined (reading [value])')
  })
})

describe('AppErrorGate', () => {
  it('reports each distinct error once an hour and at most a few a minute', () => {
    const gate = new AppErrorGate(2, 3_600_000)
    expect(gate.admit('a', 0)).toBe(0)
    expect(gate.admit('a', 1_000)).toBeNull()
    expect(gate.admit('b', 2_000)).toBe(0)
    expect(gate.admit('c', 3_000)).toBeNull()
    expect(gate.admit('c', 61_000)).toBe(1)
    expect(gate.admit('a', 3_600_001)).toBe(1)
  })
})

describe('installAppErrorCapture', () => {
  let uninstall: (() => void) | null = null
  afterEach(() => uninstall?.())

  it('sends uncaught errors and rejections with scrubbed messages', () => {
    const sink = vi.fn()
    uninstall = installAppErrorCapture(sink)
    window.dispatchEvent(
      new ErrorEvent('error', {
        error: new TypeError('bad host db.example.com'),
        message: 'x',
      })
    )
    const rejection = new Event('unhandledrejection') as PromiseRejectionEvent
    Object.assign(rejection, { reason: new Error('SELECT secret FROM t') })
    window.dispatchEvent(rejection)

    expect(sink).toHaveBeenCalledWith({
      source: 'web',
      component: 'window',
      error_class: 'TypeError',
      message: 'bad host [host]',
      handled: false,
      suppressed_repeats: 0,
    })
    expect(sink).toHaveBeenCalledWith(
      expect.objectContaining({
        component: 'promise',
        error_class: 'Error',
        message: '[sql]',
      })
    )
  })

  it('is silent until installed', () => {
    const sink = vi.fn()
    reportAppError('error_boundary', new Error('x'))
    expect(sink).not.toHaveBeenCalled()
  })
})

describe('automatic event privacy', () => {
  it('scrubs URLs and nested errors while keeping email on person profiles', () => {
    const props = scrubEventProperties({
      distinct_id: 'usr_42',
      app_version: '1.2.3',
      email: 'person@example.com',
      $current_url: 'http://localhost:8797/database/orders',
      sql: 'SELECT card_number FROM payments',
      $exception_list: [
        {
          message:
            'host=db.example.com at 2001:db8::1 SELECT card_number FROM payments',
        },
      ],
      $set: { email: 'person@example.com', account_kind: 'signed_in' },
    })
    expect(props.distinct_id).toBe('usr_42')
    expect(props.app_version).toBe('1.2.3')
    expect(props.$set).toEqual({
      email: 'person@example.com',
      account_kind: 'signed_in',
    })
    const { $set: _profile, ...event } = props
    expect(JSON.stringify(event)).not.toMatch(
      /person@|localhost|db.example|2001:db8|SELECT|card_number/
    )
    expect(event).not.toHaveProperty('sql')
  })
})
