import {
  act,
  cleanup,
  fireEvent,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { selectGlobalTarget } from '../../features/database/targetSelection'
import { addFlowFor, endAddFlow, newAddAttempt } from '../../lib/addFlow'
import { trackEvent } from '../../lib/analytics'
import { useConfigure } from '../../lib/useConfigure'
import { useSystemStatus } from '../../lib/useSystemStatus'
import { AddDatabaseDialog } from './AddDatabaseDialog'
import { AgentConnect } from './AgentConnect'
import { forgetLastImport } from './FindDatabases'

const navigate = vi.fn()
vi.mock('@tanstack/react-router', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@tanstack/react-router')>()),
  useNavigate: () => navigate,
}))
vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))
vi.mock('../../lib/backgroundRuns', () => ({ startBootstrapRun: vi.fn() }))
vi.mock('../../features/database/targetSelection', () => ({
  selectGlobalTarget: vi.fn(),
}))
vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEvent: vi.fn(),
}))

/** The progress stream, driven by the test. */
class FakeEventSource {
  static last: FakeEventSource | null = null
  url: string
  listeners = new Map<string, (event: MessageEvent<string>) => void>()
  onerror: (() => void) | null = null
  closed = false
  constructor(url: string) {
    this.url = url
    FakeEventSource.last = this
  }
  addEventListener(
    name: string,
    listener: (event: MessageEvent<string>) => void
  ) {
    this.listeners.set(name, listener)
  }
  close() {
    this.closed = true
  }
  emit(payload: object) {
    act(() => {
      this.listeners.get('message')?.({
        data: JSON.stringify(payload),
      } as MessageEvent<string>)
    })
  }
}

const PROMPT = 'RDST is a local database monitoring app from Readyset.'
const writeText = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  forgetLastImport()
  FakeEventSource.last = null
  vi.stubGlobal('EventSource', FakeEventSource)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(
      async () =>
        new Response(
          JSON.stringify({
            prompt: PROMPT,
            issued_at: 1700000000,
          }),
          { headers: { 'content-type': 'application/json' } }
        )
    )
  )
  writeText.mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
  vi.mocked(useSystemStatus).mockReturnValue({
    data: { targets: [] },
  } as never)
  vi.mocked(useConfigure).mockReturnValue({} as never)
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

async function renderAgent() {
  const onAddByHand = vi.fn()
  renderWithClient(<AgentConnect onAddByHand={onAddByHand} />)
  fireEvent.click(await screen.findByTestId('agent-prompt-reveal'))
  expect(await screen.findByText(PROMPT)).toBeTruthy()
  await waitFor(() => expect(FakeEventSource.last).not.toBeNull())
  return { onAddByHand, stream: FakeEventSource.last as FakeEventSource }
}

const status = () => screen.getByTestId('agent-status')

