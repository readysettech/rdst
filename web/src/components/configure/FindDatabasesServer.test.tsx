import { cleanup, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { useConfigure } from '../../lib/useConfigure'
import { FindDatabases, forgetLastImport } from './FindDatabases'

vi.mock('../../lib/useConfigure', () => ({ useConfigure: vi.fn() }))
vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEvent: vi.fn(),
}))

const CNPG = {
  id: '0',
  name: 'pg',
  engine: 'postgresql',
  host: 'pg-rw.database.svc.cluster.local',
  port: 5432,
  database: 'shop',
  user: 'shop',
  provider: 'kubernetes',
  environment: 'unknown',
  project: null,
  source: null,
  env_file: null,
  env_var: null,
  password_file: null,
  password_var: null,
  note: 'CloudNativePG cluster database/pg',
  password_hint:
    "kubectl -n database get secret shop-app -o jsonpath='{.data.password}' | base64 -d",
  found_by: ['CloudNativePG cluster database/pg'],
  reach: 'answering',
}

let meta: HTMLMetaElement
let kubernetes = true
let scanRun: object = {}

const json = (body: object) =>
  new Response(JSON.stringify(body), {
    headers: { 'content-type': 'application/json' },
  })

beforeEach(() => {
  forgetLastImport()
  meta = document.createElement('meta')
  meta.name = 'rdst-runtime'
  meta.content = 'container'
  document.head.appendChild(meta)
  kubernetes = true
  vi.mocked(useConfigure).mockReturnValue({} as never)
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string) => {
      if (String(url).includes('/api/agent-connect/agents'))
        return json({ agents: [], apps: [], kubernetes })
      if (String(url).includes('/api/agent-connect/scan')) return json(scanRun)
      return json({})
    })
  )
})

afterEach(() => {
  meta.remove()
  cleanup()
  vi.unstubAllGlobals()
})

function renderFind() {
  renderWithClient(
    <FindDatabases
      onOpen={vi.fn()}
      onSignIn={vi.fn()}
      onFillIn={vi.fn()}
      onPastePrompt={vi.fn()}
      onOpenApp={vi.fn()}
    />
  )
}

const scanned = (candidates: object[], message: string | null = null) => ({
  id: 'scan-1',
  agent: null,
  kind: 'scan',
  state: 'done',
  message,
  steps: 0,
  progress: [],
  candidates,
  methods: [
    {
      method: 'kubernetes',
      label: 'Databases in this Kubernetes cluster',
      state: candidates.length ? 'done' : 'failed',
      found: candidates.length,
      looked: [],
      seconds: 0.2,
    },
  ],
})

describe('finding a database from a server', () => {
  it('searches the cluster and offers the cluster prompt for a coding agent in a Kubernetes pod', async () => {
    renderFind()
    expect(await screen.findByText('Search this cluster')).toBeTruthy()
    expect(screen.queryByText('Search on this computer')).toBeNull()
    expect(await screen.findByTestId('agent-ways')).toBeTruthy()
    expect(screen.getByTestId('agent-prompt-reveal')).toBeTruthy()
    // No coding agent app runs on a server, so there's nothing to start one in.
    expect(screen.queryByTestId('open-in-app')).toBeNull()
  })

  it('offers no search outside Kubernetes, only sign-ins and the form', async () => {
    kubernetes = false
    renderFind()
    expect(await screen.findByText("Sign in to where it's hosted")).toBeTruthy()
    expect(screen.queryByTestId('scan-computer')).toBeNull()
    expect(screen.queryByTestId('agent-ways')).toBeNull()
  })

  it('says why the cluster search found nothing', async () => {
    scanRun = scanned(
      [],
      "RDST isn't allowed to list Services in this cluster. Turn on discovery.kubernetes in the Helm chart, or connect the database manually."
    )
    renderFind()
    fireEvent.click(await screen.findByText('Search this cluster'))
    expect(
      await screen.findByText('No databases found in this cluster')
    ).toBeTruthy()
    expect(screen.getByText(/Turn on discovery.kubernetes/)).toBeTruthy()
  })

  it('says no new databases when the cluster only has ones already in RDST', async () => {
    scanRun = scanned([{ ...CNPG, saved_as: 'shop' }])
    renderFind()
    fireEvent.click(await screen.findByText('Search this cluster'))
    expect(
      await screen.findByText('No new databases found in this cluster')
    ).toBeTruthy()
    expect(screen.getByTestId('already-in-rdst')).toBeTruthy()
  })

  it('runs the cluster search again from the results', async () => {
    scanRun = scanned([CNPG])
    renderFind()
    fireEvent.click(await screen.findByText('Search this cluster'))
    fireEvent.click(await screen.findByTestId('search-again'))
    await waitFor(() =>
      expect(
        vi
          .mocked(fetch)
          .mock.calls.filter(([url]) =>
            String(url).includes('/api/agent-connect/scan')
          ).length
      ).toBeGreaterThanOrEqual(2)
    )
    expect(screen.queryByText('Search this cluster')).toBeNull()
  })

  it("shows how to read an operator's password for a found cluster", async () => {
    scanRun = scanned([CNPG])
    renderFind()
    fireEvent.click(await screen.findByText('Search this cluster'))
    await waitFor(() =>
      expect(screen.getAllByText(/PostgreSQL on Kubernetes/).length).toBe(1)
    )
    fireEvent.click(screen.getByTestId('found-details-toggle'))
    expect(
      screen.getByText(
        "Read its password with: kubectl -n database get secret shop-app -o jsonpath='{.data.password}' | base64 -d"
      )
    ).toBeTruthy()
  })
})
