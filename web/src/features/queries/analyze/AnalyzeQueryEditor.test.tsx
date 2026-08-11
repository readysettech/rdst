import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../../components/SQLInput', () => ({
  SQLInput: ({ value }: { value: string }) => (
    <span data-testid="sql-input-value">{value}</span>
  ),
}))

vi.mock('../../../components/sql-input/SQLSchemaStatus', () => ({
  SQLSchemaStatus: () => <span>3 tables · PostgreSQL</span>,
}))

vi.mock('../../../components/sql-input/useSQLSchema', () => ({
  useSQLSchema: () => ({
    schema: {
      tables: { users: {}, orders: {}, products: {} },
      dialect: 'postgresql',
    },
    isLoading: false,
  }),
}))

vi.mock('./query-editor/AnalysisModePicker', () => ({
  AnalysisModePicker: () => <span>Detailed analysis</span>,
}))

import { AnalyzeQueryEditor } from './AnalyzeQueryEditor'

afterEach(cleanup)

describe('AnalyzeQueryEditor', () => {
  it('keeps Analyze disabled until SQL is present', () => {
    const onAnalyze = vi.fn()
    const { rerender } = render(
      <AnalyzeQueryEditor
        value=""
        onChange={vi.fn()}
        onAnalyze={onAnalyze}
        target="prod"
      />
    )

    expect(screen.getByText('SQL query')).toBeTruthy()
    expect(screen.getByText('3 tables · PostgreSQL')).toBeTruthy()
    expect(
      screen
        .getByRole('button', { name: 'Analyze query' })
        .hasAttribute('disabled')
    ).toBe(true)

    rerender(
      <AnalyzeQueryEditor
        value="SELECT 1"
        onChange={vi.fn()}
        onAnalyze={onAnalyze}
        target="prod"
      />
    )

    fireEvent.click(screen.getByRole('button', { name: 'Analyze query' }))
    expect(onAnalyze).toHaveBeenCalledTimes(1)
  })

  it('disables editor actions without presenting a false loading state', () => {
    render(
      <AnalyzeQueryEditor
        value="SELECT 1"
        onChange={vi.fn()}
        onAnalyze={vi.fn()}
        disabled
        target="locked"
      />
    )

    const action = screen.getByRole('button', { name: 'Analyze query' })
    expect(action.hasAttribute('disabled')).toBe(true)
    expect(action.getAttribute('aria-busy')).not.toBe('true')
  })
})
