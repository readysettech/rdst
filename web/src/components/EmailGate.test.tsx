import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { EmailGate } from './EmailGate';

function jsonResponse(data: unknown) {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorResponse(status: number) {
  return new Response(JSON.stringify({ detail: 'nope' }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const FRESH_INSTALL = { email: null, first_name: null, last_name: null, verified: false };

function stubGateFetch(overrides: {
  identity?: unknown;
  submit?: () => Response;
  poll?: () => Response;
}) {
  const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.endsWith('/api/settings/email/verify-poll')) {
      return Promise.resolve(overrides.poll?.() ?? jsonResponse({ verified: false }));
    }
    if (url.endsWith('/api/settings/email') && !init?.method) {
      return Promise.resolve(jsonResponse(overrides.identity ?? FRESH_INSTALL));
    }
    if (url.endsWith('/api/settings/email') && init?.method === 'POST') {
      return Promise.resolve(
        overrides.submit?.() ??
          jsonResponse({ success: true, verified: false, verification_started: true }),
      );
    }
    return Promise.resolve(jsonResponse({}));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

function fillForm() {
  fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'ada@example.com' } });
}

describe('EmailGate', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('renders nothing while the settings check is in flight', async () => {
    let resolveCheck!: (response: Response) => void;
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>((resolve) => {
      resolveCheck = resolve;
    })));

    render(<EmailGate />);

    expect(screen.queryByText('Enter your email to start the demo')).toBeNull();

    resolveCheck(jsonResponse(FRESH_INSTALL));
    expect(await screen.findByText('Enter your email to start the demo')).toBeTruthy();
  });

  it('requires a valid email, then holds for inbox verification', async () => {
    const fetchMock = stubGateFetch({});
    render(<EmailGate />);

    expect(await screen.findByText('Enter your email to start the demo')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    expect(screen.getByText('Please enter a valid email address.')).toBeTruthy();

    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));

    expect(await screen.findByText('Check your inbox')).toBeTruthy();
    // Email only — a local demo needs no name; the POST carries just the email.
    expect(fetchMock).toHaveBeenCalledWith('/api/settings/email', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ email: 'ada@example.com' }),
    }));
  });

  it('moves on when the manual check reports verified', async () => {
    let verified = false;
    stubGateFetch({ poll: () => jsonResponse({ verified }) });
    render(<EmailGate />);

    await screen.findByText('Enter your email to start the demo');
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    await screen.findByText('Check your inbox');

    fireEvent.click(screen.getByRole('button', { name: "I've clicked the link" }));
    expect(await screen.findByText(/Not verified yet/)).toBeTruthy();

    verified = true;
    fireEvent.click(screen.getByRole('button', { name: "I've clicked the link" }));
    await waitFor(() => {
      expect(screen.queryByText('Check your inbox')).toBeNull();
    });
  });

  it('skips the inbox hold when the email is already verified elsewhere', async () => {
    stubGateFetch({
      submit: () => jsonResponse({ success: true, verified: true, verification_started: true }),
    });
    render(<EmailGate />);

    await screen.findByText('Enter your email to start the demo');
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    await waitFor(() => {
      expect(screen.queryByText('Enter your email to start the demo')).toBeNull();
      expect(screen.queryByText('Check your inbox')).toBeNull();
    });
  });

  it('grandfathers any install that already has a stored email', async () => {
    stubGateFetch({
      identity: { email: 'old@example.com', first_name: null, last_name: null, verified: false },
    });
    render(<EmailGate />);
    await waitFor(() => {
      expect(screen.queryByText('Enter your email to start the demo')).toBeNull();
    });
  });

  it('hard-blocks with a retry message when the keyservice is unreachable', async () => {
    stubGateFetch({
      submit: () => jsonResponse({ success: true, verified: false, verification_started: false }),
    });
    render(<EmailGate />);

    await screen.findByText('Enter your email to start the demo');
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));

    expect(await screen.findByText(/verification service is temporarily unavailable/)).toBeTruthy();
    // The gate stays up: no way into the app without verification.
    expect(screen.getByText('Enter your email to start the demo')).toBeTruthy();
  });

  it('fails open when OUR settings API is unavailable', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(errorResponse(403))));
    render(<EmailGate />);
    await waitFor(() => {
      expect(screen.queryByText('Enter your email to start the demo')).toBeNull();
    });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('keeps the gate up with an error when the POST fails', async () => {
    stubGateFetch({ submit: () => errorResponse(400) });
    render(<EmailGate />);

    await screen.findByText('Enter your email to start the demo');
    fillForm();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));

    await waitFor(() => {
      expect(screen.getByText('nope')).toBeTruthy();
    });
    expect(screen.getByText('Enter your email to start the demo')).toBeTruthy();
  });
});
