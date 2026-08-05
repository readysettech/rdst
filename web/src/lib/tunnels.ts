import type { components } from './api.generated'
import { api } from './client'
import { throwIfNotOk } from './httpError'

export type TunnelStatus = components['schemas']['TunnelStatusResponse']
export type TunnelTestResult = components['schemas']['TestTunnelResponse']

export interface SshAuthOption {
  kind: string
  label: string
  key_path?: string | null
  host?: string | null
  hostname?: string | null
  port?: number | null
  user?: string | null
  outside_ssh_dir?: boolean
}

export interface SshProfile {
  name: string
  host: string
  port: number
  user?: string | null
  key_path?: string | null
}

export interface SshBrowserEntry {
  name: string
  path: string
  is_dir: boolean
}

export interface SshBrowserDirectory {
  path: string
  parent?: string | null
  entries: SshBrowserEntry[]
}

export async function fetchTunnelStatuses(): Promise<TunnelStatus[]> {
  const { data, response } = await api.GET('/api/tunnel/status')
  await throwIfNotOk(response, 'Failed to fetch SSH tunnel status')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function testTunnel(target: string): Promise<TunnelTestResult> {
  const { data, response } = await api.POST('/api/tunnel/test', {
    body: { target },
  })
  await throwIfNotOk(response, 'Failed to test SSH tunnel')
  if (!data) throw new Error('Missing response body')
  return data
}

export async function fetchSshKeys(jumpHost = ''): Promise<SshAuthOption[]> {
  const query = jumpHost ? `?jump_host=${encodeURIComponent(jumpHost)}` : ''
  const response = await fetch(`/api/tunnel/ssh-keys${query}`)
  await throwIfNotOk(response, 'Failed to discover local SSH keys')
  const body = (await response.json()) as { options: SshAuthOption[] }
  return body.options
}

export async function fetchSshProfiles(): Promise<SshProfile[]> {
  const response = await fetch('/api/tunnel/ssh-profiles')
  await throwIfNotOk(response, 'Failed to load saved jump hosts')
  return (await response.json()) as SshProfile[]
}

export async function fetchSshDirectory(
  path?: string
): Promise<SshBrowserDirectory> {
  const query = path ? `?path=${encodeURIComponent(path)}` : ''
  const response = await fetch(`/api/tunnel/browse${query}`)
  await throwIfNotOk(response, 'Failed to browse local files')
  return (await response.json()) as SshBrowserDirectory
}

export async function importSshKey(sourcePath: string): Promise<string> {
  const response = await fetch('/api/tunnel/ssh-keys/import', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ source_path: sourcePath }),
  })
  await throwIfNotOk(response, 'Failed to copy SSH key')
  const body = (await response.json()) as { key_path: string }
  return body.key_path
}
