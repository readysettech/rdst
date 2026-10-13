import { describe, expect, it } from 'vitest'
import { Route } from './configure'

const parse = (search: Record<string, unknown>) =>
  (Route.options.validateSearch as (s: Record<string, unknown>) => unknown)(
    search
  ) as { add?: unknown }

describe('configure search', () => {
  it('keeps the add-database dialog request alongside provider tabs', () => {
    expect(parse({ add: '1' }).add).toBe(1)
    expect(parse({ add: 1 }).add).toBe(1)
    expect(parse({ add: 'aws' }).add).toBe('aws')
    expect(parse({ add: 'nope' }).add).toBeUndefined()
    expect(parse({}).add).toBeUndefined()
  })
})
