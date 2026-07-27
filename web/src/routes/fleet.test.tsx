import { describe, expect, it } from 'vitest'
import { Route } from './fleet'

// The standalone /fleet screen was merged into the Settings page (targets,
// groups, AWS discovery and CSV import now render under the Database
// connections section). The route survives only as a redirect so old deep links
// keep working.
describe('fleet route (redirect after merge)', () => {
  const runBeforeLoad = (search: { add?: 'aws' | 'csv' }) => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { add?: 'aws' | 'csv' }
    }) => void
    try {
      beforeLoad({ search })
    } catch (thrown) {
      return thrown
    }
    return undefined
  }

  it('beforeLoad redirects to the Database connections section', () => {
    const thrown = runBeforeLoad({})

    expect(thrown).toBeDefined()
    expect(JSON.stringify(thrown)).toContain('/configure')
    expect(JSON.stringify(thrown)).toContain('connections')
  })

  it('forwards ?add so the discovery drawer still opens on its tab', () => {
    // search: true carries the whole query string through the redirect,
    // including the validated ?add the drawer reads on the other side.
    expect(JSON.stringify(runBeforeLoad({ add: 'aws' }))).toContain(
      '"search":true'
    )
  })

  it('only accepts the two known drawer tabs', () => {
    const validateSearch = Route.options.validateSearch as (
      search: Record<string, unknown>
    ) => { add?: string }

    expect(validateSearch({ add: 'aws' })).toEqual({ add: 'aws' })
    expect(validateSearch({ add: 'csv' })).toEqual({ add: 'csv' })
    expect(validateSearch({ add: 'nope' })).toEqual({})
    expect(validateSearch({})).toEqual({})
  })
})
