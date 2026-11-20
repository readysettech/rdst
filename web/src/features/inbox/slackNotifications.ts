// Inbox pushes to Slack through the user's own incoming webhook. The backend keeps the URL
// in its secret store (or RDST_SLACK_WEBHOOK_URL) and never sends it back; this only says
// whether one is set and how the last delivery went.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

const SETTINGS_URL = '/api/settings/slack'
const KEY = ['settings', 'slack'] as const

export interface SlackSettings {
  configured: boolean
  /** 'env' when the server's environment sets it; Settings can't change it then. */
  source?: 'env' | 'settings' | null
  links_to_ui?: boolean
  last_sent_at?: number | null
  last_error?: string | null
  last_error_at?: number | null
}

async function detail(response: Response, fallback: string): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown }
    if (typeof body.detail === 'string') return body.detail
  } catch {
    /* not JSON */
  }
  return fallback
}

async function request(
  method: string,
  path: string,
  body?: unknown
): Promise<Response> {
  const response = await fetch(path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok)
    throw new Error(await detail(response, "RDST couldn't save that."))
  return response
}

export function useSlackSettings() {
  return useQuery({
    queryKey: KEY,
    queryFn: async (): Promise<SlackSettings> =>
      (await request('GET', SETTINGS_URL)).json(),
    retry: false,
  })
}

export function useSaveSlackWebhook() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (webhookUrl: string | null): Promise<SlackSettings> =>
      webhookUrl === null
        ? (await request('DELETE', SETTINGS_URL)).json()
        : (
            await request('PUT', SETTINGS_URL, { webhook_url: webhookUrl })
          ).json(),
    onSuccess: (data) => client.setQueryData(KEY, data),
  })
}

/** Sends a test message; resolves to why it failed, or null when Slack took it. */
export async function sendSlackTest(
  webhookUrl?: string
): Promise<string | null> {
  try {
    const response = await request('POST', `${SETTINGS_URL}/test`, {
      webhook_url: webhookUrl || null,
    })
    const result = (await response.json()) as { ok: boolean; error?: string }
    return result.ok ? null : (result.error ?? "Slack didn't take the message.")
  } catch (error) {
    return error instanceof Error ? error.message : String(error)
  }
}
