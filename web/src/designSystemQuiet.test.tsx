// The opt-in `tone="quiet"` of the shared feedback and dialog components: the inline-alert
// line and the 480px dialog. The default tone stays as it was for the other apps.
import { Alert } from '@rs/ui-new/alert'
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'
import { EmptyState } from '@rs/ui-new/empty-state'
import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'
import {
  Modal,
  ModalContent,
  ModalContentContainer,
  ModalTitle,
} from '@rs/ui-new/modal'
import { PageBar } from '@rs/ui-new/page-bar'
import { PageNotices, PageNoticesProvider } from '@rs/ui-new/page-notices'
import { Progress } from '@rs/ui-new/progress'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { useEffect, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

afterEach(cleanup)

const classesOf = (element: Element) =>
  element.className.toString().split(/\s+/)

describe('ErrorState and InlineNotice, quiet', () => {
  it('is one sunk line: no tile, no border, status tone on the lead only', () => {
    const retry = vi.fn()
    render(
      <ErrorState
        tone="quiet"
        errorClass="rdst-service"
        title="The Inbox could not load"
        message="RDST could not read the items."
        action={{ label: 'Open Settings', icon: 'settings', onClick: vi.fn() }}
        onRetry={retry}
      />
    )
    const alert = screen.getByRole('alert')
    expect(classesOf(alert)).toContain('bg-surface-layout-2')
    expect(classesOf(alert)).not.toContain('border')
    expect(alert.querySelectorAll('svg')).toHaveLength(1)
    const title = screen.getByRole('heading', {
      name: 'The Inbox could not load',
    })
    expect(classesOf(title)).toContain('text-content-warning-soft')
    expect(
      classesOf(screen.getByText('RDST could not read the items.'))
    ).toContain('text-content-layout-2')
    const action = screen.getByRole('button', { name: 'Open Settings' })
    expect(action.querySelector('svg')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it('ignores the page layout and its eyebrow', () => {
    render(
      <ErrorState
        tone="quiet"
        layout="page"
        eyebrow="404"
        titleAs="h1"
        title="Page not found"
        message="It may have moved."
      />
    )
    expect(screen.queryByText('404')).toBeNull()
    expect(
      screen.getByRole('heading', { level: 1, name: 'Page not found' })
    ).toBeTruthy()
  })

  it('leads with the message when an InlineNotice has no title', () => {
    render(
      <InlineNotice
        tone="quiet"
        accent="warning"
        title=""
        message="Docker isn't running."
      />
    )
    expect(screen.queryByRole('heading')).toBeNull()
    expect(classesOf(screen.getByText("Docker isn't running."))).toContain(
      'text-content-warning-soft'
    )
  })

  it('keeps the default card for everyone else', () => {
    render(<ErrorState title="Failed" message="Cause." />)
    expect(classesOf(screen.getByRole('alert'))).toContain('border')
  })
})

describe('Alert, quiet', () => {
  it('is not a pressable surface and colors only the label', () => {
    render(
      <Alert
        tone="quiet"
        variant="warning"
        icon="alert"
        label="localhost refused."
      />
    )
    const label = screen.getByText('localhost refused.')
    const root = label.parentElement as HTMLElement
    expect(classesOf(root)).not.toContain('cursor-pointer')
    expect(classesOf(root)).not.toContain('rounded-2xl')
    expect(classesOf(root)).toContain('bg-surface-layout-2')
    expect(classesOf(label)).toContain('text-content-warning-soft')
  })

  it('keeps the default banner', () => {
    render(<Alert variant="warning" label="Careful." />)
    expect(classesOf(screen.getByText('Careful.'))).toContain('cursor-pointer')
  })
})

describe('EmptyState, quiet', () => {
  it('draws no tile even when given an icon', () => {
    const { container } = render(
      <EmptyState
        tone="quiet"
        icon="observe"
        title="Nothing yet"
        body="Runs show here."
      />
    )
    expect(container.querySelector('svg')).toBeNull()
    expect(
      classesOf(screen.getByRole('heading', { name: 'Nothing yet' }))
    ).toContain('text-headline-5')
  })
})

describe('Progress, quiet', () => {
  it('is a 4px track with a first-tone fill', () => {
    render(<Progress tone="quiet" value={40} label="Downloading" />)
    const bar = screen.getByRole('progressbar', { name: 'Downloading' })
    expect(classesOf(bar)).toContain('h-1')
    expect(classesOf(bar)).not.toContain('h-4')
    const fill = bar.firstElementChild as HTMLElement
    expect(classesOf(fill)).toContain('bg-content-layout-1')
    expect(classesOf(fill)).not.toContain('bg-content-rising-plain')
  })
})

describe('ConfirmDialog, quiet', () => {
  it('has a 17px title, plain paragraphs, a footer note and one plain solid', () => {
    render(
      <ConfirmDialog
        tone="quiet"
        isOpen
        onClose={vi.fn()}
        onConfirm={vi.fn()}
        title="Shut down the demo?"
        notice={{ icon: 'trash', message: 'Removes the four containers.' }}
        note="About 10 seconds."
        confirmLabel="Shut down"
        confirmIcon="trash"
        cancelLabel="Keep running"
      />
    )
    const dialog = screen.getByRole('dialog')
    expect(classesOf(dialog)).toContain('max-w-120')
    expect(
      classesOf(screen.getByRole('heading', { name: 'Shut down the demo?' }))
    ).toContain('text-headline-5')
    expect(
      screen.getByText('Removes the four containers.').closest('[role="alert"]')
    ).toBeNull()
    expect(screen.getByText('About 10 seconds.')).toBeTruthy()
    const confirm = screen.getByRole('button', { name: 'Shut down' })
    expect(confirm.querySelector('svg')).toBeNull()
    expect(screen.getByRole('button', { name: 'Keep running' })).toBeTruthy()
  })
})

describe('Modal, quiet', () => {
  it('passes its tone to the title', () => {
    render(
      <Modal open>
        <ModalContentContainer open>
          <ModalContent tone="quiet">
            <ModalTitle>Analyze a query</ModalTitle>
          </ModalContent>
        </ModalContentContainer>
      </Modal>
    )
    expect(
      classesOf(screen.getByRole('heading', { name: 'Analyze a query' }))
    ).toContain('text-headline-5')
  })
})

describe('PageNotices', () => {
  function Counter() {
    const [mounts, setMounts] = useState(0)
    useEffect(() => setMounts((count) => count + 1), [])
    return <p>Notice mounted {mounts}</p>
  }

  it('shows the notices under the bar on screen and keeps them mounted across pages', () => {
    const { rerender } = render(
      <PageNoticesProvider>
        <PageNotices>
          <Counter />
        </PageNotices>
        <PageBar title="Home" />
      </PageNoticesProvider>
    )
    const notice = screen.getByText('Notice mounted 1')
    const bar = screen.getByRole('banner')
    expect(
      bar.compareDocumentPosition(notice) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()

    act(() => {
      rerender(
        <PageNoticesProvider>
          <PageNotices>
            <Counter />
          </PageNotices>
          <div>
            <PageBar title="Ask" />
          </div>
        </PageNoticesProvider>
      )
    })
    expect(screen.getByText('Notice mounted 1')).toBeTruthy()
    expect(screen.getByRole('heading', { name: 'Ask' })).toBeTruthy()
  })

  it('stays mounted but unseen with no bar on screen', () => {
    render(
      <PageNoticesProvider>
        <PageNotices>
          <Counter />
        </PageNotices>
      </PageNoticesProvider>
    )
    expect(screen.queryByText(/Notice mounted/)).toBeNull()
  })
})
