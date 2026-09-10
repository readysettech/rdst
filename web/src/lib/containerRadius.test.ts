import { globSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const WEB_APPS = join(import.meta.dirname, '../../../..')
const THEME = join(WEB_APPS, 'packages/tailwind-base/style.css')

// F-37: four container radii (8, 12, 16, 20px) were in simultaneous use with
// nothing about an element's role predicting which it got. The roles are named
// in @rs/tailwind-base now, so an arbitrary radius is a call site that skipped
// the decision.
describe('container radius roles', () => {
  it('names every container radius role in the theme', () => {
    const theme = readFileSync(THEME, 'utf8')

    for (const role of ['card', 'panel', 'control', 'pill']) {
      expect(theme).toContain(`--radius-${role}:`)
    }
  })

  it('leaves no arbitrary radius in the app or the design system', () => {
    const sources = [
      ...globSync('**/*.{ts,tsx}', {
        cwd: join(WEB_APPS, 'apps/rdst/src'),
      }).map((f) => join(WEB_APPS, 'apps/rdst/src', f)),
      ...globSync('**/*.{ts,tsx}', {
        cwd: join(WEB_APPS, 'packages/ui-new/src'),
      }).map((f) => join(WEB_APPS, 'packages/ui-new/src', f)),
    ]

    const offenders = sources.filter((file) =>
      /rounded-\[/.test(readFileSync(file, 'utf8'))
    )

    expect(offenders).toEqual([])
  })
})
