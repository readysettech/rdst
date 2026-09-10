import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SchemaEmptyState } from './SchemaEmptyState'
import { SchemaTableTree } from './SchemaTableTree'

afterEach(cleanup)

describe('SchemaEmptyState', () => {
  it('is one heading, one sentence and one action', () => {
    render(<SchemaEmptyState target="orders" onInit={() => {}} />)

    expect(
      screen.getByRole('heading', { name: 'No semantic layer yet' })
    ).toBeTruthy()
    expect(
      screen.getByRole('button', { name: /Initialize schema/ })
    ).toBeTruthy()
    // The two "feature cards" were implementation detail, not a next step.
    expect(screen.queryByText('Schema introspection')).toBeNull()
    expect(screen.queryByText('YAML configuration')).toBeNull()
  })

  it('stops accepting clicks while initialization is in flight', () => {
    const onInit = vi.fn()
    render(<SchemaEmptyState target="orders" onInit={onInit} isLoading />)

    fireEvent.click(screen.getByRole('button', { name: /Initialize schema/ }))
    expect(onInit).not.toHaveBeenCalled()
  })
})

describe('SchemaTableTree', () => {
  it('gives an empty layer a way out instead of a dead-end sentence', () => {
    const onRefreshStructure = vi.fn()
    render(
      <SchemaTableTree tables={[]} onRefreshStructure={onRefreshStructure} />
    )

    expect(
      screen.getByRole('heading', { name: 'No tables in the semantic layer' })
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Refresh structure/ }))
    expect(onRefreshStructure).toHaveBeenCalledTimes(1)
  })
})
