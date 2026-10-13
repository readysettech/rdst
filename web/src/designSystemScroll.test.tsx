// Every scrolling surface of the redesigned app scrolls in the design-system scroll area
// (a styled, auto-hiding scrollbar), never in a native overflow container, and each panel
// keeps its own scroll while the page itself never scrolls.
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { Scrollable } from '@rs/ui-new/scrollable'
import { SidePane } from '@rs/ui-new/side-pane'
import { TwoPane } from '@rs/ui-new/two-pane'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DialogShell } from '@/components/DialogShell'
import { ScrollDialog } from '@/features/monitor-kit/ScrollDialog'

afterEach(cleanup)

const VIEWPORT = '[data-radix-scroll-area-viewport]'
const viewportOf = (node: Element) =>
  node.closest(VIEWPORT) as HTMLElement | null

describe('Scrollable', () => {
  it('hands out its viewport and splits root and viewport classes', () => {
    const ref = createRef<HTMLDivElement>()
    render(
      <Scrollable
        rootClassName="min-h-0 flex-1"
        className="px-6"
        viewportRef={ref}
        data-testid="body"
      >
        <p>Row</p>
      </Scrollable>
    )
    const viewport = screen.getByTestId('body')
    expect(viewport.matches(VIEWPORT)).toBe(true)
    expect(ref.current).toBe(viewport)
    expect(viewport.className).toContain('px-6')
    const root = viewport.parentElement as HTMLElement
    expect(root.className).toContain('min-h-0')
    expect(root.className).toContain('overflow-hidden')
    expect(viewportOf(screen.getByText('Row'))).toBe(viewport)
  })

  it('keeps the clipping root at the origin when something scrolls it', () => {
    render(
      <Scrollable data-testid="body">
        <p>Row</p>
      </Scrollable>
    )
    const root = screen.getByTestId('body').parentElement as HTMLElement
    root.scrollTop = 40
    fireEvent.scroll(root)
    expect(root.scrollTop).toBe(0)
  })
})

describe('TwoPane', () => {
  it('scrolls the first panel in a scroll area and reserves no gap while closed', () => {
    const { rerender } = render(
      <TwoPane
        open={false}
        primary={<p>Main list</p>}
        secondary={<p>Detail</p>}
      />
    )
    const pane = document.querySelector('[data-two-pane]') as HTMLElement
    const [first, second] = [...pane.children] as HTMLElement[]
    expect(first.className).toContain('overflow-hidden')
    expect(first.className).not.toContain('overflow-y-auto')
    expect(first.contains(viewportOf(screen.getByText('Main list')))).toBe(true)
    expect(second.className).toContain('ml-0')
    expect(screen.queryByText('Detail')).toBeNull()
    rerender(
      <TwoPane open primary={<p>Main list</p>} secondary={<p>Detail</p>} />
    )
    expect(second.className).toContain('ml-3')
    expect(second.className).toContain('transition-[flex-basis,margin-left')
    expect(screen.getByText('Detail')).toBeTruthy()
  })

  it('leaves a first panel with its own fixed header to scroll its body', () => {
    render(
      <TwoPane
        primaryScrolls={false}
        primaryWidth={480}
        primary={<p>Queue</p>}
        secondary={<p>Detail</p>}
      />
    )
    expect(viewportOf(screen.getByText('Queue'))).toBeNull()
  })
})

describe('SidePane', () => {
  it('keeps the header fixed and scrolls the body in a scroll area', () => {
    render(
      <SidePane aria-label="Query" onClose={vi.fn()}>
        <p>Pane body</p>
      </SidePane>
    )
    const viewport = viewportOf(screen.getByText('Pane body')) as HTMLElement
    expect(viewport).toBeTruthy()
    expect(viewport.parentElement?.className).toContain('flex-1')
    expect(
      viewport.contains(screen.getByRole('button', { name: 'Close' }))
    ).toBe(false)
  })
})

describe('dialog bodies', () => {
  it('ConfirmDialog, quiet: the body scrolls, the actions stay, Escape still closes', () => {
    const onClose = vi.fn()
    render(
      <ConfirmDialog
        tone="quiet"
        isOpen
        onClose={onClose}
        onConfirm={vi.fn()}
        title="Shut down the demo?"
        notice={{ icon: 'trash', message: 'Removes the four containers.' }}
        confirmLabel="Shut down"
        cancelLabel="Keep running"
      />
    )
    const viewport = viewportOf(
      screen.getByText('Removes the four containers.')
    )
    expect(viewport).toBeTruthy()
    const cancel = screen.getByRole('button', { name: 'Keep running' })
    expect(viewport?.contains(cancel)).toBe(false)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(
      true
    )
    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('DialogShell: the body scrolls between the title and the footer', () => {
    const onClose = vi.fn()
    render(
      <DialogShell
        open
        onClose={onClose}
        title="Analyze a query"
        description="Analyze one query."
        note="Runs once."
        action={<span>Actions</span>}
      >
        <p>Dialog body</p>
      </DialogShell>
    )
    const viewport = viewportOf(screen.getByText('Dialog body'))
    expect(viewport).toBeTruthy()
    expect(viewport?.contains(screen.getByText('Runs once.'))).toBe(false)
    expect(
      viewport?.contains(
        screen.getByRole('heading', { name: 'Analyze a query' })
      )
    ).toBe(false)
    expect(screen.getByRole('dialog').contains(document.activeElement)).toBe(
      true
    )
    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('ScrollDialog: the body test id names the scrolling viewport', () => {
    render(
      <ScrollDialog
        open
        onClose={vi.fn()}
        title="Fixes"
        description="The fixes to try."
        footer={<span>Footer</span>}
        testId="fixes"
      >
        <p>Fix list</p>
      </ScrollDialog>
    )
    const body = screen.getByTestId('fixes-body')
    expect(body.matches(VIEWPORT)).toBe(true)
    expect(body.contains(screen.getByText('Footer'))).toBe(false)
  })
})

describe('redesigned surfaces', () => {
  const SOURCES = [
    'layout/Main.tsx',
    'features/inbox/InboxPage.tsx',
    'features/inbox/InboxDetail.tsx',
    'features/database/settings/MonitoringSettingsPane.tsx',
    'features/database/queries/SqlCode.tsx',
    'features/queries/results/AnalysisCards.tsx',
    'features/monitor-kit/ScrollDialog.tsx',
    'features/prerequisites/FixPanel.tsx',
    'components/DialogShell.tsx',
    'components/top/ParameterDialog.tsx',
    'components/configure/AddDatabaseDialog.tsx',
    'components/configure/SshFields.tsx',
    'components/TrialRegistrationDialog.tsx',
    '../../../packages/ui-new/src/components/element/two-pane.tsx',
    '../../../packages/ui-new/src/components/element/side-pane.tsx',
  ]

  it.each(SOURCES)('%s has no native scroll container', (path) => {
    const here = dirname(fileURLToPath(import.meta.url))
    const source = readFileSync(join(here, path), 'utf8')
    expect(source).not.toMatch(
      /overflow-(x-|y-)?(auto|scroll)\b|overflow[XY]?: ?'(auto|scroll)'/
    )
  })
})
