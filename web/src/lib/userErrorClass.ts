import type { UserErrorClass } from './analytics'

const PATTERNS: [UserErrorClass, RegExp][] = [
  ['ssh_failed', /ssh|tunnel|bastion/i],
  ['tls', /tls|ssl|certificate/i],
  [
    'auth_failed',
    /auth|password|access denied for user|login|credential|role .* does not exist/i,
  ],
  ['permission_denied', /permission|privilege|not allowed|denied/i],
  ['db_not_found', /unknown database|database .* does not exist|db_not_found/i],
  ['timeout', /timed? ?out|timeout/i],
  [
    'host_unreachable',
    /unreachable|refused|could not connect|can't connect|no route|resolve|getaddrinfo|name or service|network|host/i,
  ],
]

/**
 * The fixed class of a connection or setup error, for analytics. Reads the error's code,
 * category and text locally, in that order; only the class leaves the app.
 */
export function userErrorClass(
  ...hints: (string | null | undefined)[]
): UserErrorClass {
  for (const hint of hints) {
    if (!hint) continue
    const found = PATTERNS.find(([, pattern]) => pattern.test(hint))
    if (found) return found[0]
  }
  return 'unknown'
}
