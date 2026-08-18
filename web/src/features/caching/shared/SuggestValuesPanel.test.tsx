import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SuggestValuesPanel } from './SuggestValuesPanel'

vi.mock('@tanstack/react-router', async () => ({
  Link: (await import('@/test-utils')).LinkStub,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('SuggestValuesPanel', () => {
  it('renders nothing when no selected query has parameters', () => {
    const { container } = render(
      <SuggestValuesPanel
        hasParameters={false}
        suggesting={false}
        missingParameterCount={0}
        message={null}
        schemaUnavailable={false}
        onSuggest={vi.fn()}
      />
    )

    expect(container.firstChild).toBeNull()
  })

  it('runs a suggestion pass while values are missing', () => {
    const onSuggest = vi.fn()
    render(
      <SuggestValuesPanel
        hasParameters
        suggesting={false}
        missingParameterCount={2}
        message={null}
        schemaUnavailable={false}
        onSuggest={onSuggest}
      />
    )

    const button = screen.getByRole('button', { name: 'Suggest values' })
    fireEvent.click(button)
    expect(onSuggest).toHaveBeenCalledTimes(1)
  })

  it('disables the action once every parameter has a value', () => {
    render(
      <SuggestValuesPanel
        hasParameters
        suggesting={false}
        missingParameterCount={0}
        message={null}
        schemaUnavailable={false}
        onSuggest={vi.fn()}
      />
    )

    const button = screen.getByRole('button', {
      name: 'Suggest values',
    }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
  })

  it('shows the pass outcome with the schema-initialization CTA', () => {
    render(
      <SuggestValuesPanel
        hasParameters
        suggesting={false}
        missingParameterCount={1}
        message="No schema evidence is available for this database."
        schemaUnavailable
        onSuggest={vi.fn()}
      />
    )

    expect(
      screen.getByText('No schema evidence is available for this database.')
    ).toBeTruthy()
    const initLink = screen.getByRole('link', {
      name: 'Initialize the semantic layer',
    })
    expect(initLink.getAttribute('href')).toBe('/schema')
  })
})
