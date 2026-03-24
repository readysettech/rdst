import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { ParameterDialog } from './ParameterDialog'

vi.mock('../SQLDisplay', () => ({
  SQLDisplay: ({ sql }: { sql: string }) => (
    <pre data-testid="sql-display">{sql}</pre>
  ),
}))

describe('ParameterDialog', () => {
  const onClose = vi.fn()
  const onSubmit = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  const fewParamsQuery = 'SELECT * FROM users WHERE id = :p1 AND name = :p2 AND age = :p3'
  const manyParamsQuery =
    'SELECT * FROM orders WHERE shop_id = :p1 AND status = :p2 AND created_at >= :p3 AND updated_at >= :p4 AND test = :p5 AND id > :p6 AND currency = :p7 AND fulfillment = :p8 AND archived = :p9 AND type = :p10'

  it('renders with few params in two-column layout', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />,
    )

    expect(screen.getByText('3 parameters detected')).toBeTruthy()
    expect(screen.getAllByPlaceholderText('Enter value')).toHaveLength(3)
    expect(screen.getByText('Original Query')).toBeTruthy()
    expect(screen.getAllByText('Parameters').length).toBeGreaterThanOrEqual(1)
  })

  it('renders with many params in two-column layout', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={manyParamsQuery}
      />,
    )

    expect(screen.getByText('10 parameters detected')).toBeTruthy()
    expect(screen.getAllByPlaceholderText('Enter value')).toHaveLength(10)
    expect(screen.getByText('Original Query')).toBeTruthy()
    expect(screen.getAllByText('Parameters').length).toBeGreaterThanOrEqual(1)
  })

  it('disables Analyze button when params are empty', () => {
    render(
      <ParameterDialog
        isOpen
        onClose={onClose}
        onSubmit={onSubmit}
        query={fewParamsQuery}
      />,
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
      />,
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
      />,
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
      />,
    )

    const inputs = screen.getAllByPlaceholderText('Enter value') as HTMLInputElement[]
    expect(inputs[0].value).toBe('42')
    expect(inputs[1].value).toBe('Bob')
    expect(inputs[2].value).toBe('30')
  })
})
