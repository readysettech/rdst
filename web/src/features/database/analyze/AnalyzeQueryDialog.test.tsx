import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'

const add = vi.hoisted(() => ({
  mutate: vi.fn(),
  reset: vi.fn(),
  isPending: false,
  isError: false,
  error: null,
}))
const enqueue = vi.hoisted(() => ({
  mutate: vi.fn(),
  reset: vi.fn(),
  isPending: false,
  isError: false,
  error: null as Error | null,
}))

vi.mock('@/lib/monitor/hooks', async (original) => ({
  ...(await original<typeof import('@/lib/monitor/hooks')>()),
  useAddQuery: () => add,
  useEnqueueAnalysis: () => enqueue,
}))
vi.mock('@/components/top', () => ({
  ParameterDialog: () => <div data-testid="values-step" />,
}))

const { AnalyzeQueryDialog } = await import('./AnalyzeQueryDialog')

beforeEach(() => {
  add.mutate.mockReset()
  enqueue.mutate.mockReset()
  enqueue.isError = false
  enqueue.error = null
})
afterEach(cleanup)

it('takes one statement, adds it to the list and queues its analysis', () => {
  const onClose = vi.fn()
  const onShowQuery = vi.fn()
  renderWithClient(
    <AnalyzeQueryDialog
      target="demo"
      open
      onClose={onClose}
      onShowQuery={onShowQuery}
    />
  )
  expect(screen.getByRole('heading', { name: 'Analyze a query' })).toBeTruthy()
  expect(
    screen.getByText(
      'Runs EXPLAIN and, when timings are on, a read-only sample with a time limit.'
    )
  ).toBeTruthy()
  expect(screen.getByText('Takes 10 to 30 seconds.')).toBeTruthy()
  expect(screen.queryByRole('button', { name: /Cancel|Not now/ })).toBeNull()
  const analyze = screen.getByRole('button', { name: 'Analyze' })
  expect(analyze.hasAttribute('disabled')).toBe(true)
  fireEvent.change(screen.getByRole('textbox', { name: 'SQL' }), {
    target: { value: 'select id from posts where score > 10;' },
  })
  fireEvent.click(analyze)
  expect(add.mutate).toHaveBeenCalledWith(
    { target: 'demo', input: { sql: 'select id from posts where score > 10' } },
    expect.anything()
  )
  add.mutate.mock.calls[0][1].onSuccess({ key: 'k1', created: true })
  expect(enqueue.mutate).toHaveBeenCalledWith('k1', expect.anything())
  // The dialog stays until the analysis is queued.
  expect(onShowQuery).not.toHaveBeenCalled()
  enqueue.mutate.mock.calls[0][1].onSuccess()
  // One URL write closes the dialog and opens the query.
  expect(onClose).not.toHaveBeenCalled()
  expect(onShowQuery).toHaveBeenCalledWith('k1')
})

it('keeps the dialog and the SQL when the analysis is refused, and says why', () => {
  const onShowQuery = vi.fn()
  const sql =
    'with gone as (delete from posts where id = 1 returning *) select * from gone'
  const view = renderWithClient(
    <AnalyzeQueryDialog
      target="demo"
      open
      onClose={vi.fn()}
      onShowQuery={onShowQuery}
    />
  )
  fireEvent.change(screen.getByRole('textbox', { name: 'SQL' }), {
    target: { value: sql },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Analyze' }))
  add.mutate.mock.calls[0][1].onSuccess({ key: 'k1', created: true })
  enqueue.isError = true
  enqueue.error = new Error("WITH changes data, so RDST doesn't run it.")
  enqueue.mutate.mock.calls[0][1].onError(enqueue.error)
  view.rerender(
    <AnalyzeQueryDialog
      target="demo"
      open
      onClose={vi.fn()}
      onShowQuery={onShowQuery}
    />
  )

  expect(onShowQuery).not.toHaveBeenCalled()
  expect(
    screen.getByText(
      "The analysis didn't start: WITH changes data, so RDST doesn't run it."
    )
  ).toBeTruthy()
  const box = screen.getByRole('textbox', {
    name: 'SQL',
  }) as HTMLTextAreaElement
  expect(box.value).toBe(sql)
  expect(box.disabled).toBe(false)
})

it('asks for values when the statement has placeholders', () => {
  renderWithClient(
    <AnalyzeQueryDialog
      target="demo"
      open
      onClose={vi.fn()}
      onShowQuery={vi.fn()}
    />
  )
  fireEvent.change(screen.getByRole('textbox', { name: 'SQL' }), {
    target: { value: 'select id from posts where owneruserid = $1' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Analyze' }))
  expect(screen.getByTestId('values-step')).toBeTruthy()
  expect(add.mutate).not.toHaveBeenCalled()
})
