import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ReadysetCacheabilitySection } from './AnalysisSections'

afterEach(cleanup)

describe('ReadysetCacheabilitySection', () => {
  it('does not turn an unavailable Readyset check into a blocked verdict', () => {
    render(
      <ReadysetCacheabilitySection
        cacheability={{
          checked: false,
          cacheable: null,
          confidence: 'unknown',
          method: 'readyset_unavailable',
          explanation: 'Readyset endpoint was not reachable',
          issues: [],
          warnings: [],
        }}
      />
    )

    expect(screen.getAllByText('Not Verified').length).toBeGreaterThan(0)
    expect(screen.getByText('UNAVAILABLE')).toBeTruthy()
    expect(screen.queryByText('Not Cacheable')).toBeNull()
    expect(screen.queryByText('Not cacheable')).toBeNull()
  })

  it('presents a positive static screen without claiming verification', () => {
    const tryReadyset = vi.fn()
    render(
      <ReadysetCacheabilitySection
        cacheability={{
          checked: true,
          cacheable: true,
          confidence: 'medium',
          method: 'static_analysis',
          explanation: 'The query appears cacheable.',
          issues: [],
          warnings: [],
        }}
        onSetUpCaching={tryReadyset}
      />
    )

    expect(screen.getAllByText('No obvious blockers').length).toBeGreaterThan(0)
    expect(screen.getByText('STATIC CHECK')).toBeTruthy()
    expect(screen.queryByText('Readyset compatible')).toBeNull()
    expect(screen.queryByText('Likely compatible')).toBeNull()
    const verdict = screen
      .getAllByText('No obvious blockers')
      .find((element) => element.tagName === 'P')
    expect(verdict?.closest('div.rounded-xl')?.className).toContain(
      'border-border-positive-soft'
    )

    fireEvent.click(screen.getByRole('button', { name: /Try with Readyset/ }))
    expect(tryReadyset).toHaveBeenCalledOnce()
  })

  it('presents static issues as potential blockers', () => {
    const tryReadyset = vi.fn()
    render(
      <ReadysetCacheabilitySection
        cacheability={{
          checked: true,
          cacheable: false,
          confidence: 'high',
          method: 'static_analysis',
          explanation: 'Unsupported SQL was detected.',
          issues: ['Window functions may not be supported.'],
          warnings: [],
        }}
        onSetUpCaching={tryReadyset}
      />
    )

    expect(screen.getAllByText('Potential blockers').length).toBeGreaterThan(0)
    expect(screen.getByText('STATIC CHECK')).toBeTruthy()
    expect(
      screen.getByText('Window functions may not be supported.')
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Try with Readyset/ }))
    expect(tryReadyset).toHaveBeenCalledOnce()
  })
})
