import { Button } from '@rs/ui-new/button'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

afterEach(cleanup)

// The design system's Button used to resolve a missing `iconPosition` to
// 'none', so `icon="play"` on its own drew nothing and the call site had no
// signal. Fourteen call sites across Compare and Load test lost their icons
// that way (D-02). These pin the contract from the consumer's side.
describe('Button icon contract', () => {
  it('draws an icon that is supplied without a position', () => {
    const { container } = render(<Button label="Run again" icon="play" />)

    const use = container.querySelector('use')
    expect(use?.getAttribute('href')).toContain('#play')
  })

  it('keeps an explicit position', () => {
    const { container } = render(
      <Button label="History" icon="observe" iconPosition="right" />
    )

    const use = container.querySelector('use')
    expect(use?.getAttribute('href')).toContain('#observe')
  })

  it('draws nothing when no icon is supplied', () => {
    const { container } = render(<Button label="Cancel" />)

    expect(container.querySelector('use')).toBeNull()
  })

  it('labels an icon-only button with its label', () => {
    render(<Button label="Close" icon="close" iconPosition="icon" />)

    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy()
  })
})
