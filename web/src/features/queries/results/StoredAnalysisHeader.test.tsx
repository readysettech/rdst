import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { AnalysisHistoryEntry } from '../../../lib/api'
import { StoredAnalysisHeader } from './StoredAnalysisHeader'

afterEach(cleanup)

beforeAll(() => {
  const proto = HTMLElement.prototype as unknown as {
    hasPointerCapture: () => boolean
    setPointerCapture: () => void
    releasePointerCapture: () => void
    scrollIntoView: () => void
  }
  proto.hasPointerCapture = () => false
  proto.setPointerCapture = () => {}
  proto.releasePointerCapture = () => {}
  proto.scrollIntoView = () => {}
})

const ago = (ms: number) => new Date(Date.now() - ms).toISOString()
const HOUR = 3_600_000
const DAY = 86_400_000

function historyEntry(
  overrides: Partial<AnalysisHistoryEntry> = {}
): AnalysisHistoryEntry {
  return {
    analysis_id: 'an-1',
    created_at: ago(2 * HOUR),
    target: 'imdb',
    overall_rating: 'good',
    efficiency_score: 82,
    ...overrides,
  }
}

function renderHeader(
  props: Partial<Parameters<typeof StoredAnalysisHeader>[0]> = {}
) {
  const onReRun = vi.fn()
  const onOpenAnalysis = vi.fn()
  render(
    <StoredAnalysisHeader
      createdAt={ago(2 * HOUR)}
      target="imdb"
      overallRating="good"
      efficiencyScore={82}
      hasBody
      history={[historyEntry()]}
      currentAnalysisId="an-1"
      onOpenAnalysis={onOpenAnalysis}
      onReRun={onReRun}
      {...props}
    />
  )
  return { onReRun, onOpenAnalysis }
}

describe('StoredAnalysisHeader', () => {
  it('says when the analysis ran and what it concluded', () => {
    renderHeader()

    expect(screen.getByText('Viewing analysis from 2 hours ago')).toBeTruthy()
    expect(screen.getByText('Good · 82/100')).toBeTruthy()
  })

  it('states staleness in plain words next to the re-run action', () => {
    const { onReRun } = renderHeader({ createdAt: ago(3 * DAY) })

    expect(
      screen.getByText(
        'Your data and query plans may have changed since this ran.'
      )
    ).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Re-run analysis' }))
    expect(onReRun).toHaveBeenCalledWith('stale')
  })

  it('calls a re-run of a fresh analysis a deliberate choice', () => {
    const { onReRun } = renderHeader()

    fireEvent.click(screen.getByRole('button', { name: 'Re-run analysis' }))
    expect(onReRun).toHaveBeenCalledWith('manual')
  })

  it('attributes a re-run to the missing body when one was never stored', () => {
    const { onReRun } = renderHeader({ hasBody: false })

    fireEvent.click(screen.getByRole('button', { name: 'Re-run analysis' }))
    expect(onReRun).toHaveBeenCalledWith('missing-body')
  })

  it('offers no history affordance when this is the only stored run', () => {
    renderHeader()

    expect(
      screen.queryByRole('button', { name: 'Analysis history' })
    ).toBeNull()
  })

  it('lists earlier runs and opens the one chosen', async () => {
    const older = historyEntry({
      analysis_id: 'an-0',
      created_at: ago(3 * DAY),
      overall_rating: 'fair',
      efficiency_score: 61,
    })
    const { onOpenAnalysis } = renderHeader({
      history: [historyEntry(), older],
    })

    fireEvent.keyDown(
      screen.getByRole('button', { name: 'Analysis history' }),
      { key: 'Enter' }
    )
    const olderItem = await screen.findByRole('menuitem', {
      name: /3 days ago · Fair · 61\/100/,
    })
    fireEvent.click(olderItem)
    expect(onOpenAnalysis).toHaveBeenCalledWith('an-0')
  })
})
