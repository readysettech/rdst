import { afterEach, describe, expect, it } from 'vitest'
import { thisComputer, thisDevice } from './desktop'

describe('machine labels', () => {
  afterEach(() => {
    document.head.querySelector('meta[name="rdst-runtime"]')?.remove()
  })

  it('names the computer outside server mode', () => {
    expect(thisComputer()).toBe('this computer')
    expect(thisDevice()).toBe('this computer')
  })

  it('names the server, and the browser for pushes, in server mode', () => {
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    expect(thisComputer()).toBe('this server')
    expect(thisDevice()).toBe('this browser')
  })
})
