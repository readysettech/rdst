import { useEffect, useState } from 'react';
import { isValidEmail, normalizeEmail } from './emailValidation';

type EmailGateState = 'checking' | 'needed' | 'ready';

function extractEmail(payload: unknown): string | null {
  if (typeof payload === 'string') return payload.trim() || null;
  if (!payload || typeof payload !== 'object') return null;
  const record = payload as Record<string, unknown>;
  const value = record.email ?? record.stored_email;
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

async function getStoredEmail() {
  const response = await fetch('/api/settings/email');
  if (!response.ok) throw new Error(await response.text() || 'Email settings are unavailable.');
  return extractEmail(await response.json());
}

async function saveStoredEmail(email: string) {
  const response = await fetch('/api/settings/email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) throw new Error(await response.text() || 'Could not save this email.');
  return extractEmail(await response.json()) ?? email;
}

export function EmailGate() {
  const [state, setState] = useState<EmailGateState>('checking');
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getStoredEmail()
      .then((storedEmail) => {
        if (cancelled) return;
        // Gate ONLY when the settings API answered and had no email. If the GET
        // itself failed (403 off-loopback, 500, network) we fail open and let
        // the app render — the gate must never brick the app.
        setState(storedEmail ? 'ready' : 'needed');
      })
      .catch((e: Error) => {
        if (cancelled) return;
        console.warn('EmailGate: contact settings unavailable, not gating.', e);
        setState('ready');
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const submit = async () => {
    const normalized = normalizeEmail(email);
    if (!isValidEmail(normalized)) {
      setError('Please enter a valid email address.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await saveStoredEmail(normalized);
      setState('ready');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save this email.');
    } finally {
      setSaving(false);
    }
  };

  if (state === 'ready') return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby="email-gate-title" className="w-full max-w-md rounded-lg border border-border-layout-1 bg-surface-layout-1 p-6 shadow-xl">
        <h2 id="email-gate-title" className="text-xl font-medium text-content-layout-1">Tell us where to reach you</h2>
        <p className="mt-2 text-sm leading-relaxed text-content-layout-2">We'll use this to keep you posted on ReadySet — no spam.</p>
        <label className="mt-5 block text-sm font-medium text-content-layout-2" htmlFor="rdst-email-gate">
          Email
        </label>
        <input
          id="rdst-email-gate"
          type="email"
          autoComplete="email"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          disabled={saving || state === 'checking'}
          value={email}
          onChange={(event) => setEmail(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit();
          }}
          className="mt-2 h-11 w-full rounded-lg border border-border-layout-1 bg-surface-layout-soft px-3 text-sm text-content-layout-1 outline-none focus:border-border-primary-soft disabled:opacity-60"
          placeholder="you@company.com"
        />
        {error && <p className="mt-3 text-sm text-content-negative-soft">{error}</p>}
        {state === 'checking' && <p className="mt-3 text-sm text-content-layout-3">Checking contact settings...</p>}
        <button
          type="button"
          disabled={saving || state === 'checking'}
          onClick={() => void submit()}
          className="mt-5 inline-flex h-10 w-full items-center justify-center rounded-lg bg-surface-primary-solid px-4 text-sm font-medium text-content-primary-solid disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Get started'}
        </button>
      </div>
    </div>
  );
}
