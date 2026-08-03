import type { components } from './api.generated'
import { api } from './client'
import { throwIfApiError } from './httpError'

export type AllowlistContext = components['schemas']['AllowlistContextResponse']
export type AllowlistAddResponse = components['schemas']['AllowlistAddResponse']

export async function fetchAllowlistContext(
  target: string
): Promise<AllowlistContext> {
  const { data, error, response } = await api.GET('/api/allowlist/context', {
    params: { query: { target } },
  })
  throwIfApiError(response, error, 'Failed to load provider allowlist guidance')
  if (!data) throw new Error('Missing allowlist context response')
  return data
}

export async function addCurrentIpToAllowlist(
  target: string,
  expectedIp: string
): Promise<AllowlistAddResponse> {
  const { data, error, response } = await api.POST('/api/allowlist/add', {
    body: { target, expected_ip: expectedIp },
  })
  throwIfApiError(response, error, 'Failed to update the provider allowlist')
  if (!data) throw new Error('Missing allowlist update response')
  return data
}
