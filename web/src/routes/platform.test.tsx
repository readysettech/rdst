import type { ReactElement } from 'react'
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

  it('shows a desktop-app notice instead of the demo in a container', async () => {
    const { render, screen } = await import('@testing-library/react')
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    try {
      // The route declares its component as a split-module loader.
      const load = (
        PlatformRoute as unknown as {
          component: () => Promise<{ component: () => ReactElement }>
        }
      ).component
      const Component = (await load()).component
      render(<Component />)
      expect(
        await screen.findByText(
          'The Readyset Platform demo runs in the desktop app'
        )
      ).toBeTruthy()
    } finally {
      meta.remove()
    }
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
