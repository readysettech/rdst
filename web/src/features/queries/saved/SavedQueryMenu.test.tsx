import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { SavedQueryMenu } from './SavedQueryMenu'

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

function openMenu() {
  fireEvent.keyDown(screen.getByRole('button', { name: 'More actions' }), {
    key: 'Enter',
  })
}

describe('SavedQueryMenu re-run (A3)', () => {
  it('keeps re-run out of the menu until an analysis is stored', () => {
    render(
      <SavedQueryMenu
        onEditSql={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />
    )

    openMenu()
    expect(
      screen.queryByRole('menuitem', { name: /Re-run analysis/ })
    ).toBeNull()
  })

  it('moves re-run into the menu once View leads the row', () => {
    const onReRunAnalysis = vi.fn()
    render(
      <SavedQueryMenu
        onEditSql={vi.fn()}
        onRename={vi.fn()}
        onReRunAnalysis={onReRunAnalysis}
        onDelete={vi.fn()}
      />
    )

    openMenu()
    fireEvent.click(screen.getByRole('menuitem', { name: /Re-run analysis/ }))
    expect(onReRunAnalysis).toHaveBeenCalledOnce()
  })
})
