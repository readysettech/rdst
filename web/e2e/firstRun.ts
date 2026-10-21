// First-run helpers: a signed-out install with no Anthropic key, the first-run tour, and
// a scripted MCP client that plays the user's coding agent.
import {
  type APIRequestContext,
  expect,
  type Locator,
  type Page,
} from '@playwright/test'

/**
 * Answer /api/account/status as signed out and report no Anthropic key, so the app shows
 * what a new install sees before signing in.
 */
export async function mockSignedOut(page: Page) {
  await page.route('**/api/account/status', (route) =>
    route.fulfill({
      json: {
        signed_in: false,
        has_session: false,
        detail: 'Not signed in',
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
