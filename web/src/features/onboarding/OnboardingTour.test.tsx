import { act, cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { claimFirstRunPopup } from '../../components/GetStarted'
import { trackEventOnce } from '../../lib/analytics'
import { useSystemStatus } from '../../lib/useSystemStatus'
import {
  OnboardingTour,
  OnboardingTourHost,
  TOUR_STEPS,
} from './OnboardingTour'
import {
  clearTourStep,
  markTourDone,
  openTour,
  savedTourStep,
  saveTourStep,
  tourPending,
} from './onboardingState'

const openAddDatabase = vi.fn()
vi.mock('../../components/configure/addDatabase', async (importOriginal) => ({
  ...(await importOriginal<
    typeof import('../../components/configure/addDatabase')
  >()),
  useOpenAddDatabase: () => openAddDatabase,
}))
vi.mock('../../lib/useSystemStatus', () => ({ useSystemStatus: vi.fn() }))
const reducedMotion = vi.hoisted(() => ({ value: false }))
vi.mock('@rs/ui-new/motion', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@rs/ui-new/motion')>()),
  useReducedMotion: () => reducedMotion.value,
}))
vi.mock('../../lib/analytics', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/analytics')>()),
  trackEventOnce: vi.fn(),
}))

// jsdom has no media playback; the tour only needs play and pause to be callable.
const played: HTMLMediaElement[] = []
const play = vi.fn(function (this: HTMLMediaElement) {
  played.push(this)
  return Promise.resolve()
})
const pause = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  localStorage.clear()
  sessionStorage.clear()
  reducedMotion.value = false
  played.length = 0
  clearTourStep()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play)
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(pause)
  vi.mocked(useSystemStatus).mockReturnValue({ data: { targets: [] } } as never)
})
afterEach(cleanup)

const next = () => screen.getByTestId('onboarding-next')
const video = () => screen.getByTestId('onboarding-video') as HTMLVideoElement

