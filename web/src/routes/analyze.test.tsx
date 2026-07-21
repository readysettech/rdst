import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute: () => (options: unknown) => options,
  // autoCodeSplitting rewrites the route's `component` to a lazyRouteComponent
  // call; the test imports the exported page directly, so this just needs to exist.
  lazyRouteComponent: (loader: unknown) => loader,
  useNavigate: () => vi.fn(),
}))

// Stub the heavy editor + history so the test can drive onSelect and inspect the
// value the editor received, without mounting CodeMirror. The QueryEditor stub
// exposes a `.cm-content` node so the focus path is exercised for real.
vi.mock('../components', () => ({
  QueryEditor: ({ value }: { value: string }) => (
    <div>
      <div className="cm-content" tabIndex={-1}>
        editor
      </div>
      <span data-testid="editor-value">{value}</span>
    </div>
  ),
  QueryHistory: ({ onSelect }: { onSelect: (sql: string) => void }) => (
    <button type="button" onClick={() => onSelect('SELECT picked FROM t')}>
      pick recent
    </button>
  ),
  TargetLockNotice: () => null,
}))

vi.mock('../lib/useQueryRegistry', () => ({
  useQueryRegistry: () => ({ queries: [], addQuery: vi.fn() }),
}))

vi.mock('../lib/useTargetPasswordLock', () => ({
  useTargetPasswordLock: () => ({
    isLocked: false,
    message: '',
    missingTargetRequirements: [],
    keyringAvailable: true,
  }),
}))

vi.mock('../hooks/useTarget', () => ({
  useTarget: () => ({ target: 'prod' }),
}))

import { AnalyzePage } from './-analyze-page'

afterEach(() => cleanup())

// jsdom implements neither scrollIntoView nor matchMedia; install fakes so the
// feedback path can be exercised.
const originalScrollIntoView = Element.prototype.scrollIntoView
const originalMatchMedia = window.matchMedia

describe('analyze click-to-input feedback', () => {
  let scrollSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    scrollSpy = vi.fn()
    Element.prototype.scrollIntoView = scrollSpy
    // Default: no reduced-motion preference.
    window.matchMedia = undefined as unknown as typeof window.matchMedia
  })

  afterEach(() => {
    Element.prototype.scrollIntoView = originalScrollIntoView
    window.matchMedia = originalMatchMedia
  })

  it('scrolls the editor into view, focuses it, flashes, and lands the SQL', () => {
    render(<AnalyzePage />)

    // Before the click the editor is empty and un-flashed.
    expect(screen.getByTestId('editor-value').textContent).toBe('')
    const wrapper = screen.getByTestId('analyze-editor')
    expect(wrapper.className).toContain('ring-0')

    fireEvent.click(screen.getByRole('button', { name: 'pick recent' }))

    // 1) SQL landed in the editor.
    expect(screen.getByTestId('editor-value').textContent).toBe(
      'SELECT picked FROM t'
    )
    // 2) The editor was scrolled into view (block:"nearest" = minimal, no jump
    //    when already visible) and focused.
    expect(scrollSpy).toHaveBeenCalledTimes(1)
    expect(scrollSpy.mock.calls[0][0]).toMatchObject({ block: 'nearest' })
    expect(document.activeElement?.className).toContain('cm-content')
    // 3) A non-colour ring flash is applied to the editor wrapper.
    expect(wrapper.className).toContain('ring-2')
  })

  it('uses reduced-motion (instant) scrolling when the user prefers it', () => {
    window.matchMedia = ((q: string) =>
      ({
        matches: q.includes('reduce'),
        media: q,
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
        addListener: vi.fn(),
        removeListener: vi.fn(),
        onchange: null,
        dispatchEvent: vi.fn(),
      }) as unknown as MediaQueryList) as unknown as typeof window.matchMedia

    render(<AnalyzePage />)
    fireEvent.click(screen.getByRole('button', { name: 'pick recent' }))

    expect(scrollSpy.mock.calls[0][0]).toMatchObject({ behavior: 'auto' })
  })
})
