import { describe, expect, it } from 'vitest'
import { rendererGoneAction } from './renderer-recovery.js'
import { rendererCrashPageHtml } from './startup-page.js'

describe('rendererGoneAction', () => {
  it('leaves a clean exit alone', () => {
    expect(rendererGoneAction('clean-exit', [], 0)).toEqual({ action: 'none' })
  })

  it('reloads up to three times a minute, then shows the error page', () => {
    let reloads: number[] = []
    for (const at of [0, 10_000, 20_000]) {
      const result = rendererGoneAction('crashed', reloads, at)
      expect(result.action).toBe('reload')
      if (result.action !== 'none') reloads = result.reloads
    }
    expect(rendererGoneAction('oom', reloads, 30_000)).toEqual({
      action: 'error-page',
      reloads: [0, 10_000, 20_000],
    })
  })

  it('forgets reloads older than a minute', () => {
    expect(rendererGoneAction('killed', [0, 1_000, 2_000], 61_500)).toEqual({
      action: 'reload',
      reloads: [2_000, 61_500],
    })
  })
})

describe('renderer crash page', () => {
  it('offers a reload and the logs', () => {
    const html = rendererCrashPageHtml()
    expect(html).toContain('id="reload"')
    expect(html).toContain('id="logs"')
    expect(html).not.toContain('id="retry"')
  })
})
