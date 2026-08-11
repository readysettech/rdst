import { describe, expect, it } from 'vitest'
import { getDataResponseState } from './DataResponse'

describe('getDataResponseState', () => {
  it('resolves states in loading, error, empty, data order', () => {
    expect(
      getDataResponseState({
        data: [],
        isLoading: true,
        error: new Error('offline'),
      })
    ).toBe('loading')

    expect(
      getDataResponseState({
        data: [],
        isLoading: false,
        error: new Error('offline'),
      })
    ).toBe('error')

    expect(
      getDataResponseState({ data: [], isLoading: false, error: null })
    ).toBe('empty')

    expect(
      getDataResponseState({ data: ['query'], isLoading: false, error: null })
    ).toBe('data')
  })
})
