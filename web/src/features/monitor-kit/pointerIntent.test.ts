import { afterEach, expect, it } from 'vitest'
import { fromScroll, resetPointerIntent } from './pointerIntent'

function move(x: number, y: number) {
  window.dispatchEvent(
    new MouseEvent('pointermove', { clientX: x, clientY: y })
  )
}

afterEach(resetPointerIntent)

it('treats events at the parked position after a scroll as scroll-made', () => {
  move(100, 200)
  expect(fromScroll({ clientX: 100, clientY: 200 })).toBe(false)
  document.dispatchEvent(new Event('scroll'))
  expect(fromScroll({ clientX: 100, clientY: 200 })).toBe(true)
  // A pointermove at the same spot (the browser's own after a scroll) changes nothing.
  move(100, 200)
  expect(fromScroll({ clientX: 100, clientY: 200 })).toBe(true)
})

it('counts hovers again once the pointer moves', () => {
  move(100, 200)
  document.dispatchEvent(new Event('scroll'))
  move(101, 200)
  expect(fromScroll({ clientX: 101, clientY: 200 })).toBe(false)
})

it('never suppresses before the pointer has been seen', () => {
  document.dispatchEvent(new Event('scroll'))
  expect(fromScroll({ clientX: 0, clientY: 0 })).toBe(false)
})
