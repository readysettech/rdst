import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { inboxItems } from '@/lib/monitor/fixtures'
import { Occurrences, OnsetClusters } from './OnsetClusters'
import { burstFrame, type MemberJump } from './onsetClusterModel'

const START = new Date(2026, 8, 29, 15, 4).getTime() / 1000

const jump = (index: number): MemberJump => ({
  key: `key${String(index).padStart(3, '0')}`,
  name: `Query ${index}`,
  start: START + (index % 2) * 60,
  end: null,
  metric: 'calls',
  kind: 'jumped',
  before: 0.01,
  after: 0.08,
  change: 7,
  onset: null,
})

function setup() {
  const onOpenFrame = vi.fn()
  const jumps = Array.from({ length: 25 }, (_, index) => jump(index))
  render(
    <OnsetClusters
      item={{ ...inboxItems(START)[0], id: 57, target: 'demoshop' }}
      jumps={jumps}
      frame={burstFrame(jumps)}
      hrefFor={(key) => `/database?query=${key}`}
      onOpen={vi.fn()}
      onOpenFrame={onOpenFrame}
    />
  )
  return { onOpenFrame }
}

afterEach(cleanup)

const rows = () =>
  within(
    screen.getByRole('region', { name: 'Queries in this burst' })
  ).getAllByRole('listitem')

const unfold = () =>
  fireEvent.click(screen.getByRole('button', { name: /^Show the 25 queries/ }))

describe('OnsetClusters', () => {
  it('starts folded, then pages members ten at a time', () => {
    setup()
    expect(screen.queryAllByRole('listitem')).toHaveLength(0)
    unfold()
    expect(rows()).toHaveLength(10)
    fireEvent.click(screen.getByRole('button', { name: /Show 10 more/ }))
    expect(rows()).toHaveLength(20)
    fireEvent.click(screen.getByRole('button', { name: /Show 5 more/ }))
    expect(rows()).toHaveLength(25)
    expect(screen.queryByRole('button', { name: /more/ })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Show fewer/ }))
    expect(rows()).toHaveLength(10)
  })

  it('shows every member its before, after and size', () => {
    setup()
    unfold()
    for (const row of rows()) {
      expect(within(row).getByText('36 an hour')).toBeTruthy()
      expect(within(row).getByText('4.8 a minute')).toBeTruthy()
      expect(within(row).getByText('8x')).toBeTruthy()
    }
  })

  it("links the burst's time frame on the Database page while folded", () => {
    const { onOpenFrame } = setup()
    const link = screen.getByRole('link', { name: /^Open the Database page/ })
    const href = new URL(link.getAttribute('href') ?? '', 'http://x')
    expect(href.pathname).toBe('/database')
    expect(href.searchParams.get('target')).toBe('demoshop')
    expect(href.searchParams.get('from')).toBe('inbox:57')
    expect(href.searchParams.get('zoom')).toMatch(/^\d+-\d+$/)
    fireEvent.click(link)
    expect(onOpenFrame).toHaveBeenCalledTimes(1)
  })
})

describe('Occurrences', () => {
  const entries = [
    {
      start: START + 7200,
      end: null,
      size: 6.2,
      query_count: 4,
      quiet: false,
      verdict: 'bigger' as const,
    },
    {
      start: START + 3600,
      end: START + 4200,
      size: 3,
      query_count: 4,
      quiet: true,
      verdict: 'usual' as const,
    },
    {
      start: START,
      end: START + 600,
      size: 3.1,
      query_count: 3,
      quiet: false,
      verdict: null,
    },
  ]

  it('lists each time folded, newest first, each with its own time frame link', () => {
    const onOpenFrame = vi.fn()
    render(
      <Occurrences
        item={{ ...inboxItems(START)[0], id: 58, target: 'demoshop' }}
        entries={entries}
        onOpenFrame={onOpenFrame}
      />
    )
    const region = screen.getByRole('region', { name: 'Each time it happened' })
    expect(within(region).queryAllByRole('listitem')).toHaveLength(0)
    fireEvent.click(
      within(region).getByRole('button', { name: /Show the 3 times/ })
    )
    const items = within(region).getAllByRole('listitem')
    expect(items).toHaveLength(3)
    expect(items[0].textContent).toMatch(/still going/)
    expect(items[0].textContent).toMatch(/6\.2x.*Bigger than usual/)
    expect(items[1].textContent).toMatch(/Usual/)
    const links = within(region).getAllByRole('link', {
      name: /^Open the Database page/,
    })
    expect(links).toHaveLength(3)
    expect(
      new URL(links[2].getAttribute('href') ?? '', 'http://x').searchParams.get(
        'from'
      )
    ).toBe('inbox:58')
    fireEvent.click(links[1])
    expect(onOpenFrame).toHaveBeenCalledWith({
      start: START + 3600,
      last: START + 4200,
    })
  })
})

it('omits still going on recovered occurrences with stale end times', () => {
  render(
    <Occurrences
      item={{ ...inboxItems(START)[0], severity_band: 'recovered' }}
      entries={[
        {
          start: START,
          end: null,
          size: 2,
          query_count: 1,
          quiet: false,
          verdict: null,
        },
        {
          start: START - 600,
          end: START - 300,
          size: 2,
          query_count: 1,
          quiet: false,
          verdict: null,
        },
      ]}
      onOpenFrame={vi.fn()}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: /Show the 2 times/ }))
  expect(
    screen.getByRole('region', { name: 'Each time it happened' }).textContent
  ).not.toMatch(/still going/)
})

it('omits still going on members of a recovered incident', () => {
  const jumps = [jump(1)]
  render(
    <OnsetClusters
      item={{ ...inboxItems(START)[0], severity_band: 'recovered' }}
      jumps={jumps}
      frame={burstFrame(jumps)}
      hrefFor={() => '/database'}
      onOpen={vi.fn()}
      onOpenFrame={vi.fn()}
    />
  )
  fireEvent.click(screen.getByRole('button', { name: /Show the 1 query/ }))
  expect(screen.queryByText(/still going/)).toBeNull()
})
