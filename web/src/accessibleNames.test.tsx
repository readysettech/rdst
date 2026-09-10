// A control is named once. An icon that repeats the label beside it adds a
// second, screen-reader-only copy of that label to the name. [MG-06, F-46]
import { BaseInputCheckboxRow } from '@rs/ui-new/base-input-checkbox'
import { Icon } from '@rs/ui-new/icon'
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SectionCard } from './components/audit/report/ReportPrimitives'
import { ExperimentalBanner } from './components/ExperimentalBanner'

afterEach(cleanup)

describe('decorative icons', () => {
  it('contributes no text when the label is empty', () => {
    const { container } = render(
      <div>
        <Icon name="database" label="" />
        <span>Connections</span>
      </div>
    )
    expect(container.textContent).toBe('Connections')
    expect(container.querySelector('svg')?.getAttribute('aria-hidden')).toBe(
      'true'
    )
  })

  it('still names the icon when it carries meaning of its own', () => {
    const { container } = render(<Icon name="alert" label="Unreachable" />)
    expect(container.textContent).toBe('Unreachable')
  })

  it('leaves a section card announcing its title once', () => {
    const { container } = render(
      <SectionCard icon="document-validation" title="Top findings">
        <p>body</p>
      </SectionCard>
    )
    expect(
      screen.getByRole('heading', { name: 'Top findings' }).textContent
    ).toBe('Top findings')
    expect(container.textContent).toBe('Top findingsbody')
  })

  it('leaves the experimental banner announcing its word once', () => {
    const { container } = render(<ExperimentalBanner name="Guards" />)
    expect(container.textContent?.startsWith('Experimental')).toBe(true)
    expect(container.textContent?.match(/Experimental/g)).toHaveLength(1)
  })
})

describe('selection rows', () => {
  it('are one named checkbox, not a checkbox inside a checkbox', () => {
    render(
      <BaseInputCheckboxRow checked={false} aria-label="Select e2e-guard">
        <span>e2e-guard</span>
      </BaseInputCheckboxRow>
    )
    const boxes = screen.getAllByRole('checkbox')
    expect(boxes).toHaveLength(1)
    expect(boxes[0].getAttribute('aria-label')).toBe('Select e2e-guard')
  })

  it('reports the mixed state for a partially selected group', () => {
    render(
      <BaseInputCheckboxRow
        checked="indeterminate"
        aria-label="Select group eu"
      >
        <span>eu</span>
      </BaseInputCheckboxRow>
    )
    expect(screen.getByRole('checkbox').getAttribute('aria-checked')).toBe(
      'mixed'
    )
  })

  it('toggles from the keyboard, because it is a real control', async () => {
    const onCheckedChange = vi.fn()
    render(
      <BaseInputCheckboxRow
        checked={false}
        onCheckedChange={onCheckedChange}
        aria-label="Select e2e-guard"
      >
        <span>e2e-guard</span>
      </BaseInputCheckboxRow>
    )
    screen.getByRole('checkbox').click()
    expect(onCheckedChange).toHaveBeenCalledWith(true)
  })
})
