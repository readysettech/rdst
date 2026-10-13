import { describe, expect, it } from 'vitest'
import { goChord, inEditable } from './goShortcuts'

describe('goChord', () => {
  it('arms on g and goes on the second key', () => {
    expect(goChord(false, 'g')).toEqual({ armed: true, to: null })
    expect(goChord(true, 'h')).toEqual({ armed: false, to: '/' })
    expect(goChord(true, 'i')).toEqual({ armed: false, to: '/inbox' })
    expect(goChord(true, 'a')).toEqual({ armed: false, to: '/actions' })
    expect(goChord(true, 'd')).toEqual({ armed: false, to: '/database' })
  })

  it('opens the shortcut list on ?', () => {
    expect(goChord(false, '?')).toEqual({ armed: false, to: '/help' })
  })

  it('ignores a lone letter and drops a chord that goes nowhere', () => {
    expect(goChord(false, 'h')).toEqual({ armed: false, to: null })
    expect(goChord(true, 'x')).toEqual({ armed: false, to: null })
  })
})

describe('inEditable', () => {
  it('reads a keydown on the document as outside any field', () => {
    expect(inEditable(document)).toBe(false)
    expect(inEditable(document.createElement('input'))).toBe(true)
    expect(inEditable(document.createElement('div'))).toBe(false)
  })
})
