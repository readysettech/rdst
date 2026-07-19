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
  it('renders the two setup steps and the ungated demo path', () => {
    const { container } = render(<FirstRunHome needsApiKey={true} />);
    const scope = within(container as HTMLElement);
    expect(scope.getByText('Two steps, then RDST gets smart')).toBeTruthy();
    expect(scope.getAllByText('Connect a database').length).toBeGreaterThan(0);
    expect(scope.getByText('Add your Anthropic key')).toBeTruthy();
    expect(scope.getByText('no setup · no sign-up')).toBeTruthy();
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
