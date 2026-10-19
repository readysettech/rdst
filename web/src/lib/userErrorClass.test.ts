import { describe, expect, it } from 'vitest'
import { userErrorClass } from './userErrorClass'

describe('userErrorClass', () => {
  it.each([
    ['password authentication failed for user "app"', 'auth_failed'],
    ["Access denied for user 'app'@'10.0.0.4'", 'auth_failed'],
    ['could not connect to server: Connection refused', 'host_unreachable'],
    [
      'could not translate host name "db.internal" to address',
      'host_unreachable',
    ],
    ['connection timed out', 'timeout'],
    ['SSL connection has been closed unexpectedly', 'tls'],
    ['database "shop" does not exist', 'db_not_found'],
    ["Unknown database 'shop'", 'db_not_found'],
    ['permission denied for relation pg_stat_statements', 'permission_denied'],
    ['SSH tunnel failed to open', 'ssh_failed'],
    ['something odd', 'unknown'],
  ] as const)('classes "%s" as %s', (message, expected) => {
    expect(userErrorClass(message)).toBe(expected)
  })

  it('reads the code and category before the text, and is unknown with nothing', () => {
    expect(userErrorClass('ssh_failed', null, 'Connection refused')).toBe(
      'ssh_failed'
    )
    expect(userErrorClass(undefined, null)).toBe('unknown')
  })
})