describe('OnboardingTour', () => {
  it('pages through the four steps with Next and Back', () => {
    const onFinish = vi.fn()
    renderWithClient(<OnboardingTour open onFinish={onFinish} />)
    expect(
      screen.getByRole('heading', { name: 'Connect your database' })
    ).toBeTruthy()
    expect(screen.getByText('Step 1 of 4')).toBeTruthy()

    fireEvent.click(next())
    expect(screen.getByText('Step 2 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByText('Step 1 of 4')).toBeTruthy()

    fireEvent.click(
      screen.getByRole('button', { name: `Step 4: ${TOUR_STEPS[3].title}` })
    )
    expect(screen.getByText('Step 4 of 4')).toBeTruthy()
    expect(next().textContent).toContain('Add a database')
    expect(screen.queryByRole('button', { name: 'Skip' })).toBeNull()
    fireEvent.click(next())
    expect(onFinish).toHaveBeenCalledWith('done', 3)
  })

  it('reports each step viewed and completed', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    expect(trackEventOnce).toHaveBeenCalledWith(
      'tour-viewed-tour-connect',
      'onboarding_step_viewed',
      { step: 'tour-connect' }
    )
    fireEvent.click(next())
    expect(trackEventOnce).toHaveBeenCalledWith(
      'tour-completed-tour-connect',
      'onboarding_step_completed',
      { step: 'tour-connect' }
    )
    expect(trackEventOnce).toHaveBeenCalledWith(
      'tour-viewed-tour-watch',
      'onboarding_step_viewed',
      { step: 'tour-watch' }
    )
  })

  it('links to how data is handled and the privacy policy on every step', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    for (let i = 0; i < TOUR_STEPS.length; i += 1) {
      expect(
        screen
          .getByRole('link', { name: 'How your data is handled' })
          .getAttribute('href')
      ).toBe('https://readyset.io/docs/readyset-ai/rdst/desktop/privacy')
      expect(
        screen
          .getByRole('link', { name: 'Privacy policy' })
          .getAttribute('href')
      ).toBe('https://readyset.io/privacy-policy')
      if (i < TOUR_STEPS.length - 1) fireEvent.click(next())
    }
  })

  it('says who is behind RDST on the first step only, with no trust callouts', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    expect(screen.getByTestId('authority-note')).toBeTruthy()
    for (let i = 1; i < TOUR_STEPS.length; i += 1) {
      fireEvent.click(next())
      expect(screen.queryByTestId('authority-note')).toBeNull()
    }
    expect(screen.queryByText(/keyring/i)).toBeNull()
    expect(screen.queryByText(/You decide what runs/)).toBeNull()
  })

  it("doesn't pitch a coding agent beside a server-mode RDST", () => {
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    try {
      renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
      expect(screen.getByText(/search its cluster/)).toBeTruthy()
      expect(screen.queryByText(/hand the job to Claude/)).toBeNull()
    } finally {
      meta.remove()
    }
  })

  it('shows a still instead of the desktop agent video in server mode', () => {
    const meta = document.createElement('meta')
    meta.name = 'rdst-runtime'
    meta.content = 'container'
    document.head.appendChild(meta)
    try {
      renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
      expect(screen.getByTestId('onboarding-server-still')).toBeTruthy()
      expect(screen.queryByTestId('onboarding-video')).toBeNull()
      expect(screen.getByText('Search this cluster')).toBeTruthy()
      fireEvent.click(screen.getByTestId('onboarding-next'))
      act(() => {})
      expect(screen.getByTestId('onboarding-video')).toBeTruthy()
    } finally {
      meta.remove()
    }
  })

  it('ends on asking a coding agent what RDST found', () => {
    expect(TOUR_STEPS.map((step) => step.key)).toEqual([
      'tour-connect',
      'tour-watch',
      'tour-inbox',
      'tour-agent',
    ])
    expect(TOUR_STEPS[3].title).toBe('Ask your coding agent what RDST found')
  })

  it("plays each step's video, muted and inline, with its poster", () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    for (const step of TOUR_STEPS) {
      const element = video()
      expect(element.getAttribute('src')).toMatch(
        new RegExp(`^/onboarding/${step.video}\\.webm$`)
      )
      expect(element.getAttribute('poster')).toBe(
        `/onboarding/${step.video}.png`
      )
      expect(element.muted).toBe(true)
      expect(element.hasAttribute('playsinline')).toBe(true)
      expect(played).toContain(element)
      if (step !== TOUR_STEPS[TOUR_STEPS.length - 1]) fireEvent.click(next())
    }
  })

  it('moves on when a video ends and holds on the last one', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    fireEvent.ended(video())
    expect(screen.getByText('Step 2 of 4')).toBeTruthy()
    expect(trackEventOnce).toHaveBeenCalledWith(
      'tour-completed-tour-connect',
      'onboarding_step_completed',
      { step: 'tour-connect' }
    )
    fireEvent.click(
      screen.getByRole('button', { name: `Step 4: ${TOUR_STEPS[3].title}` })
    )
    fireEvent.ended(video())
    expect(screen.getByText('Step 4 of 4')).toBeTruthy()
  })

  it('pauses and resumes the video', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    play.mockClear()
    fireEvent.click(screen.getByRole('button', { name: 'Pause' }))
    expect(pause).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(play).toHaveBeenCalled()
  })

  it('falls back to the live scene when a video fails', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    fireEvent.error(video())
    expect(screen.queryByTestId('onboarding-video')).toBeNull()
    expect(screen.getByText('Add a database')).toBeTruthy()
    fireEvent.click(next())
    expect(screen.getByTestId('onboarding-video')).toBeTruthy()
  })

  it('waits on the poster under reduced motion until Play', () => {
    reducedMotion.value = true
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    expect(video().getAttribute('poster')).toBe('/onboarding/tour-connect.png')
    expect(play).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Play' }))
    expect(play).toHaveBeenCalled()
  })

  it('opens at the step it is given', () => {
    renderWithClient(<OnboardingTour open start={2} onFinish={vi.fn()} />)
    expect(screen.getByText('Step 3 of 4')).toBeTruthy()
  })

  it('skips', () => {
    const onFinish = vi.fn()
    renderWithClient(<OnboardingTour open onFinish={onFinish} />)
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(onFinish).toHaveBeenCalledWith('skip', 0)
  })

  it('pages with the arrow keys', () => {
    renderWithClient(<OnboardingTour open onFinish={vi.fn()} />)
    fireEvent.keyDown(screen.getByTestId('onboarding-tour'), {
      key: 'ArrowRight',
    })
    expect(screen.getByText('Step 2 of 4')).toBeTruthy()
    fireEvent.keyDown(screen.getByTestId('onboarding-tour'), {
      key: 'ArrowLeft',
    })
    expect(screen.getByText('Step 1 of 4')).toBeTruthy()
  })
})

