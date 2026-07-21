import { render, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

vi.mock('@tanstack/react-router', async (importOriginal) => {
  const mod = await importOriginal<typeof import('@tanstack/react-router')>();
  return {
    ...mod,
    createFileRoute: () => (opts: unknown) => opts,
    Link: ({ children, ...rest }: { children: React.ReactNode }) => (
      <a {...rest}>{children}</a>
    ),
    useNavigate: () => vi.fn(),
  };
});

import { ConnectedHome, FirstRunHome } from './index';

describe('FirstRunHome (state 1)', () => {
  it('leads with the demo band (reference structure), then the setup steps', () => {
    const { container } = render(<FirstRunHome needsApiKey={true} />);
    const scope = within(container as HTMLElement);
    // The consolidated "Welcome to RDST" header is the screen's only
    // title/description pair now — the old "Two steps" block is gone, and the
    // old "Not ready? …" framing was dropped when the band became the lead.
    expect(scope.queryByText('Two steps, then RDST gets smart')).toBeNull();
    expect(scope.queryByText(/Not ready\?/)).toBeNull();
    // Demo band: destination-matching title (USE-041) with its ungated
    // "no setup, no sign-up" qualifier, pill CTA.
    const demoTitle = scope.getByText(/See Readyset Platform in action/);
    expect(scope.getByText(/no setup, no sign-up/)).toBeTruthy();
    expect(scope.getByText('Try it')).toBeTruthy();
    const connect = scope.getAllByText('Connect a database')[0];
    expect(scope.getByText('Add your Anthropic key')).toBeTruthy();
    // Demo band is the lead element: its title renders before the setup cards.
    expect(
      demoTitle.compareDocumentPosition(connect) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });
  it('reflects an already-configured key', () => {
    const { container } = render(<FirstRunHome needsApiKey={false} />);
    expect(within(container as HTMLElement).getByText('Key configured')).toBeTruthy();
  });
});

describe('ConnectedHome (state 2)', () => {
  it('makes discovery the hero with meanwhile alternatives', () => {
    const { container } = render(
      <ConnectedHome target="large" needsApiKey={false} />,
    );
    const scope = within(container as HTMLElement);
    expect(
      scope.getByText('large is connected — now teach RDST what it means'),
    ).toBeTruthy();
    expect(scope.getAllByText('Discover schema').length).toBeGreaterThan(0);
    expect(scope.getByText('Meanwhile: run a health check')).toBeTruthy();
    expect(scope.getByText("Can't wait? Ask now")).toBeTruthy();
  });
  it('degrades to a key prerequisite when the key is missing', () => {
    const { container } = render(
      <ConnectedHome target="large" needsApiKey={true} />,
    );
    const scope = within(container as HTMLElement);
    expect(scope.getByText('Needs an Anthropic key')).toBeTruthy();
    expect(scope.queryAllByText('Discover schema').length).toBe(0);
  });
});
