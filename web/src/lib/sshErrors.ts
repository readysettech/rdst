export const SSH_ERROR_CATEGORIES = [
  'ssh_key_missing',
  'ssh_passphrase_required',
  'ssh_auth_failed',
  'ssh_jump_unreachable',
  'ssh_tunnel_error',
] as const

export type SshErrorCategory = (typeof SSH_ERROR_CATEGORIES)[number]

export function isSshErrorCategory(
  category?: string | null
): category is SshErrorCategory {
  return SSH_ERROR_CATEGORIES.includes(category as SshErrorCategory)
}

function firstSentence(message?: string | null): string {
  return message?.split('\n')[0]?.trim() ?? ''
}

export function sshErrorCopy({
  category,
  message,
  target,
}: {
  category?: string | null
  message?: string | null
  target?: string
}): string {
  const detail = firstSentence(message)
  switch (category) {
    case 'ssh_key_missing':
      return detail || 'SSH key not found. Choose an existing private key.'
    case 'ssh_passphrase_required':
      return `Unlock via CLI: rdst tunnel test ${target || '<target>'}.`
    case 'ssh_auth_failed':
      return 'SSH authentication failed. Check the SSH user and authorized key.'
    case 'ssh_jump_unreachable':
      return 'SSH jump host is unreachable. Check its host, port, VPN, and firewall.'
    case 'ssh_tunnel_error':
      return detail || 'SSH tunnel setup failed. Check the jump-host settings.'
    default:
      return detail || 'The connection failed.'
  }
}

export function isPrivateConnectivityFailure(result?: {
  category?: string | null
  code?: string | null
  error?: string | null
}): boolean {
  if (!result) return false
  const category = result.category ?? result.code
  if (isSshErrorCategory(category)) return true
  if (
    category &&
    /network|unreachable|timeout|timed_out|refused/i.test(category)
  ) {
    return true
  }
  return /network is unreachable|connection (?:timed out|refused)|could not connect|no route to host/i.test(
    result.error ?? ''
  )
}
