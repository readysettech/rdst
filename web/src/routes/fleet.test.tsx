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

  it('beforeLoad redirects to the Targets section', () => {
    const thrown = runBeforeLoad({})

    expect(thrown).toBeDefined()
    expect(JSON.stringify(thrown)).toContain('/configure')
    expect(JSON.stringify(thrown)).toContain('connections')
  })

  it('forwards ?add so the discovery drawer still opens on its tab', () => {
    // The redirect rebuilds the search so it can add `from`, and carries the
    // validated ?add the drawer reads on the other side with it.
    expect(JSON.stringify(runBeforeLoad({ add: 'aws' }))).toContain(
      '"add":"aws"'
    )
  })

  it('tells Settings where the reader came from, so the move is explained', () => {
    expect(JSON.stringify(runBeforeLoad({}))).toContain('"from":"fleet"')
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