describe('AgentConnect', () => {
  it('shows the prompt as a short card and copies it', async () => {
    await renderAgent()
    expect(screen.getByText('Prompt for your agent')).toBeTruthy()
    // A summary of what the agent does, and no second button for the prompt on show.
    expect(screen.getByTestId('agent-prompt-summary').textContent).toMatch(
      /finds the database this project uses, adds it to RDST/
    )
    expect(screen.queryByTestId('agent-prompt-reveal')).toBeNull()
    expect(screen.queryByTestId('agent-prompt-caption')).toBeNull()
    expect(screen.queryByText(/minute|expire|once/i)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Show full prompt' }))
    expect(
      screen.getByRole('button', { name: 'Hide full prompt' })
    ).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-copy-prompt'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PROMPT))
    expect(await screen.findByText('Copied')).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith('agent_prompt_shown', {
      attempt_id: null,
    })
  })

  it('names the attempt by a hash of its nonce, never the nonce', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        async () =>
          new Response(
            JSON.stringify({
              prompt: PROMPT,
              attempt: '0123abcd0123abcd',
              issued_at: 1700000000,
            }),
            { headers: { 'content-type': 'application/json' } }
          )
      )
    )
    await renderAgent()
    await waitFor(() =>
      expect(trackEvent).toHaveBeenCalledWith('agent_prompt_shown', {
        attempt_id: expect.stringMatching(/^[0-9a-f]{16}$/),
      })
    )
    expect(JSON.stringify(vi.mocked(trackEvent).mock.calls)).not.toContain(
      '0123abcd0123abcd'
    )
  })

  it('follows the agent until it adds the database, then opens its turn-on dialog', async () => {
    const { stream } = await renderAgent()
    expect(stream.url).toBe('/api/agent-connect/events?since=1700000000')
    expect(status().getAttribute('data-state')).toBe('waiting')
    expect(screen.getByText('Waiting for your agent')).toBeTruthy()

    stream.emit({ step: 'connected' })
    expect(status().getAttribute('data-state')).toBe('connected')
    expect(screen.getByText('Your agent connected')).toBeTruthy()
    expect(screen.getByText('Your agent connected.')).toBeTruthy()

    stream.emit({ step: 'database_added', target: 'shop' })
    expect(status().getAttribute('data-state')).toBe('added')
    expect(screen.getByText('Added shop')).toBeTruthy()
    await waitFor(
      () =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/database',
          search: { target: 'shop', panel: 'enable' },
        }),
      { timeout: 3000 }
    )
    expect(selectGlobalTarget).toHaveBeenCalledWith('shop')
    expect(addFlowFor('shop')).toMatchObject({
      via: 'agent',
      agentSince: 1700000000,
    })
    endAddFlow('shop')
  })

  it("opens the database's page without the dialog once monitoring is already on", async () => {
    const { stream } = await renderAgent()
    stream.emit({ step: 'monitoring_enabled', target: 'shop' })
    await waitFor(
      () =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/database',
          search: { target: 'shop' },
        }),
      { timeout: 3000 }
    )
    endAddFlow('shop')
  })

  it("shows the agent's error with a way to connect manually", async () => {
    const { stream, onAddByHand } = await renderAgent()
    stream.emit({ step: 'connected' })
    stream.emit({ step: 'error', message: "Couldn't reach the database." })
    expect(status().getAttribute('data-state')).toBe('error')
    expect(await screen.findByText("Couldn't reach the database.")).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-connect-manually'))
    expect(onAddByHand).toHaveBeenCalled()
  })

  it('reports where the attempt stopped when the dialog closes', async () => {
    const attempt = newAddAttempt()
    renderWithClient(<AgentConnect onAddByHand={vi.fn()} attempt={attempt} />)
    fireEvent.click(await screen.findByTestId('agent-prompt-reveal'))
    expect(await screen.findByText(PROMPT)).toBeTruthy()
    await waitFor(() => expect(FakeEventSource.last).not.toBeNull())
    FakeEventSource.last?.emit({ step: 'connected' })
    cleanup()
    expect(trackEvent).toHaveBeenCalledWith('database_add_abandoned', {
      last_stage: 'paired',
      via: 'agent',
      seconds_open: expect.any(Number),
    })
  })

  it('never dead-ends when RDST has no prompt to give', async () => {
    let failed = false
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (url: string) => {
        if (url.endsWith('/prompt') && !failed) {
          failed = true
          throw new TypeError('Failed to fetch')
        }
        return new Response(
          JSON.stringify({ prompt: PROMPT, issued_at: 1700000000 }),
          { headers: { 'content-type': 'application/json' } }
        )
      })
    )
    const onAddByHand = vi.fn()
    renderWithClient(<AgentConnect onAddByHand={onAddByHand} />)
    expect(
      await screen.findByText("Couldn't get a prompt from RDST")
    ).toBeTruthy()
    expect(screen.getByText(/RDST didn't answer/)).toBeTruthy()
    expect(screen.queryByText(/Failed to fetch/)).toBeNull()

    fireEvent.click(screen.getByTestId('agent-connect-url-instead'))
    expect(onAddByHand).toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    fireEvent.click(await screen.findByTestId('agent-prompt-reveal'))
    expect(await screen.findByText(PROMPT)).toBeTruthy()
  })

  it('names a server without agent connect', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(new Response('{}', { status: 404 }))
    )
    renderWithClient(<AgentConnect onAddByHand={vi.fn()} />)
    expect(
      await screen.findByText(/can't connect through an agent yet/)
    ).toBeTruthy()
  })

  it('leaves for the form at any stage', async () => {
    const onAddByHand = vi.fn()
    const { unmount } = renderWithClient(
      <AgentConnect onAddByHand={onAddByHand} />
    )
    fireEvent.click(await screen.findByTestId('agent-prompt-reveal'))
    expect(await screen.findByText(PROMPT)).toBeTruthy()
    fireEvent.click(screen.getByTestId('agent-connect-manually'))
    expect(onAddByHand).toHaveBeenCalled()
    unmount()
    const prompts = vi
      .mocked(fetch)
      .mock.calls.filter(([url]) => String(url).endsWith('/prompt'))
    expect(prompts).toHaveLength(1)
  })

  it('links how data is handled, and nothing more', async () => {
    await renderAgent()
    expect(
      screen.getByRole('link', { name: 'How your data is handled' })
    ).toBeTruthy()
    expect(screen.queryByText(/keyring/i)).toBeNull()
  })
})

