import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SemanticLayerBadge } from './SemanticLayerBadge'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
}))

vi.mock('@tanstack/react-query', () => ({
  useQuery: () => ({ data: { exists: false } }),
}))

afterEach(cleanup)

describe('SemanticLayerBadge', () => {
  it('lets a sentence-length label wrap instead of painting out of the chip', () => {
    render(<SemanticLayerBadge target="e2e-guard" />)

    const tag = screen.getByText('Live introspection — no semantic layer')
    expect(tag.className).toContain('h-auto')
    expect(tag.className).toContain('min-h-6')
  })

  it('wraps its own row so the chip keeps its width beside the link', () => {
    const { container } = render(<SemanticLayerBadge target="e2e-guard" />)

    expect(container.firstElementChild?.className).toContain('flex-wrap')
  })
})
