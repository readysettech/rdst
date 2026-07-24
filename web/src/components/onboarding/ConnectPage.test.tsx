import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { startBootstrapRun } from '../../lib/backgroundRuns'
import { useConfigure } from '../../lib/useConfigure'
import { useOnboarding } from '../../lib/useOnboarding'
import { ConnectPage } from './ConnectPage'

const mockNavigate = vi.fn()
const mockHistoryPush = vi.fn()

vi.mock('@tanstack/react-router', async () => {
  const actual = await vi.importActual('@tanstack/react-router')
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useRouter: () => ({ history: { push: mockHistoryPush } }),
  }
})

vi.mock('../../lib/backgroundRuns', () => ({
  startBootstrapRun: vi.fn(),
}))

vi.mock('../../lib/useConfigure', () => ({
  useConfigure: vi.fn(),
}))

vi.mock('../../lib/useOnboarding', () => ({
  useOnboarding: vi.fn(),
}))

// The real form pulls in the full configure stack; the page only needs a
// hook to fire onSubmit with form data.
vi.mock('../configure', () => ({
  ConfigureForm: ({
    onSubmit,
  }: {
    onSubmit: (data: Record<string, unknown>) => void
  }) => (
    <button type="button" onClick={() => onSubmit({ name: 'mydb' })}>
      Mock Submit
    </button>
  ),
}))

const addTarget = vi.fn()
const setDefaultTarget = vi.fn()
const completeInit = vi.fn()

function renderPage(props: { redirectTo?: string; from?: string } = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={queryClient}>
      <ConnectPage {...props} />
    </QueryClientProvider>
  )
  return queryClient
}

describe('ConnectPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    addTarget.mockResolvedValue(undefined)
    setDefaultTarget.mockResolvedValue(undefined)
    completeInit.mockResolvedValue(true)
    vi.mocked(useConfigure).mockReturnValue({
      addTarget,
      setDefaultTarget,
      loading: false,
    } as unknown as ReturnType<typeof useConfigure>)
    vi.mocked(useOnboarding).mockReturnValue({
      completeInit,
    } as unknown as ReturnType<typeof useOnboarding>)
  })

  afterEach(() => {
    cleanup()
  })

  it('skip for now leaves the gate and goes home', () => {
    renderPage({ redirectTo: '/analyze' })

    fireEvent.click(screen.getByRole('button', { name: /Skip for now/i }))

    expect(mockNavigate).toHaveBeenCalledWith({ to: '/' })
  })

  it('completes init and returns to the intended destination on connect', async () => {
    const queryClient = renderPage({ redirectTo: '/analyze' })
    queryClient.setQueryData(['init-status'], { initialized: false })

    fireEvent.click(screen.getByRole('button', { name: /Mock Submit/i }))

    await waitFor(() => {
      expect(mockHistoryPush).toHaveBeenCalledWith('/analyze')
    })
    expect(addTarget).toHaveBeenCalled()
    expect(setDefaultTarget).toHaveBeenCalledWith('mydb')
    expect(completeInit).toHaveBeenCalled()
    expect(startBootstrapRun).toHaveBeenCalledWith('mydb')
    expect(queryClient.getQueryData(['init-status'])).toMatchObject({
      initialized: true,
    })
  })

  it('stays on the page when a connect step fails', async () => {
    // addTarget can throw after the target was created server-side (e.g. the
    // password secret save failed); the page must not complete init or leave.
    addTarget.mockRejectedValue(new Error('secret save failed'))

    renderPage()

    fireEvent.click(screen.getByRole('button', { name: /Mock Submit/i }))

    await waitFor(() => {
      expect(addTarget).toHaveBeenCalled()
    })
    expect(completeInit).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
    expect(mockHistoryPush).not.toHaveBeenCalled()
  })
})