describe('AddDatabaseDialog agent path', () => {
  it('opens from the form and goes back to it', async () => {
    renderWithClient(<AddDatabaseDialog request="form" onClose={vi.fn()} />)
    fireEvent.click(screen.getByTestId('agent-connect-option'))
    expect(
      await screen.findByRole('heading', {
        name: 'Find your database',
      })
    ).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith('database_add_path_selected', {
      via: 'agent',
    })
    // The find view is the way in: the form is "Connect manually instead".
    expect(screen.queryByRole('button', { name: 'Back' })).toBeNull()
    fireEvent.click(screen.getByTestId('agent-connect-manually'))
  })

  it('opens straight on the agent path from ?add=agent', async () => {
    renderWithClient(<AddDatabaseDialog request="agent" onClose={vi.fn()} />)
    expect(await screen.findByTestId('find-databases')).toBeTruthy()
  })
})

const AGENT = {
  key: 'claude:wsl',
  id: 'claude',
  side: 'wsl',
  label: 'Claude Code in WSL',
  name: 'Claude',
}

const RUN_BASE = {
  message: null,
  steps: 0,
  progress: [],
  elapsed: 3,
  activity: [],
  target: null,
  waiting: false,
  monitoring: false,
  resume_command: null,
  continue_in: null,
  methods: null,
}

const found = (over: object) => ({
  engine: 'postgresql',
  user: null,
  source: null,
  env_file: null,
  env_var: null,
  password_file: null,
  password_var: null,
  note: null,
  project: null,
  found_by: [],
  environment: 'development',
  ...over,
})

const FOUND = [
  found({
    id: '0',
    name: 'shop',
    host: 'localhost',
    port: 25440,
    database: 'shop',
    provider: 'docker',
    project: '/home/me/projects/shop',
    env_file: '/home/me/projects/shop/.env',
    env_var: 'DATABASE_URL',
    found_by: ['DATABASE_URL in .env', 'Docker container shop-db'],
  }),
  found({
    id: '1',
    name: 'billing',
    host: 'db.abcdefghijabcdefghij.supabase.co',
    port: 5432,
    database: 'postgres',
    provider: 'supabase',
    environment: 'production',
    found_by: ['SUPABASE_URL in .env'],
  }),
  found({
    id: '2',
    name: 'postgres-5433',
    host: 'localhost',
    port: 5433,
    database: null,
    provider: 'local',
    found_by: ['PostgreSQL answering on port 5433'],
  }),
]

const SCAN = {
  ...RUN_BASE,
  id: 'scan-1',
  agent: null,
  kind: 'scan',
  state: 'done',
  candidates: FOUND,
  methods: [
    {
      method: 'projects',
      label: 'Your recent projects',
      state: 'done',
      found: 2,
      looked: ['/home/me/projects/shop'],
      seconds: 0.3,
    },
    {
      method: 'docker',
      label: 'Docker containers',
      state: 'done',
      found: 1,
      looked: ['Docker on this computer'],
      seconds: 0.1,
    },
  ],
}

const SEARCH = {
  ...RUN_BASE,
  id: 'run1',
  agent: AGENT,
  kind: 'search',
  state: 'running',
  candidates: [],
  activity: [{ kind: 'say', text: 'Reading shop.' }],
}

const SETUP = {
  ...RUN_BASE,
  id: 'setup1',
  agent: AGENT,
  kind: 'setup',
  state: 'running',
  candidates: [],
  activity: [{ kind: 'say', text: 'Finding the port Docker publishes.' }],
}

/** fetch answered by path: the prompt, the agents, a scan, a search and a setup. */
// shop without its password on disk: the agent sets it up.
const NO_SECRET = [
  { ...FOUND[0], env_file: null, env_var: null },
  FOUND[1],
  FOUND[2],
]

