import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { QueryCardFooter } from './QueryCardFooter'

afterEach(cleanup)

describe('QueryCardFooter', () => {
  it('wraps its action row so the card primary survives a phone width', () => {
    render(
      <QueryCardFooter
        meta="hash 11c45172 · e2e-guard"
        secondaryActions={
          <button type="button">Compare against Readyset</button>
        }
        primaryAction={<button type="button">Analyze</button>}
        detailsOpen={false}
        onToggleDetails={() => {}}
      />
    )

    const row = screen.getByRole('button', { name: 'Analyze' }).parentElement
    expect(row?.className).toContain('flex-wrap')
    expect(row?.className).not.toContain('shrink-0')
  })
})
