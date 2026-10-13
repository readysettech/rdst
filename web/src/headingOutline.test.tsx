// A page is navigable by heading only if its section titles are headings.
// These are the surfaces the audit found made entirely of paragraphs. [E-04,
// F-29]
import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import { Page } from '@rs/ui-new/page'
import { PageBar } from '@rs/ui-new/page-bar'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

afterEach(cleanup)

describe('page title', () => {
  it('is the page bar level-1 heading', () => {
    render(<PageBar title="Inbox" meta={<span>2 open</span>} />)
    expect(
      screen.getByRole('heading', { name: 'Inbox', level: 1 })
    ).toBeTruthy()
  })

  it('stays with the page when the shell draws the bar for it', () => {
    render(
      <div>
        <PageBar title="Queries" titleAs="p" />
        <h1>Queries</h1>
      </div>
    )
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1)
  })

  it('is the route level-1 heading', () => {
    render(
      <Page>
        <Page.Header>
          <Page.Title>Home</Page.Title>
        </Page.Header>
      </Page>
    )
    expect(screen.getByRole('heading', { name: 'Home', level: 1 })).toBeTruthy()
  })
})

describe('error surfaces', () => {
  it('names the failure with a heading, not a paragraph', () => {
    render(<ErrorState title="Home could not load" message="Try again." />)
    expect(
      screen.getByRole('heading', { name: 'Home could not load', level: 3 })
    ).toBeTruthy()
  })

  it('takes the level from the caller when the outline needs another', () => {
    render(
      <ErrorState
        title="Analysis unavailable"
        message="Try again."
        titleAs="h4"
      />
    )
    expect(
      screen.getByRole('heading', { name: 'Analysis unavailable', level: 4 })
    ).toBeTruthy()
  })

  it('applies to the compact inline notice too', () => {
    render(
      <InlineNotice title="AI analysis unavailable" message="No findings." />
    )
    expect(
      screen.getByRole('heading', {
        name: 'AI analysis unavailable',
        level: 3,
      })
    ).toBeTruthy()
  })

  it('keeps the page layout at level 1', () => {
    render(
      <ErrorState layout="page" title="Page not found" message="Go home." />
    )
    expect(
      screen.getByRole('heading', { name: 'Page not found', level: 1 })
    ).toBeTruthy()
  })
})
