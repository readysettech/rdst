import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PreferenceMenu } from './PreferenceMenu'
import { THEME_LABEL, THEMES } from './preferences'

afterEach(cleanup)

it('shows the current choice as the button and names the row', () => {
  const onChange = vi.fn()
  render(
    <PreferenceMenu
      label="Theme"
      value="dark"
      options={THEMES}
      optionLabel={(option) => THEME_LABEL[option]}
      onChange={onChange}
    />
  )
  const button = screen.getByRole('button', { name: 'Theme: Dark' })
  expect(button.textContent).toContain('Dark')
  expect(button.className.includes('bg-surface-primary-solid')).toBe(false)
})

it('can be held while the store loads', () => {
  render(
    <PreferenceMenu
      label="Font size"
      value="large"
      options={['default', 'large'] as const}
      optionLabel={(option) => (option === 'large' ? 'Large' : 'Default')}
      disabled
      onChange={vi.fn()}
    />
  )
  expect(
    (
      screen.getByRole('button', {
        name: 'Font size: Large',
      }) as HTMLButtonElement
    ).disabled
  ).toBe(true)
})
