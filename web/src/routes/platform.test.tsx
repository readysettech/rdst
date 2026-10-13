import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', async () => {
  const { fileRouteModuleMock } = await import('@/test-utils')
  return fileRouteModuleMock({
    redirect: (options: unknown) => ({ redirect: options }),
  })
})

vi.mock('../features/platform/PlatformPage', () => ({
  PlatformPage: () => null,
}))

import { Route as DemoRoute } from './demo'
import { Route as PlatformRoute } from './platform'

describe('Readyset Platform route', () => {
  it('renders the platform page', () => {
    expect(
      (PlatformRoute as unknown as { component: unknown }).component
    ).toBeTypeOf('function')
  })

  it('keeps the old demo URL working by redirecting to /platform', () => {
    const beforeLoad = (DemoRoute as unknown as { beforeLoad: () => void })
      .beforeLoad
    let thrown: unknown
    try {
      beforeLoad()
    } catch (error) {
      thrown = error
    }
    expect(JSON.stringify(thrown)).toContain('/platform')
  })
})
