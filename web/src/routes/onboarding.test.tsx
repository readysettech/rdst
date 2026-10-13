import { describe, expect, it } from 'vitest'
import { Route } from './onboarding'

describe('onboarding route', () => {
  it('lands on Home with the add-database dialog open', () => {
    const beforeLoad = Route.options.beforeLoad as () => void
    let thrown: unknown
    try {
      beforeLoad()
    } catch (caught) {
      thrown = caught
    }

    const redirect = JSON.stringify(thrown)
    expect(redirect).toContain('"to":"/"')
    expect(redirect).toContain('"add":1')
  })
})
