// One focus treatment, on every primitive a keyboard user can land on, and no
// transition that would let the ring fade in behind the caret. [MG-04, MG-05]
import { Button } from '@rs/ui-new/button'
import { DividedList, ListRow } from '@rs/ui-new/divided-list'
import { controlTransition, focusRing } from '@rs/ui-new/focus'
import { InteractiveRow } from '@rs/ui-new/interactive-row'
import { Metric, MetricRow } from '@rs/ui-new/metric-row'
import { Pressable } from '@rs/ui-new/pressable'
import { SidePane } from '@rs/ui-new/side-pane'
import { TabItemButton, TabList } from '@rs/ui-new/tab'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

afterEach(cleanup)

const classesOf = (element: HTMLElement) => element.className.split(/\s+/)

const expectFocusRing = (element: HTMLElement) => {
  const classes = classesOf(element)
  for (const utility of focusRing) {
    // The ring offset is overridden per primitive; the ring itself is not.
    if (utility.startsWith('focus-visible:ring-offset-')) continue
    expect(classes).toContain(utility)
  }
  expect(classes.some((c) => c.startsWith('focus-visible:ring-offset-'))).toBe(
    true
  )
}

const expectRingNotTransitioned = (element: HTMLElement) => {
  const classes = classesOf(element)
  expect(classes).not.toContain('transition')
  expect(classes).not.toContain('transition-all')
  expect(classes).not.toContain('transition-shadow')
  for (const c of classes) {
    if (c.startsWith('transition-[')) expect(c).not.toContain('box-shadow')
  }
}

describe('shared focus ring', () => {
  it('is the same set of utilities everywhere', () => {
    expect(focusRing).toContain('focus-visible:outline-none')
    expect(focusRing).toContain('focus-visible:ring-2')
    expect(controlTransition).not.toContain('box-shadow')
    expect(controlTransition).not.toContain('outline')
  })

  it('is on Button, and Button does not animate it', () => {
    render(<Button label="Run analysis" />)
    const button = screen.getByRole('button', { name: 'Run analysis' })
    expectFocusRing(button)
    expectRingNotTransitioned(button)
  })

  it('is on Pressable', () => {
    render(<Pressable>Option</Pressable>)
    expectFocusRing(screen.getByRole('button', { name: 'Option' }))
  })

  it('is on InteractiveRow, and InteractiveRow does not animate it', () => {
    render(
      <InteractiveRow label="Open e2e-guard report">
        <span>row</span>
      </InteractiveRow>
    )
    const row = screen.getByRole('button', { name: 'Open e2e-guard report' })
    expectFocusRing(row)
    expectRingNotTransitioned(row)
  })

  it('is on TabItemButton, and the tab does not animate it', () => {
    render(
      <TabList aria-label="Settings sections">
        <TabItemButton
          label="Connections"
          layoutPrefix="settings"
          active
          onClick={() => {}}
        />
      </TabList>
    )
    const tab = screen.getByRole('tab', { name: 'Connections' })
    expectFocusRing(tab)
    expectRingNotTransitioned(tab)
    // `outline-none` unconditionally would suppress the ring for every input
    // modality, which is how the tabs came to have no indicator at all.
    expect(classesOf(tab)).not.toContain('outline-none')
  })

  it('is on an interactive ListRow, on the link the row renders', () => {
    render(
      <DividedList columns="1fr auto">
        <ListRow asChild interactive>
          <a href="/inbox">Stuck query filed</a>
        </ListRow>
        <ListRow>
          <span>Monitoring starts</span>
        </ListRow>
      </DividedList>
    )
    const row = screen.getByRole('link', { name: 'Stuck query filed' })
    expectFocusRing(row)
    expectRingNotTransitioned(row)
    expect(
      screen.getByText('Monitoring starts').parentElement?.className
    ).not.toContain('focus-visible:ring-2')
  })

  it('is on a selectable Metric, which is a button', () => {
    render(
      <MetricRow>
        <Metric
          label="Queries per second"
          value="11.7"
          onSelect={() => {}}
          selected
        />
      </MetricRow>
    )
    const metric = screen.getByRole('button', { name: /Queries per second/ })
    expectFocusRing(metric)
    expect(metric.getAttribute('aria-pressed')).toBe('true')
  })

  it('is on the side pane controls', () => {
    render(
      <SidePane
        aria-label="Stuck query on demo"
        onPrev={() => {}}
        onNext={() => {}}
        onClose={() => {}}
      >
        <p>body</p>
      </SidePane>
    )
    for (const name of ['Previous', 'Next', 'Close'])
      expectFocusRing(screen.getByRole('button', { name }))
  })
})
