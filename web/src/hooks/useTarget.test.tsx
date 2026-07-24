import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { useTarget } from './useTarget'

describe('useTarget', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
  })

  it('restores a target from localStorage without starting Readyset work', () => {
    localStorage.setItem('rdst_selected_target', 'tpcds-sf100')

    const { result } = renderHook(() => useTarget())

    expect(result.current.target).toBe('tpcds-sf100')
  })

  it('persists and clears the selected target', () => {
    const { result } = renderHook(() => useTarget())

    act(() => result.current.setTarget('imdb'))
    expect(result.current.target).toBe('imdb')
    expect(localStorage.getItem('rdst_selected_target')).toBe('imdb')

    act(() => result.current.setTarget(null))
    expect(result.current.target).toBeNull()
    expect(localStorage.getItem('rdst_selected_target')).toBeNull()
  })
})
