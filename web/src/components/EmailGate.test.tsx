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
  return new Response('nope', { status });
}

describe('EmailGate', () => {
  afterEach(() => {
    cleanup();
    vi.unstubAllGlobals();
  });

  it('blocks until a valid email is stored', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/settings/email') && !init?.method) return Promise.resolve(jsonResponse({ email: null }));
      if (url.endsWith('/api/settings/email') && init?.method === 'POST') return Promise.resolve(jsonResponse({ email: 'mike@example.com' }));
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<EmailGate />);

    expect(await screen.findByText('Tell us where to reach you')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));
    expect(screen.getByText('Please enter a valid email address.')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'mike@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));

    await waitFor(() => {
      expect(screen.queryByText('Tell us where to reach you')).toBeNull();
    });
    expect(fetchMock).toHaveBeenCalledWith('/api/settings/email');
    expect(fetchMock).toHaveBeenCalledWith('/api/settings/email', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ email: 'mike@example.com' }),
    }));
  });

  it('does not show the gate when the backend already has an email', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse({ email: 'stored@example.com' }))));

    render(<EmailGate />);

    await waitFor(() => {
      expect(screen.queryByText('Tell us where to reach you')).toBeNull();
    });
  });

  it('fails open (renders the app) when the settings GET returns 403', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(errorResponse(403))));

    render(<EmailGate />);

    await waitFor(() => {
      expect(screen.queryByText('Tell us where to reach you')).toBeNull();
    });
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it('fails open (renders the app) when the settings GET returns 500', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(errorResponse(500))));

    render(<EmailGate />);

    await waitFor(() => {
      expect(screen.queryByText('Tell us where to reach you')).toBeNull();
    });
  });

  it('keeps the gate up with an error when the POST fails', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/api/settings/email') && !init?.method) return Promise.resolve(jsonResponse({ email: null }));
      if (url.endsWith('/api/settings/email') && init?.method === 'POST') return Promise.resolve(errorResponse(400));
      return Promise.resolve(jsonResponse({}));
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<EmailGate />);

    expect(await screen.findByText('Tell us where to reach you')).toBeTruthy();
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'mike@example.com' } });
    fireEvent.click(screen.getByRole('button', { name: 'Get started' }));

    await waitFor(() => {
      expect(screen.getByText('nope')).toBeTruthy();
    });
    // Gate must remain — a failed save cannot let the user past.
    expect(screen.getByText('Tell us where to reach you')).toBeTruthy();
  });
});
