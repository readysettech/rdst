import { expect, it } from 'vitest'
import { turnOnRequestTarget } from './useAgentTurnOnRequests'

const elsewhere = { pathname: '/', search: {} }

it("opens the requested database's turn-on dialog", () => {
  expect(
    turnOnRequestTarget({ step: 'awaiting_user', target: 'shop' }, elsewhere)
  ).toBe('shop')
  expect(
    turnOnRequestTarget(
      { step: 'awaiting_user', target: 'shop' },
      { pathname: '/database', search: { target: 'orders', panel: 'enable' } }
    )
  ).toBe('shop')
})

it('leaves an open dialog, the add dialog and other steps alone', () => {
  expect(
    turnOnRequestTarget(
      { step: 'awaiting_user', target: 'shop' },
      { pathname: '/database', search: { target: 'shop', panel: 'enable' } }
    )
  ).toBeNull()
  expect(
    turnOnRequestTarget(
      { step: 'awaiting_user', target: 'shop' },
      { pathname: '/', search: { add: 'agent' } }
    )
  ).toBeNull()
  expect(
    turnOnRequestTarget({ step: 'tested', target: 'shop' }, elsewhere)
  ).toBeNull()
  expect(turnOnRequestTarget({ step: 'awaiting_user' }, elsewhere)).toBeNull()
})
