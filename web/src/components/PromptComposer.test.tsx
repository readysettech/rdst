import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { MessageInput } from './MessageInput'
import { PromptComposer } from './PromptComposer'

afterEach(cleanup)

function composer(props: Partial<Parameters<typeof PromptComposer>[0]> = {}) {
  return (
    <PromptComposer
      value="why is this slow"
      onChange={vi.fn()}
      onSubmit={vi.fn()}
      label="Follow-up question"
      placeholder="Ask about this analysis"
      submitLabel="Send"
      fieldClassName="min-h-[2lh] max-h-[5lh]"
      {...props}
    />
  )
}

describe('PromptComposer', () => {
  it('sizes the send action to its label instead of the pane', () => {
    render(composer())

    const send = screen.getByRole('button', { name: 'Send' })
    expect(send.className).not.toContain('w-full')
  })

  it('takes the height range the surface asks for', () => {
    render(composer())

    const field = screen.getByRole('textbox', { name: 'Follow-up question' })
    expect(field.className).toContain('min-h-[2lh]')
    expect(field.className).toContain('max-h-[5lh]')
  })

  it('sends on Enter and breaks the line on Shift+Enter', () => {
    const onSubmit = vi.fn()
    render(composer({ onSubmit }))
    const field = screen.getByRole('textbox', { name: 'Follow-up question' })

    fireEvent.keyDown(field, { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()

    fireEvent.keyDown(field, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('prints the keyboard contract it honours', () => {
    render(composer())

    expect(
      screen.getByText('Enter to ask · Shift+Enter for a new line')
    ).toBeTruthy()
  })

  it('reads as disabled with nothing to send, and as busy only in flight', () => {
    const { rerender } = render(composer({ value: '  ' }))

    const idle = screen.getByRole('button', { name: 'Send' })
    expect(idle.hasAttribute('disabled')).toBe(true)
    expect(idle.querySelector('#loader')).toBeNull()

    rerender(composer({ busy: true }))
    expect(
      screen.getByRole('button', { name: 'Send' }).querySelector('#loader')
    ).toBeTruthy()
  })
})

describe('MessageInput', () => {
  it('gives the follow-up field a name and a two-line floor', () => {
    render(<MessageInput value="" onChange={vi.fn()} onSubmit={vi.fn()} />)

    const field = screen.getByRole('textbox', { name: 'Follow-up question' })
    expect(field.className).toContain('min-h-[2lh]')
    expect(field.className).toContain('max-h-[5lh]')
    expect(field.className).not.toContain('min-h-[5lh]')
  })
})