describe('OnboardingTourHost', () => {
  it('shows on a new install, then opens the add-database dialog on the agent prompt', async () => {
    renderWithClient(<OnboardingTourHost />)
    expect(await screen.findByTestId('onboarding-tour')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(openAddDatabase).toHaveBeenCalledWith('agent')
    expect(tourPending()).toBe(false)
  })

  it('stays away once a database exists', () => {
    vi.mocked(useSystemStatus).mockReturnValue({
      data: { targets: [{ name: 'shop' }] },
    } as never)
    renderWithClient(<OnboardingTourHost />)
    expect(screen.queryByTestId('onboarding-tour')).toBeNull()
    expect(tourPending()).toBe(false)
  })

  it('saves the step when closed part way and resumes there', async () => {
    markTourDone()
    renderWithClient(<OnboardingTourHost />)
    act(() => openTour())
    expect(await screen.findByTestId('onboarding-tour')).toBeTruthy()
    fireEvent.click(next())
    fireEvent.click(next())
    expect(screen.getByText('Step 3 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))
    expect(savedTourStep()).toBe(2)
    expect(openAddDatabase).not.toHaveBeenCalled()

    act(() => openTour(savedTourStep() ?? 0))
    expect(await screen.findByText('Step 3 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(savedTourStep()).toBeNull()
  })

  it('opens the add-database dialog once when a resumed first run is skipped', async () => {
    renderWithClient(<OnboardingTourHost />)
    expect(await screen.findByTestId('onboarding-tour')).toBeTruthy()
    fireEvent.click(next())
    fireEvent.click(screen.getByRole('button', { name: 'Close modal' }))
    expect(savedTourStep()).toBe(1)
    expect(openAddDatabase).not.toHaveBeenCalled()

    act(() => openTour(savedTourStep() ?? 0))
    expect(await screen.findByText('Step 2 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(openAddDatabase).toHaveBeenCalledTimes(1)
    // Home's own first-run pop-up doesn't open it again.
    expect(claimFirstRunPopup('add-database')).toBe(false)
  })

  it('opens the add-database dialog when a resumed tour is finished', async () => {
    markTourDone()
    saveTourStep(3)
    renderWithClient(<OnboardingTourHost />)
    act(() => openTour(3))
    expect(await screen.findByText('Step 4 of 4')).toBeTruthy()
    fireEvent.click(next())
    expect(openAddDatabase).toHaveBeenCalledTimes(1)
  })

  it('leaves the dialog closed after a resumed tour once a database exists', async () => {
    vi.mocked(useSystemStatus).mockReturnValue({
      data: { targets: [{ name: 'shop' }] },
    } as never)
    saveTourStep(1)
    renderWithClient(<OnboardingTourHost />)
    act(() => openTour(1))
    expect(await screen.findByText('Step 2 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(openAddDatabase).not.toHaveBeenCalled()
  })

  it('clears a saved step when the tour is finished', async () => {
    markTourDone()
    saveTourStep(3)
    renderWithClient(<OnboardingTourHost />)
    act(() => openTour(3))
    expect(await screen.findByText('Step 4 of 4')).toBeTruthy()
    fireEvent.click(next())
    expect(savedTourStep()).toBeNull()
  })

  it("opens the add-database dialog from a replay's last step", async () => {
    vi.mocked(useSystemStatus).mockReturnValue({
      data: { targets: [{ name: 'shop' }] },
    } as never)
    markTourDone()
    renderWithClient(<OnboardingTourHost />)
    act(() => openTour(3))
    expect(await screen.findByText('Step 4 of 4')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Add a database' }))
    expect(openAddDatabase).toHaveBeenCalledWith('agent')
  })

  it('replays on request without opening the add-database dialog', async () => {
    markTourDone()
    renderWithClient(<OnboardingTourHost />)
    expect(screen.queryByTestId('onboarding-tour')).toBeNull()
    act(() => openTour())
    expect(await screen.findByTestId('onboarding-tour')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(openAddDatabase).not.toHaveBeenCalled()
  })
})

describe('first-run add-database pop-up', () => {
  it('waits for the tour on a new install', () => {
    expect(claimFirstRunPopup('add-database')).toBe(false)
    markTourDone()
    expect(claimFirstRunPopup('add-database')).toBe(true)
  })
})
