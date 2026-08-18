import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ParameterDialog } from './ParameterDialog'

vi.mock('../SQLDisplay', () => ({
  SQLDisplay: ({ sql }: { sql: string }) => (
    <pre data-testid="sql-display">{sql}</pre>
  ),
}))

vi.mock('@tanstack/react-router', async () => ({
  Link: (await import('@/test-utils')).LinkStub,
}))

describe('ParameterDialog', () => {
  const onClose = vi.fn()
  const onSubmit = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
    vi.unstubAllGlobals()
  })

  const fewParamsQuery =
    'SELECT * FROM users WHERE id = :p1 AND name = :p2 AND age = :p3'
  const manyParamsQuery =
    'SELECT * FROM orders WHERE shop_id = :p1 AND status = :p2 AND created_at >= :p3 AND updated_at >= :p4 AND test = :p5 AND id > :p6 AND currency = :p7 AND fulfillment = :p8 AND archived = :p9 AND type = :p10'

  it('renders with few params in two-column layout', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />
    )

    expect(
      screen.getByText('3 parameters detected. Values apply only to this run.')
    ).toBeTruthy()
    expect(screen.getAllByPlaceholderText('Enter value')).toHaveLength(3)
    expect(screen.getByText('Original query')).toBeTruthy()
    expect(screen.getAllByText('Parameters').length).toBeGreaterThanOrEqual(1)
  })

  it('renders with many params in two-column layout', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={manyParamsQuery}
      />
    )

    expect(
      screen.getByText('10 parameters detected. Values apply only to this run.')
    ).toBeTruthy()
    expect(screen.getAllByPlaceholderText('Enter value')).toHaveLength(10)
    expect(screen.getByText('Original query')).toBeTruthy()
    expect(screen.getAllByText('Parameters').length).toBeGreaterThanOrEqual(1)
  })

  it('disables Analyze button when params are empty', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />
    )

    const button = screen.getByRole('button', { name: /Analyze Query/i })
    expect((button as HTMLButtonElement).disabled).toBe(true)
  })

  it('enables Analyze button when all params are filled', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />
    )

    const inputs = screen.getAllByPlaceholderText('Enter value')
    fireEvent.change(inputs[0], { target: { value: '1' } })
    fireEvent.change(inputs[1], { target: { value: 'Alice' } })
    fireEvent.change(inputs[2], { target: { value: '25' } })

    const button = screen.getByRole('button', { name: /Analyze Query/i })
    expect((button as HTMLButtonElement).disabled).toBe(false)
  })

  it('submits substituted query on Analyze', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />
    )

    const inputs = screen.getAllByPlaceholderText('Enter value')
    fireEvent.change(inputs[0], { target: { value: '1' } })
    fireEvent.change(inputs[1], { target: { value: 'Alice' } })
    fireEvent.change(inputs[2], { target: { value: '25' } })

    fireEvent.click(screen.getByRole('button', { name: /Analyze Query/i }))

    expect(onSubmit).toHaveBeenCalledTimes(1)
    const submitted = onSubmit.mock.calls[0][0] as string
    expect(submitted).toContain('1')
    expect(submitted).toContain("'Alice'")
    expect(submitted).toContain('25')
    expect(submitted).not.toContain(':p1')
  })

  it('pre-fills initial values from stored params', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
        initialValues={{ p1: 42, p2: 'Bob', p3: 30 }}
      />
    )

    const inputs = screen.getAllByPlaceholderText(
      'Enter value'
    ) as HTMLInputElement[]
    expect(inputs[0].value).toBe('42')
    expect(inputs[1].value).toBe('Bob')
    expect(inputs[2].value).toBe('30')
  })

  it('fills only unresolved values from safe schema evidence', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          target: 'prod',
          tables: [
            {
              name: 'users',
              description: null,
              business_context: null,
              row_estimate: null,
              relationships: [],
              columns: [
                {
                  name: 'id',
                  data_type: 'bigint',
                  description: null,
                  unit: null,
                  is_pii: false,
                  enum_values: null,
                },
                {
                  name: 'name',
                  data_type: 'text',
                  description: null,
                  unit: null,
                  is_pii: false,
                  enum_values: { Alice: 'Example account' },
                },
                {
                  name: 'age',
                  data_type: 'integer',
                  description: null,
                  unit: null,
                  is_pii: false,
                  enum_values: null,
                },
              ],
            },
          ],
          terminology: [],
          extensions: [],
          custom_types: [],
          metrics: [],
        }),
      })
    )
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
        target="prod"
        initialValues={{ p1: 42 }}
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Suggest values' }))

    const inputs = screen.getAllByPlaceholderText(
      'Enter value'
    ) as HTMLInputElement[]
    await waitFor(() => expect(inputs[1].value).toBe('Alice'))
    expect(inputs[0].value).toBe('42')
    expect(inputs[2].value).toBe('1')
    expect(screen.getByText('Observed value')).toBeTruthy()
    expect(screen.getByText('Schema enum · users.name')).toBeTruthy()
    expect(
      screen.getByText(
        'Filled 2 of 2 missing parameters. Review suggested values before running.'
      )
    ).toBeTruthy()
    expect(
      screen.queryByRole('link', { name: 'Initialize the semantic layer' })
    ).toBeNull()
  })

  it('reports how many parameters still need a value after suggesting', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 404 })
    )
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query="SELECT * FROM users WHERE name = :p1 LIMIT :p2"
        target="prod"
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Suggest values' }))

    await waitFor(() =>
      expect(
        screen.getByText(
          'Filled 1 of 2 missing parameters. 1 needs a value you provide. Schema evidence is unavailable, so only query-shape suggestions were applied. Review suggested values before running.'
        )
      ).toBeTruthy()
    )
    const inputs = screen.getAllByPlaceholderText(
      'Enter value'
    ) as HTMLInputElement[]
    expect(inputs[0].value).toBe('')
    expect(inputs[1].value).toBe('100')
    const initLink = screen.getByRole('link', {
      name: 'Initialize the semantic layer',
    })
    expect(initLink.getAttribute('href')).toBe('/schema')
  })

  it('offers sampled values from the database and a captured statement', async () => {
    const suggestions = {
      placeholders: [
        {
          placeholder: '$1',
          index: 1,
          column: 'orders.status',
          suggestions: [
            { value: 'shipped', provenance: 'Common value in orders.status (pg_stats)' },
            { value: 'paid', provenance: 'Common value in orders.status (pg_stats)' },
          ],
        },
      ],
      sample: {
        sql: "SELECT * FROM orders WHERE status = 'shipped'",
        source: 'pg_stat_activity (running now)',
        seen_at: null,
        aligned: true,
      },
    }
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(async (input: RequestInfo | URL) => {
        const url = input instanceof Request ? input.url : String(input)
        if (url.includes('/api/analyze/parameter-suggestions')) {
          return new Response(JSON.stringify(suggestions), {
            status: 200,
            headers: { 'content-type': 'application/json' },
          })
        }
        return new Response('{}', { status: 404 })
      })
    )
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query="SELECT * FROM orders WHERE status = $1"
        target="prod"
        queryHash="123"
      />
    )
    const chip = await screen.findByRole('button', { name: 'shipped' })
    fireEvent.click(chip)
    const input = screen.getByPlaceholderText('Enter value') as HTMLInputElement
    expect(input.value).toBe('shipped')
    expect(screen.getByText('Common value in orders.status (pg_stats)')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Analyze captured statement' }))
    expect(onSubmit).toHaveBeenCalledWith("SELECT * FROM orders WHERE status = 'shipped'")
  })
})
