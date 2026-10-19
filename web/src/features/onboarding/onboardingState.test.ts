import { afterEach, describe, expect, it } from 'vitest'
import { setInstallationScope } from '../../lib/installationScope'
import {
  clearTourStep,
  markTourDone,
  savedTourStep,
  saveTourStep,
  tourPending,
} from './onboardingState'

afterEach(() => localStorage.clear())

describe('onboardingState', () => {
  it('keys the tour to the installation, so a new data directory shows it again', () => {
    setInstallationScope('aaaa1111aaaa1111')
    expect(tourPending()).toBe(true)
    markTourDone()
    saveTourStep(2)
    expect(tourPending()).toBe(false)
    expect(savedTourStep()).toBe(2)

    setInstallationScope('bbbb2222bbbb2222')
    expect(tourPending()).toBe(true)
    expect(savedTourStep()).toBeNull()

    setInstallationScope('aaaa1111aaaa1111')
    expect(tourPending()).toBe(false)
    clearTourStep()
    expect(savedTourStep()).toBeNull()
  })

  it('ignores the unscoped flags older versions saved', () => {
    setInstallationScope('cccc3333cccc3333')
    localStorage.setItem('rdst_onboarding_tour_done', '1')
    localStorage.setItem('rdst_onboarding_tour_step', '3')
    expect(tourPending()).toBe(true)
    expect(savedTourStep()).toBeNull()
  })

  it('keeps the known installation when a status reply has no id', () => {
    setInstallationScope('dddd4444dddd4444')
    markTourDone()
    setInstallationScope(null)
    expect(tourPending()).toBe(false)
  })
})
