import { describe, expect, it } from 'vitest'
import { QUERY_LIBRARY_DISPLAY_MODES } from './queryLibraryDisplay'

describe('query library display modes', () => {
  it('keeps Card 1 first and exposes Card 2 and List as alternatives', () => {
    expect(QUERY_LIBRARY_DISPLAY_MODES.map(({ value }) => value)).toEqual([
      'card-1',
      'card-2',
      'rows',
    ])
  })
})
