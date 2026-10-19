// Helpers for hosted AI without sign-in: the Key Service stub's request log, the account
// states the sign-in prompts follow, the first-run tour, and a scripted MCP client that
// plays the user's coding agent.
import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
} from '@playwright/test'

export const KEYSERVICE_STUB_URL = `http://127.0.0.1:${
  process.env.RDST_E2E_KEYSERVICE_PORT ?? '8786'
}`

export type StubRequest = { method: string; path: string; at: number }

/** Every request RDST's backend made to the Key Service stub, oldest first. */
export async function keyserviceRequests(
  request: APIRequestContext
): Promise<StubRequest[]> {
  const response = await request.get(`${KEYSERVICE_STUB_URL}/__stub/requests`)
  expect(response.ok()).toBe(true)
  return ((await response.json()) as { requests: StubRequest[] }).requests
}

export type SigninPrompt = 'none' | 'soft' | 'budget' | 'required'

/**
 * Answer /api/account/status as an anonymous session in `prompt` state, and report no
 * Anthropic key, so the hosted-AI path (not an own key) decides what the app shows.
 */
export async function mockAnonymousAccess(
  page: Page,
  prompt: SigninPrompt,
  extra: Record<string, unknown> = {}
) {
  await page.route('**/api/account/status', (route) =>
    route.fulfill({
      json: {
        signed_in: false,
        has_session: true,
        is_anonymous: true,
        user_id: 'anon-e2e',
        detail: 'Using Readyset-hosted AI without signing in',
        signin_prompt: prompt,
        signin_reason:
          prompt === 'required' ? 'anonymous_signin_required' : null,
        percent_used: 12,
        days_remaining: 23,
        trial_days: 30,
        limit_microusd: 1_000_000,
        used_microusd: 120_000,
        ...extra,
      },
    })
  )
  await page.route('**/api/env/requirements', (route) =>
    route.fulfill({
      json: {
        keyring_available: false,
        requirements: [
          {
            kind: 'anthropic_api_key',
            satisfied: false,
            source: null,
            selected_provider: null,
            readyset_account_connected: false,
            anthropic_key_configured: false,
            target: null,
            accepted_names: ['ANTHROPIC_API_KEY'],
          },
        ],
      },
    })
  )
}

/** Browser automation skips the first-run tour unless a test opts in, before the app loads. */
export async function optInToOnboardingTour(page: Page) {
  await page.addInitScript(() => {
    ;(
      window as { __RDST_ONBOARDING_TOUR__?: boolean }
    ).__RDST_ONBOARDING_TOUR__ = true
  })
}

export function onboardingTour(page: Page): Locator {
  return page.getByRole('dialog', { name: 'Welcome to RDST' })
}

export const PRIVACY_DOC_URL =
  'https://readyset.io/docs/readyset-ai/rdst/desktop/privacy'
export const PRIVACY_POLICY_URL = 'https://readyset.io/privacy-policy'

type JsonRpcReply = {
  jsonrpc: '2.0'
  id: number
  result?: Record<string, unknown>
  error?: { code: number; message: string }
}

/** A coding agent's side of /mcp: JSON-RPC over POST, with no credentials. */
export class McpClient {
  private nextId = 1

  constructor(
    private readonly request: APIRequestContext,
    private readonly mcpUrl: string
  ) {}

  /** Connect the way the prompt tells an agent to: the MCP URL in its curl example. */
  static fromPrompt(request: APIRequestContext, prompt: string): McpClient {
    const match = prompt.match(/curl -s "([^"]+\/mcp[^"]*)"/)
    expect(match, 'MCP URL in the prompt').toBeTruthy()
    return new McpClient(request, (match as RegExpMatchArray)[1])
  }

  async rpc(method: string, params: Record<string, unknown> = {}) {
    const id = this.nextId++
    const response = await this.request.post(this.mcpUrl, {
      headers: { Accept: 'application/json, text/event-stream' },
      data: { jsonrpc: '2.0', id, method, params },
    })
    expect(response.status(), `${method} answered`).toBe(200)
    const reply = (await response.json()) as JsonRpcReply
    expect(reply.error, `${method} failed`).toBeUndefined()
    return reply.result ?? {}
  }

  async notify(method: string) {
    const response = await this.request.post(this.mcpUrl, {
      data: { jsonrpc: '2.0', method },
    })
    expect(response.status()).toBe(202)
  }

  /** Call a tool and return its JSON payload; fails the test on a tool error. */
  async callTool(name: string, args: Record<string, unknown> = {}) {
    const result = (await this.rpc('tools/call', {
      name,
      arguments: args,
    })) as { content?: { type: string; text: string }[]; isError?: boolean }
    const text =
      result.content?.find((part) => part.type === 'text')?.text ?? ''
    expect(result.isError, `${name}: ${text}`).toBeFalsy()
    return JSON.parse(text) as Record<string, unknown>
  }
}
