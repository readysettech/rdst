import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { Main } from './Main'

describe('Main', () => {
  it('keeps the Radix measurement wrapper table-sized and width-constrained', () => {
    render(
      <Main>
        <div>Report content</div>
      </Main>
    )

    const content = document.getElementById('main-content')
    const viewport = Array.from(
      document.querySelectorAll<HTMLElement>(
        '[data-radix-scroll-area-viewport]'
      )
    ).find((candidate) => candidate.contains(content))
    const measurementWrapper = viewport?.firstElementChild as
      | HTMLElement
      | undefined

    expect(viewport).toBeTruthy()
    expect(viewport?.classList.contains('[&>div]:!w-full')).toBe(true)
    expect(viewport?.classList.contains('[&>div]:!table-fixed')).toBe(true)
    expect(viewport?.classList.contains('[&>div]:!block')).toBe(false)
    expect(measurementWrapper?.style.display).toBe('table')
    expect(measurementWrapper?.style.minWidth).toBe('100%')
  })
})