function routeFetch(
  agents: object[],
  apps: string[] = [],
  candidates: object[] = FOUND
) {
  let polls = 0
  let setupPolls = 0
  const calls: [string, RequestInit | undefined][] = []
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string, init?: RequestInit) => {
      calls.push([url, init])
      const json = (body: object, status = 200) =>
        new Response(JSON.stringify(body), {
          status,
          headers: { 'content-type': 'application/json' },
        })
      if (url.endsWith('/agents')) return json({ agents, apps })
      if (url.endsWith('/scan'))
        return json({
          ...SCAN,
          state: 'running',
          candidates: [],
          methods: [
            SCAN.methods[0],
            { ...SCAN.methods[1], state: 'running', found: 0 },
          ],
        })
      if (url.includes('/discover/scan-1') && !url.endsWith('/add'))
        if (!url.endsWith('/setup')) return json({ ...SCAN, candidates })
      if (url.endsWith('/open-app')) return json({ opened: true })
      if (url.endsWith('/discover')) return json(SEARCH)
      if (url.endsWith('/discover/scan-1/add'))
        return json({
          target: 'shop',
          persisted: true,
          ok: true,
          message: null,
        })
      if (url.endsWith('/discover/scan-1/setup')) return json(SETUP)
      if (url.includes('/discover/setup1')) {
        setupPolls += 1
        return json(
          setupPolls < 2
            ? { ...SETUP, target: 'shop' }
            : { ...SETUP, target: 'shop', waiting: true }
        )
      }
      if (url.includes('/discover/run1')) {
        polls += 1
        return json(
          polls < 2
            ? SEARCH
            : {
                ...SEARCH,
                state: 'done',
                candidates: [
                  found({
                    id: '0',
                    name: 'reports',
                    host: 'localhost',
                    port: 15433,
                    database: 'shopdb',
                    provider: 'local',
                  }),
                ],
              }
        )
      }
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
  )
  return calls
}

