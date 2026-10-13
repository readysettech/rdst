import { cleanup, fireEvent, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { renderWithClient } from '@/test-utils'
import { MuteMenu, muteScopes } from './MuteMenu'
import { describeRule } from './severity'

afterEach(cleanup)

const subject = { target: 'demo', queryKey: 'q1', type: 'query_regressed' }

it('asks how long first, a week by default, then where to mute', async () => {
  renderWithClient(<MuteMenu {...subject} />)
  fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), {
    key: 'ArrowDown',
  })
  const week = await screen.findByRole('menuitemradio', { name: 'For 1 week' })
  expect(week.getAttribute('aria-checked')).toBe('true')
  expect(
    screen
      .getByRole('menuitemradio', { name: 'Until I unmute' })
      .getAttribute('aria-checked')
  ).toBe('false')
  const names = screen
    .getAllByRole('menuitem')
    .concat(screen.getAllByRole('menuitemradio'))
    .sort((a, b) =>
      a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1
    )
    .map((item) => item.textContent)
  expect(names.slice(0, 3)).toEqual([
    'For 1 day',
    'For 1 week',
    'Until I unmute',
  ])
  expect(names.at(-1)).toMatch(/on all databases$/)
})

it('says databases, never targets, for a rule everywhere', () => {
  expect(muteScopes(subject).at(-1)?.label).toMatch(/ on all databases$/)
  expect(
    describeRule({ target: null, type: 'query_regressed', query_key: null })
  ).toMatch(/ on all databases$/)
})
