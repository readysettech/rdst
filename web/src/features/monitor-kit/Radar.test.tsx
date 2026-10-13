import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { readingClock } from '@/features/database/databaseModel'
import type { DatabaseActivity } from '@/lib/monitor/types'
import { Radar, radarHead, radarRows } from './Radar'

const motion = vi.hoisted(() => ({ reduced: false }))
vi.mock('@rs/ui-new/motion', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@rs/ui-new/motion')>()),
  useReducedMotion: () => motion.reduced,
}))

const NOW = 1_791_453_390
const analyzing: DatabaseActivity = {
  kind: 'analyzing',
  label: 'Analyzing SELECT on orders by status',
  done: 4,
  total: 10,
  last_reading_at: NOW - 30,
  every_s: 60,
  next_check_at: NOW + 170,
}

afterEach(() => {
  cleanup()
  motion.reduced = false
})

describe('radarHead and radarRows', () => {
  it('names the database and its state word', () => {
    expect(radarHead('watching', 'demo')).toBe('demo, actively monitoring')
    expect(radarHead('watching')).toBe('Actively monitoring')
    expect(radarHead('unreachable', 'demo')).toBe("demo, can't reach")
    expect(radarHead('off', 'demo')).toBe('demo, not monitored')
    expect(radarHead('paused', 'demo')).toBe('demo, AI checks paused')
  })

  it('says what runs now, the last reading, then the changes or the next check', () => {
    const clock = readingClock(NOW - 30)
    expect(
      radarRows('watching', analyzing, NOW, 10).map((row) => row.text)
    ).toEqual([
      'Analyzing SELECT on orders by status, 4 of 10',
      `Last reading ${clock}, every minute`,
      '10 changes to do in Actions',
    ])
    const reading: DatabaseActivity = {
      ...analyzing,
      kind: 'reading',
      label: 'Reading query statistics',
      done: null,
      total: null,
      every_s: 300,
    }
    expect(
      radarRows('watching', reading, NOW, 0).map((row) => row.text)
    ).toEqual([`Last reading ${clock}, every 5 minutes`, 'Next check in 3 min'])
    expect(
      radarRows('watching', { ...reading, next_check_at: NOW + 20 }, NOW).at(-1)
        ?.text
    ).toBe('Next check in under a minute')
    expect(
      radarRows('unreachable', reading, NOW).map((row) => row.text)
    ).toEqual([`Last reading ${clock}, every 5 minutes`])
    expect(
      radarRows('watching', { ...reading, every_s: 59 }, NOW)[0].text
    ).toBe(`Last reading ${clock}, every minute`)
    expect(radarRows('off', analyzing, NOW, 3)).toEqual([])
  })

  it('with AI checks paused, counts no first-hour analyses as done', () => {
    const idle: DatabaseActivity = {
      ...analyzing,
      kind: 'reading',
      label: 'Reading query statistics',
      done: 0,
      total: 10,
      next_check_at: null,
    }
    expect(radarRows('watching', idle, NOW).map((row) => row.text)).toContain(
      'Analyzed 0 of 10 queries'
    )
    expect(radarRows('paused', idle, NOW).map((row) => row.text)).toEqual([
      `Last reading ${readingClock(NOW - 30)}, every minute`,
    ])
  })
})

describe('Radar', () => {
  it('sweeps while monitoring is on and fills with the first hour', () => {
    render(<Radar state="watching" activity={analyzing} name="demo" />)
    const mark = screen.getByTestId('radar')
    expect(mark.dataset.state).toBe('watching')
    expect(mark.className).toContain('rdst-radar-sweeping')
    expect(mark.querySelector('.rdst-radar-sweep')).not.toBeNull()
    expect(mark.querySelectorAll('.rdst-radar-blip')).toHaveLength(3)
    expect(mark.style.getPropertyValue('--radar-fill')).toBe('144deg')
    expect(mark.getAttribute('aria-hidden')).toBe('true')
  })

  it('holds still and neutral when off, still and red when unreachable', () => {
    render(<Radar state="off" activity={analyzing} />)
    const off = screen.getByTestId('radar')
    expect(off.dataset.state).toBe('off')
    expect(off.className).not.toContain('rdst-radar-sweeping')
    expect(off.querySelector('.rdst-radar-sweep')).toBeNull()
    expect(screen.queryByTestId('radar-fill')).toBeNull()
    cleanup()
    render(<Radar state="unreachable" />)
    const down = screen.getByTestId('radar')
    expect(down.dataset.state).toBe('unreachable')
    expect(down.querySelector('.rdst-radar-blip')).toBeNull()
  })

  it('keeps the still ring under reduced motion', () => {
    motion.reduced = true
    render(<Radar state="watching" activity={analyzing} />)
    const mark = screen.getByTestId('radar')
    expect(mark.className).not.toContain('rdst-radar-sweeping')
    expect(mark.querySelector('.rdst-radar-ring')).not.toBeNull()
  })

  it('opens the tooltip on focus with the head and the rows', async () => {
    const activity = {
      ...analyzing,
      last_reading_at: Date.now() / 1000 - 30,
      next_check_at: Date.now() / 1000 + 170,
    }
    render(
      <Radar
        state="watching"
        activity={activity}
        name="demo"
        size="large"
        label="demo, Watching"
      />
    )
    const mark = screen.getByRole('img', { name: 'demo, Watching' })
    expect(mark.dataset.size).toBe('large')
    act(() => mark.focus())
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('demo, actively monitoring')
    expect(tip.textContent).toContain(
      'Analyzing SELECT on orders by status, 4 of 10'
    )
    expect(tip.textContent).toContain('every minute')
    expect(tip.textContent).toContain('Next check in 3 min')
  })

  it('adds the note under the rows', async () => {
    render(
      <Radar
        state="paused"
        name="demo"
        label="demo, AI checks paused"
        note="AI checks need a Readyset sign-in, even with your own key."
      />
    )
    act(() =>
      screen.getByRole('img', { name: 'demo, AI checks paused' }).focus()
    )
    const tip = await screen.findByRole('tooltip')
    expect(tip.textContent).toContain('demo, AI checks paused')
    expect(tip.textContent).toContain(
      'AI checks need a Readyset sign-in, even with your own key.'
    )
  })
})
