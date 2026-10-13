import { Button } from '@rs/ui-new/button'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { useState } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { DialogShell } from './DialogShell'

afterEach(cleanup)

function Opener() {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button label="Give feedback" onClick={() => setOpen(true)} />
      <DialogShell
        open={open}
        onClose={() => setOpen(false)}
        title="Give feedback"
      >
        <input aria-label="Feedback" />
      </DialogShell>
    </>
  )
}

describe('DialogShell', () => {
  it('returns focus to the control that opened it when Escape closes it', async () => {
    render(<Opener />)
    const trigger = screen.getByRole('button', { name: 'Give feedback' })
    trigger.focus()
    fireEvent.click(trigger)
    const dialog = await screen.findByRole('dialog')
    await waitFor(() =>
      expect(dialog.contains(document.activeElement)).toBe(true)
    )

    fireEvent.keyDown(document.activeElement as Element, { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    await waitFor(() => expect(document.activeElement).toBe(trigger))
  })
})
