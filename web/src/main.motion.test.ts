// The reduced-motion contract lives at the composition root, where nothing can
// render it. Reading the entry module keeps the guarantee under test: every
// `m.*` in the product inherits the user's motion preference from one place.
import { describe, expect, it } from 'vitest'
import mainSource from './main.tsx?raw'

describe('app root motion configuration', () => {
  it('declares MotionConfig with reducedMotion="user"', () => {
    expect(mainSource).toContain('<MotionConfig reducedMotion="user">')
  })

  it('mounts MotionConfig inside LazyMotion, around the router', () => {
    const lazyMotion = mainSource.indexOf('<LazyMotion')
    const motionConfig = mainSource.indexOf('<MotionConfig')
    const router = mainSource.indexOf('<RouterProvider')
    expect(lazyMotion).toBeGreaterThan(-1)
    expect(motionConfig).toBeGreaterThan(lazyMotion)
    expect(router).toBeGreaterThan(motionConfig)
  })
})