describe('AgentConnect finding databases', () => {
  const renderFull = () => {
    const onSignIn = vi.fn()
    const onFillIn = vi.fn()
    renderWithClient(
      <AgentConnect
        onAddByHand={vi.fn()}
        onSignIn={onSignIn}
        onFillIn={onFillIn}
      />
    )
    return { onSignIn, onFillIn }
  }

  // Looks, and waits for the best match to come picked.
  const look = async () => {
    fireEvent.click(await screen.findByTestId('scan-computer'))
    const rows = await screen.findAllByTestId('found-database')
    await waitFor(() =>
      expect(
        rows.some(
          (row) => (row.querySelector('input') as HTMLInputElement).checked
        )
      ).toBe(true)
    )
    return rows
  }

  it('imports local databases on a click, and connects one manually with what it found', async () => {
    const calls = routeFetch([])
    const { onSignIn, onFillIn } = renderFull()
    fireEvent.click(await screen.findByTestId('sign-in-neon'))
    expect(onSignIn).toHaveBeenCalledWith('neon')
    // Nothing is read until the user asks.
    expect(calls.some(([url]) => url.endsWith('/scan'))).toBe(false)
    const rows = await look()
    expect(screen.getByTestId('find-databases-title').textContent).toBe(
      'Found three databases. Pick one to set up.'
    )
    expect(rows[0].textContent).toContain('PostgreSQL · localhost:25440/shop')
    // Grouped by project: the project heads its databases.
    expect(screen.getAllByTestId('found-group')[0].textContent).toContain(
      '~/projects/shop'
    )
    expect(rows[0].textContent).toContain(
      'DATABASE_URL in .env · Docker container shop-db'
    )
    expect((rows[0].querySelector('input') as HTMLInputElement).checked).toBe(
      true
    )
    // Its URL is on disk: Connect is the one way on.
    expect(screen.getByTestId('picked-connect')).toBeTruthy()
    expect(screen.queryByTestId('agent-setup')).toBeNull()
    expect(screen.queryByTestId('picked-manual')).toBeNull()
    // No password and no agent: connecting manually is the way on.
    fireEvent.click(rows[2])
    fireEvent.click(screen.getByTestId('picked-manual'))
    expect(onFillIn).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'postgres-5433', port: 5433 })
    )
  })

  it('connects a found database whose password is on disk, with no agent', async () => {
    const calls = routeFetch([])
    renderFull()
    const rows = await look()
    expect((rows[0].querySelector('input') as HTMLInputElement).checked).toBe(
      true
    )
    fireEvent.click(screen.getByTestId('picked-connect'))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/database',
        search: { target: 'shop', panel: 'enable' },
      })
    )
    const add = calls.find(([url]) => url.endsWith('/add'))
    expect(JSON.parse(String(add?.[1]?.body))).toEqual({ candidate: '0' })
    expect(trackEvent).toHaveBeenCalledWith('find_action', {
      action: 'add_self',
      source: 'scan',
      provider: 'docker',
      ok: true,
    })
  })

  it("stays on the list when what RDST found doesn't connect, with the fixes there", async () => {
    const calls = routeFetch([AGENT])
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      calls.push([String(url), init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/discover/scan-1/add'))
        return json({
          target: null,
          added: false,
          ok: false,
          message: 'password authentication failed',
        })
      if (path.endsWith('/discover/scan-1/setup')) return json(SETUP)
      if (path.endsWith('/scan') || path.includes('/discover/scan-1'))
        return json(SCAN)
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    await look()
    fireEvent.click(screen.getByTestId('picked-connect'))
    const failed = await screen.findByTestId('picked-failed')
    expect(failed.textContent).toContain(
      "RDST couldn't connect to shop with what it found."
    )
    expect(failed.textContent).toContain('password authentication failed')
    expect(navigate).not.toHaveBeenCalled()
    fireEvent.click(screen.getByTestId('picked-fix'))
    await waitFor(() =>
      expect(calls.some(([url]) => url.endsWith('/setup'))).toBe(true)
    )
    const setup = calls.find(([url]) => url.endsWith('/setup'))
    expect(JSON.parse(String(setup?.[1]?.body))).toEqual({
      candidates: ['0'],
      agent: 'claude:wsl',
      problem: 'password authentication failed',
    })
  })

  it('reads a project in a folder the Mac guards only once the user picks it', async () => {
    const calls = routeFetch([])
    const waiting = found({
      id: '0',
      name: 'shop',
      engine: null,
      host: null,
      port: null,
      database: null,
      provider: 'other',
      project: '/Users/me/Documents/shop',
      deferred: true,
      found_by: ['A recent project in Documents'],
    })
    const read = found({
      id: '0',
      name: 'shop',
      host: 'localhost',
      port: 5432,
      database: 'shop',
      provider: 'local',
      project: '/Users/me/Documents/shop',
      env_file: '/Users/me/Documents/shop/.env',
      env_var: 'DATABASE_URL',
      found_by: ['DATABASE_URL in .env'],
    })
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      calls.push([String(url), init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [], apps: [] })
      if (path.endsWith('/folder')) return json({ ...SCAN, candidates: [read] })
      if (path.endsWith('/scan') || path.includes('/discover/scan-1'))
        return json({ ...SCAN, candidates: [waiting] })
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    fireEvent.click(await screen.findByTestId('scan-computer'))
    const row = await screen.findByTestId('waiting-project')
    expect(row.textContent).toContain(
      'A recent project in Documents. Your Mac asks once before RDST looks inside.'
    )
    fireEvent.click(screen.getByTestId('look-in-project'))
    const rows = await screen.findAllByTestId('found-database')
    expect(rows[0].textContent).toContain('localhost:5432/shop')
    const asked = calls.find(([url]) => url.endsWith('/folder'))
    expect(JSON.parse(String(asked?.[1]?.body))).toEqual({
      project: '/Users/me/Documents/shop',
    })
  })

  it('searches with Claude: shows its trace, then only its short list, in one conversation', async () => {
    const calls = routeFetch([AGENT])
    let polls = 0
    const proposed = {
      ...SEARCH,
      id: 'run1',
      state: 'done',
      session: 's-1',
      candidates: [
        { ...FOUND[0], note: "shop's local database, from its .env" },
      ],
    }
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      calls.push([String(url), init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/scan')) return json(SCAN)
      if (path.endsWith('/discover')) return json(SEARCH)
      if (path.endsWith('/discover/run1/reply'))
        return json({ ...SEARCH, id: 'run2' })
      if (path.includes('/discover/run1')) {
        polls += 1
        return json(
          polls < 2
            ? {
                ...SEARCH,
                activity: [
                  { kind: 'say', text: 'Checking shop' },
                  { kind: 'tool', text: '$ ls shop' },
                ],
              }
            : proposed
        )
      }
      if (path.includes('/discover/scan-1')) return json(SCAN)
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    fireEvent.click(await screen.findByTestId('search-with-agent'))
    expect(
      (await screen.findByTestId('find-databases-title')).textContent
    ).toBe('Searching for databases with Claude')
    expect((await screen.findByTestId('search-trace')).textContent).toContain(
      'Reading shop.'
    )
    await waitFor(
      () =>
        expect(screen.getByTestId('search-trace').textContent).toContain(
          'Checking shop'
        ),
      { timeout: 4000 }
    )
    const started = calls.find(([url]) => url.endsWith('/discover'))
    expect(JSON.parse(String(started?.[1]?.body))).toEqual({
      agent: 'claude:wsl',
      scan: 'scan-1',
    })
    const rows = await screen.findAllByTestId(
      'found-database',
      {},
      { timeout: 4000 }
    )
    expect(rows).toHaveLength(1)
    expect(rows[0].textContent).toContain(
      "shop's local database, from its .env"
    )
    expect(screen.getByTestId('find-databases-title').textContent).toBe(
      'Claude suggests one database. Pick one to set up.'
    )
    expect(screen.getByTestId('show-all').textContent).toContain(
      'See everything RDST found (3)'
    )
    fireEvent.change(
      screen.getByLabelText("Tell Claude what you're looking for"),
      { target: { value: 'the billing one' } }
    )
    fireEvent.click(screen.getByTestId('ask-agent-send'))
    await waitFor(() =>
      expect(calls.some(([url]) => url.endsWith('/run1/reply'))).toBe(true)
    )
  })

  it('stages the prompt in a new Claude app conversation and follows it like a pasted one', async () => {
    // Only the app, no agent RDST runs: the start screen offers it.
    const calls = routeFetch([], ['claude'])
    renderFull()
    fireEvent.click(await screen.findByTestId('open-in-app'))
    expect(trackEvent).toHaveBeenCalledWith('find_view_shown', {
      agents: 0,
      agent_keys: '',
      apps: 'claude',
      signed_out: '',
    })
    await waitFor(() =>
      expect(calls.some(([url]) => url.endsWith('/open-app'))).toBe(true)
    )
    const opened = calls.find(([url]) => url.endsWith('/open-app'))
    expect(JSON.parse(String(opened?.[1]?.body))).toEqual({
      app: 'claude',
      attempt: 'n1',
    })
    expect(await screen.findByText(PROMPT)).toBeTruthy()
    expect(screen.getByTestId('agent-status')).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith('find_option_chosen', {
      option: 'open_app',
      app: 'claude',
    })
  })

  it('reports what the scan found and what the user did with it, never paths', async () => {
    routeFetch([AGENT])
    renderFull()
    const rows = await look()
    expect(trackEvent).toHaveBeenCalledWith('find_option_chosen', {
      option: 'scan',
      started: 'click',
    })
    expect(trackEvent).toHaveBeenCalledWith('find_scan_finished', {
      run_id: 'scan-1',
      state: 'done',
      found: 3,
      already_added: 0,
      hosted: 1,
      running: 2,
      from_projects: 1,
      projects_with_databases: 1,
      seconds: 3,
      projects: 2,
      docker: 1,
      ports: 0,
      clients: 0,
      folders: 1,
      failed: '',
    })
    fireEvent.click(rows[2])
    expect(trackEvent).toHaveBeenCalledWith('find_database_picked', {
      source: 'scan',
      provider: 'local',
      environment: 'development',
      has_secret: false,
      found_by: 'port',
    })
    fireEvent.click(screen.getByTestId('agent-setup'))
    expect(trackEvent).toHaveBeenCalledWith('find_action', {
      action: 'agent_setup',
      agent: 'claude:wsl',
      source: 'scan',
    })
    expect(await screen.findByText(/^Claude is setting up/)).toBeTruthy()
    cleanup()
    expect(trackEvent).toHaveBeenCalledWith('find_view_closed', {
      stage: 'setting_up',
      found: 3,
      used_agent: true,
      seconds_open: expect.any(Number),
    })
    const sent = JSON.stringify(vi.mocked(trackEvent).mock.calls)
    expect(sent).not.toContain('/home/me')
    expect(sent).not.toContain('localhost')
  })

  it('tells a scan that broke from one that found nothing, on screen and in PostHog', async () => {
    routeFetch([])
    vi.mocked(fetch).mockImplementation(async (url) => {
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [], apps: [] })
      if (path.endsWith('/scan'))
        return json({ ...SCAN, state: 'failed', candidates: [] })
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    fireEvent.click(await screen.findByTestId('scan-computer'))
    expect(
      await screen.findByText("RDST couldn't finish searching")
    ).toBeTruthy()
    expect(trackEvent).toHaveBeenCalledWith(
      'find_scan_finished',
      expect.objectContaining({ state: 'failed', found: 0 })
    )
    expect(screen.getByTestId('import-again')).toBeTruthy()
  })

  it('shows each place RDST looks as it finishes', async () => {
    routeFetch([])
    renderFull()
    fireEvent.click(await screen.findByTestId('scan-computer'))
    expect(
      (await screen.findByTestId('find-databases-title')).textContent
    ).toBe('Searching this computer')
    expect(screen.getByTestId('scan-steps').textContent).toContain(
      'Looking on this computer'
    )
    expect(screen.getByTestId('scan-steps').textContent).toContain(
      'Checking which ones are online'
    )
    expect(await screen.findAllByTestId('found-database')).toHaveLength(3)
  })

  it("shows the agent's question as choices and sends the pick back to it", async () => {
    const calls = routeFetch([AGENT])
    const asked = {
      ...SETUP,
      state: 'done',
      session: 's-1',
      question: {
        question: 'How should RDST reach orders-db?',
        options: ['Through bastion-prod', 'Through my VPN'],
      },
    }
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      calls.push([String(url), init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/scan'))
        return json({ ...SCAN, candidates: NO_SECRET })
      if (path.endsWith('/discover/scan-1/setup')) return json(asked)
      if (path.endsWith('/discover/setup1/reply'))
        return json({ ...SETUP, id: 'setup2', state: 'running' })
      if (path.includes('/discover/setup2'))
        return json({ ...SETUP, id: 'setup2' })
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    await look()
    fireEvent.click(screen.getByTestId('agent-setup'))
    expect(
      await screen.findByText('Claude has a question about shop')
    ).toBeTruthy()
    const question = screen.getByTestId('agent-question')
    expect(question.textContent).toContain('How should RDST reach orders-db?')
    fireEvent.click(
      screen.getByRole('button', { name: 'Through bastion-prod' })
    )
    await waitFor(() =>
      expect(calls.some(([url]) => url.endsWith('/reply'))).toBe(true)
    )
    const reply = calls.find(([url]) => url.endsWith('/reply'))
    expect(JSON.parse(String(reply?.[1]?.body))).toEqual({
      text: 'Through bastion-prod',
    })
    expect(trackEvent).toHaveBeenCalledWith('agent_question_answered', {
      run_id: 'setup2',
      agent: 'claude:wsl',
      chose: 'option',
      options: 2,
    })
  })

  it('follows a setup the user continued in the app until it finishes', async () => {
    routeFetch([AGENT])
    // RDST's own run had stopped on a question when the user took it to the app.
    const continued = {
      ...SETUP,
      state: 'failed',
      message: 'Claude took too long.',
      session: 's-1',
      in_app: 'claude',
      continue_in: 'claude',
      question: { question: 'Restart it?', options: ['Yes', 'No'] },
      activity: [
        { kind: 'rdst', text: 'Testing the connection to shop' },
        { kind: 'rdst', text: "Couldn't: Connection refused" },
      ],
    }
    let polls = 0
    vi.mocked(fetch).mockImplementation(async (url) => {
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/scan'))
        return json({ ...SCAN, candidates: NO_SECRET })
      if (path.endsWith('/discover/scan-1/setup')) return json(continued)
      if (path.includes(`/discover/${SETUP.id}`)) {
        polls += 1
        return json({
          ...continued,
          question: null,
          target: 'shop',
          monitoring: true,
        })
      }
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    await look()
    fireEvent.click(screen.getByTestId('agent-setup'))
    expect(
      (await screen.findByTestId('find-databases-title')).textContent
    ).toBe('Continuing in Claude')
    expect(screen.getByTestId('setup-in-app').textContent).toContain(
      'working in the Claude app'
    )
    expect(screen.getByRole('button', { name: 'Open Claude' })).toBeTruthy()
    // The steps follow the app's conversation, not where RDST's own run stopped.
    const states = [
      ...screen.getByTestId('setup-steps').querySelectorAll('[data-state]'),
    ].map((step) => step.getAttribute('data-state'))
    expect(states).toEqual(['done', 'active', 'todo', 'todo'])
    expect(screen.getByTestId('setup-trace').textContent).toContain(
      "Couldn't: Connection refused"
    )
    // The app owns the conversation: nothing here answers or redoes it.
    expect(screen.queryByRole('button', { name: 'Yes' })).toBeNull()
    expect(screen.queryByTestId('setup-manual')).toBeNull()
    await waitFor(
      () =>
        expect(screen.getByTestId('find-databases-title').textContent).toBe(
          'shop is set up and monitored'
        ),
      { timeout: 4000 }
    )
    expect(polls).toBeGreaterThan(0)
    expect(screen.queryByTestId('setup-in-app')).toBeNull()
    expect(screen.queryByText('Claude took too long.')).toBeNull()
  })

  it('offers to open a database RDST already has, never to set it up again', async () => {
    routeFetch([AGENT])
    vi.mocked(fetch).mockImplementation(async (url) => {
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/check-target'))
        return json({ target: 'rdst-demo-shop', ok: true, message: null })
      if (path.endsWith('/scan'))
        return json({
          ...SCAN,
          candidates: [{ ...FOUND[0], saved_as: 'rdst-demo-shop' }, FOUND[2]],
        })
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    fireEvent.click(await screen.findByTestId('scan-computer'))
    const rows = await screen.findAllByTestId('found-database')
    // What RDST has shows first, on its own; the list is what's left to add.
    expect(screen.getByTestId('already-in-rdst').textContent).toContain(
      'rdst-demo-shop'
    )
    expect(rows).toHaveLength(1)
    await waitFor(() =>
      expect((rows[0].querySelector('input') as HTMLInputElement).checked).toBe(
        true
      )
    )
    fireEvent.click(screen.getByTestId('already-open'))
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: '/database',
        search: { target: 'rdst-demo-shop', panel: 'enable' },
      })
    )
  })

  it("won't open a database RDST has but can't connect to: its fixes show on its row", async () => {
    const calls = routeFetch([AGENT])
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      calls.push([String(url), init])
      const json = (body: object) =>
        new Response(JSON.stringify(body), {
          headers: { 'content-type': 'application/json' },
        })
      const path = String(url)
      if (path.endsWith('/agents')) return json({ agents: [AGENT], apps: [] })
      if (path.endsWith('/check-target'))
        return json({
          target: 'readyset-postgres',
          ok: false,
          message: 'password authentication failed for user "rdst"',
        })
      if (path.endsWith('/agent-connect/setup')) return json(SETUP)
      if (path.endsWith('/scan') || path.includes('/discover/scan-1'))
        return json({
          ...SCAN,
          candidates: [
            { ...FOUND[0], saved_as: 'readyset-postgres' },
            FOUND[2],
          ],
        })
      return json({ prompt: PROMPT, attempt: 'n1', issued_at: 1700000000 })
    })
    renderFull()
    fireEvent.click(await screen.findByTestId('scan-computer'))
    fireEvent.click(await screen.findByTestId('already-open'))
    const failed = await screen.findByTestId('already-failed')
    expect(failed.textContent).toContain(
      "RDST can't connect to readyset-postgres"
    )
    expect(navigate).not.toHaveBeenCalled()
    expect(screen.queryByTestId('already-open')).toBeNull()
    expect(screen.getByTestId('already-remove')).toBeTruthy()
    fireEvent.click(screen.getByTestId('already-fix'))
    await waitFor(() =>
      expect(calls.some(([u]) => u.endsWith('/agent-connect/setup'))).toBe(true)
    )
    const fix = calls.find(([u]) => u.endsWith('/agent-connect/setup'))
    expect(JSON.parse(String(fix?.[1]?.body))).toEqual({
      agent: 'claude:wsl',
      targets: ['readyset-postgres'],
      problem: 'password authentication failed for user "rdst"',
      folder: '/home/me/projects/shop',
    })
  })

  it("sends a provider's database to its sign-in, and one without a password to the form", async () => {
    routeFetch([AGENT])
    const { onSignIn, onFillIn } = renderFull()
    const rows = await look()
    fireEvent.click(rows[1])
    expect((rows[1].querySelector('input') as HTMLInputElement).checked).toBe(
      true
    )
    expect((rows[0].querySelector('input') as HTMLInputElement).checked).toBe(
      false
    )
    // No password on disk: its sign-in comes first, and the agent can find the password.
    expect(screen.getByTestId('agent-setup')).toBeTruthy()
    expect(screen.queryByTestId('picked-connect')).toBeNull()
    fireEvent.click(screen.getByTestId('picked-sign-in'))
    expect(onSignIn).toHaveBeenCalledWith('supabase')
    fireEvent.click(rows[2])
    fireEvent.click(screen.getByTestId('picked-manual'))
    expect(onFillIn).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'postgres-5433' })
    )
  })

  it('has the agent set the pick up in steps and hands over for the Turn on monitoring click', async () => {
    const calls = routeFetch([AGENT], [], NO_SECRET)
    renderFull()
    await look()
    fireEvent.click(screen.getByTestId('agent-setup'))
    expect(await screen.findByText('Claude is setting up shop')).toBeTruthy()
    expect(screen.getByTestId('setup-steps').textContent).toContain(
      'Getting it ready to monitor'
    )
    // The steps, plus what the agent is doing right now and for how long.
    expect(screen.getByTestId('setup-trace').textContent).toContain(
      'Finding the port Docker publishes.'
    )
    expect(screen.getByTestId('setup-elapsed').textContent).toMatch(
      /^Working for/
    )
    const setup = calls.find(([url]) => url.endsWith('/setup'))
    expect(JSON.parse(String(setup?.[1]?.body))).toEqual({
      candidates: ['0'],
      agent: 'claude:wsl',
    })
    await waitFor(
      () =>
        expect(navigate).toHaveBeenCalledWith({
          to: '/database',
          search: { target: 'shop', panel: 'enable' },
        }),
      { timeout: 5000 }
    )
    expect(navigate).toHaveBeenCalledTimes(1)
  })
})
