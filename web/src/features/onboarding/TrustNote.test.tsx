import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { READYSET_HISTORY_URL, SignInTrustNote } from './TrustNote'

afterEach(cleanup)

describe('SignInTrustNote', () => {
  it("says who's behind RDST and links to how data is handled", () => {
    render(<SignInTrustNote />)
    expect(
      screen.getByText(/founding team comes from MIT's PDOS lab/)
    ).toBeTruthy()
    expect(
      screen.getByRole('link', { name: 'Our story' }).getAttribute('href')
    ).toBe(READYSET_HISTORY_URL)
    expect(
      screen
        .getByRole('link', { name: 'How RDST handles your data' })
        .getAttribute('href')
    ).toBe('https://readyset.io/docs/readyset-ai/rdst/desktop/privacy')
  })
})
