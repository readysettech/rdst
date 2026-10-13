import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { legacyTitle, Main } from './Main'

afterEach(cleanup)

describe('Main', () => {
  it('is the one rounded canvas, and scrolls the page in its own scroll area', () => {
    render(
      <Main layout="page">
        <div>Page content</div>
      </Main>
    )
    const main = document.getElementById('main-content') as HTMLElement
    expect(main.className).toContain('rounded-canvas')
    expect(main.className).toContain('overflow-hidden')
    expect(main.className).not.toContain('overflow-y-auto')
    expect(main.className).toContain('bg-surface-layout-1')
    const viewport = main.querySelector('[data-radix-scroll-area-viewport]')
    expect(viewport?.textContent).toBe('Page content')
    expect(screen.queryByText('Not found')).toBeNull()
  })

  it('starts a new page at the top and keeps the place on the same page', () => {
    const { rerender } = render(
      <Main layout="page" scrollKey="/">
        <div>Page content</div>
      </Main>
    )
    const viewport = document.querySelector(
      '#main-content [data-radix-scroll-area-viewport]'
    ) as HTMLElement
    viewport.scrollTop = 300
    rerender(
      <Main layout="page" scrollKey="/">
        <div>Page content</div>
      </Main>
    )
    expect(viewport.scrollTop).toBe(300)
    rerender(
      <Main layout="page" scrollKey="/help">
        <div>Page content</div>
      </Main>
    )
    expect(viewport.scrollTop).toBe(0)
  })

  it('gives a page that still carries its own heading a bar and the content axis', () => {
    render(
      <Main layout="legacy" fallbackTitle="Queries">
        <h1>Queries</h1>
      </Main>
    )
    expect(screen.getAllByText('Queries')).toHaveLength(2)
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
    expect(document.querySelector('[data-page-bar]')).toBeTruthy()
  })

  it('leaves a split route the bare area, so its panels scroll and the page never does', () => {
    render(
      <Main layout="split">
        <div>Two panes</div>
      </Main>
    )
    const main = document.getElementById('main-content') as HTMLElement
    expect(main.className).not.toContain('overflow-y-auto')
    expect(main.querySelector('[data-radix-scroll-area-viewport]')).toBeNull()
    expect(main.className).not.toContain('rounded-canvas')
    expect(main.className).toContain('min-h-0')
  })
})

describe('legacyTitle', () => {
  it('names the pages the shell still titles', () => {
    expect(legacyTitle('/guards')).toBe('Guards')
    expect(legacyTitle('/database/')).toBe('Database')
    expect(legacyTitle('/scan')).toBe('Code scan')
    expect(legacyTitle('/nowhere')).toBe('Not found')
  })
})
