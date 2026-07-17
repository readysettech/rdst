import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { ReadysetCacheabilitySection } from './AnalysisSections'

describe('ReadysetCacheabilitySection', () => {
  it('does not turn an unavailable ReadySet check into a blocked verdict', () => {
    render(
      <ReadysetCacheabilitySection
        cacheability={{
          checked: false,
          cacheable: null,
          confidence: 'unknown',
          method: 'readyset_unavailable',
          explanation: 'ReadySet endpoint was not reachable',
          issues: [],
          warnings: [],
        }}
      />,
    )

    expect(screen.getAllByText('Not Verified').length).toBeGreaterThan(0)
    expect(screen.getByText('UNAVAILABLE')).toBeTruthy()
    expect(screen.queryByText('Not Cacheable')).toBeNull()
    expect(screen.queryByText('BLOCKED')).toBeNull()
  })
})
