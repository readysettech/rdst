// A page is navigable by heading only if its section titles are headings.
// These are the surfaces the audit found made entirely of paragraphs. [E-04,
// F-29]
import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import { Page } from '@rs/ui-new/page'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import {
  SectionCard,
  SupportingCard,
} from './components/audit/report/ReportPrimitives'

afterEach(cleanup)

describe('page title', () => {
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

describe('audit report sections', () => {
  it('gives each section card a level-2 heading', () => {
    render(
      <SectionCard icon="document-validation" title="Top findings">
        <p>body</p>
      </SectionCard>
    )
    expect(
      screen.getByRole('heading', { name: 'Top findings', level: 2 })
    ).toBeTruthy()
  })

  it('nests the supporting scores one level below the sections', () => {
    render(
      <SupportingCard icon="sparkles" title="Cache opportunity">
        <p>body</p>
      </SupportingCard>
    )
    expect(
      screen.getByRole('heading', { name: 'Cache opportunity', level: 3 })
    ).toBeTruthy()
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
