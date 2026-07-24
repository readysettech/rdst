import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { CachePage } from './-cache-page'

const startCacheTestRun = vi.fn()
const navigate = vi.fn()

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'tpcds-sf100', setTarget: vi.fn() }),
}))

vi.mock('../lib/backgroundRuns', () => ({
  startCacheTestRun: (...args: unknown[]) => startCacheTestRun(...args),
  useBackgroundRuns: () => [],
}))

vi.mock('../lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({
    queries: [
      {
        hash: 'abc123456789',
        sql: 'SELECT * FROM item LIMIT 1',
        tag: 'Item lookup',
        target: 'tpcds-sf100',
        source: 'manual',
        readyset_supported: 'yes',
        most_recent_params: {},
      },
    ],
    isLoading: false,
    listError: null,
  }),
}))

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      {children}
    </QueryClientProvider>
  )
}

describe('CachePage speed-test workflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    startCacheTestRun.mockResolvedValue('run-1')
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          phase: 'ready',
          current_target: 'tpcds-sf100',
          generation: 1,
          queued_requests: 0,
          container_name: 'rdst-readyset-sandbox',
          healthy: true,
          docker_installed: true,
          docker_running: true,
        }),
      })
    )
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('runs a Readyset comparison directly from the Comparisons page', async () => {
    render(<CachePage />, { wrapper })

    expect(await screen.findByText('Queries to comparison')).toBeTruthy()
    expect(screen.getByText('Item lookup')).toBeTruthy()
    const testButton = screen.getByRole('button', {
      name: /Compare with Readyset/,
    }) as HTMLButtonElement
    await waitFor(() => expect(testButton.disabled).toBe(false))
    fireEvent.click(testButton)

    await waitFor(() =>
      expect(startCacheTestRun).toHaveBeenCalledWith({
        query: 'SELECT * FROM item LIMIT 1',
        target: 'tpcds-sf100',
        query_hash: 'abc123456789',
        label: 'Item lookup',
        iterations: 15,
        warmup: 5,
      })
    )
  })

  it('surfaces a failed prewarm and lets the user retry it', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        phase: 'error',
        current_target: null,
        generation: 0,
        queued_requests: 0,
        container_name: 'rdst-readyset-sandbox',
        healthy: false,
        docker_installed: true,
        docker_running: true,
        failed_target: 'tpcds-sf100',
        last_error:
          'Readyset could not be prepared for tpcds-sf100. Check that the database is reachable, then retry.',
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<CachePage />, { wrapper })

    expect(
      await screen.findByText(/Readyset could not be prepared/)
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry preparation' }))

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/cache/sandbox/prewarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: 'tpcds-sf100' }),
      })
    )
  })

  it('repairs an absent sandbox after the backend restarts', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        phase: 'absent',
        current_target: null,
        generation: 0,
        queued_requests: 0,
        container_name: 'rdst-readyset-sandbox',
        healthy: false,
        docker_installed: true,
        docker_running: true,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)
    render(<CachePage />, { wrapper })

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith('/api/cache/sandbox/prewarm', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: 'tpcds-sf100' }),
      })
    )
  })

  it('shows why preparation was rejected when the upstream is unavailable', async () => {
    const message =
      "Readyset comparisons require a reachable source database. RDST could not connect to 'tpcds-sf100', so no Readyset work was queued."
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === '/api/cache/sandbox/prewarm') {
        return {
          ok: false,
          status: 503,
          json: async () => ({
            code: 'upstream_unavailable',
            message,
            detail: { detail: 'connection refused' },
          }),
        }
      }
      return {
        ok: true,
        json: async () => ({
          phase: 'absent',
          current_target: null,
          generation: 0,
          queued_requests: 0,
          container_name: 'rdst-readyset-sandbox',
          healthy: false,
          docker_installed: true,
          docker_running: true,
        }),
      }
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<CachePage />, { wrapper })

    expect(await screen.findByText(message)).toBeTruthy()
    expect(
      screen.getAllByRole('button', { name: 'Retry preparation' }).length
    ).toBeGreaterThan(0)
  })

  it('shows useful preparation feedback instead of raw sandbox diagnostics', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          phase: 'provisioning',
          current_target: null,
          generation: 0,
          lease_purpose: 'prewarm',
          queued_requests: 0,
          container_name: 'rdst-readyset-sandbox',
          healthy: false,
          docker_installed: true,
          docker_running: true,
        }),
      })
    )
    render(<CachePage />, { wrapper })

    expect(
      await screen.findByText(/Preparing Readyset for tpcds-sf100/)
    ).toBeTruthy()
    expect(screen.queryByText('Local Readyset sandbox')).toBeNull()
    expect(screen.queryByText('Prepared target')).toBeNull()
    expect(screen.queryByText('Current activity')).toBeNull()
  })

  it('explains the Docker prerequisite and does not start a test', async () => {
    const openDockerDocs = vi
      .spyOn(window, 'open')
      .mockImplementation(() => null)
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          phase: 'absent',
          current_target: null,
          generation: 0,
          queued_requests: 0,
          container_name: 'rdst-readyset-sandbox',
          healthy: false,
          docker_installed: false,
          docker_running: false,
        }),
      })
    )

    render(<CachePage />, { wrapper })

    expect(
      await screen.findByText('Install Docker to try Readyset')
    ).toBeTruthy()
    expect(
      screen.getByText(
        /Analyze Query works without Docker. Docker is only required/
      )
    ).toBeTruthy()
    expect(
      (
        screen.getByRole('button', {
          name: /Compare with Readyset/,
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Get Docker' }))
    expect(openDockerDocs).toHaveBeenCalledWith(
      'https://docs.docker.com/get-started/get-docker/',
      '_blank',
      'noopener,noreferrer'
    )
    expect(startCacheTestRun).not.toHaveBeenCalled()
  })

  it('asks the user to start Docker when it is installed but stopped', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          phase: 'absent',
          current_target: null,
          generation: 0,
          queued_requests: 0,
          container_name: 'rdst-readyset-sandbox',
          healthy: false,
          docker_installed: true,
          docker_running: false,
        }),
      })
    )

    render(<CachePage />, { wrapper })

    expect(await screen.findByText('Start Docker to try Readyset')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Get Docker' })).toBeNull()
    expect(screen.getByRole('button', { name: 'Check again' })).toBeTruthy()
  })
})
