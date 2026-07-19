import { useCallback, useEffect, useRef, useState } from 'react';
import { isValidEmail, normalizeEmail } from './emailValidation';

// The signup gate for rdst web: email only, then inbox verification through
// the keyservice - the same mailbox proof a trial registration performs, minus
// the trial token. A local-Docker demo needs no more than a verifiable email,
// so the name fields are gone (over-asking for a local demo — USE-068), and
// the telemetry use is disclosed honestly rather than hidden.
//
// Rules:
// - Existing installs are grandfathered: any stored email means no gate.
// - An email already verified by any flow (trial, CLI audit report, this
//   gate on another machine) passes instantly with no second email.
// - If the keyservice is unreachable, the gate holds (hard block) with a
//   retry - but if OUR OWN settings API is unavailable the gate steps aside,
//   since that says nothing about the user and must not brick the app.
type GateState = 'checking' | 'collect' | 'verifying' | 'ready';

const POLL_INTERVAL_MS = 3000;

interface StoredIdentity {
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  verified: boolean;
}

async function getStoredIdentity(): Promise<StoredIdentity> {
  const response = await fetch('/api/settings/email');
  if (!response.ok) throw new Error((await response.text()) || 'Email settings are unavailable.');
  return (await response.json()) as StoredIdentity;
}

async function submitSignup(email: string) {
  const response = await fetch('/api/settings/email', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  });
  if (!response.ok) {
    let detail = 'Could not save your details.';
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return (await response.json()) as { verified: boolean; verification_started: boolean };
}

async function pollVerification(): Promise<boolean> {
  const response = await fetch('/api/settings/email/verify-poll', { method: 'POST' });
  if (!response.ok) {
    let detail = 'The RDST verification service is temporarily unavailable.';
    try {
      const body = await response.json();
      if (typeof body?.detail === 'string') detail = body.detail;
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  const body = (await response.json()) as { verified?: boolean };
  return Boolean(body.verified);
}

export function EmailGate() {
  const [state, setState] = useState<GateState>('checking');
  const [email, setEmail] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const pollTimer = useRef<number | null>(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current != null) {
      window.clearInterval(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    getStoredIdentity()
      .then((identity) => {
        if (cancelled) return;
        // Any stored email - verified or not - means this install predates
        // the gate or already passed it. Only fresh installs sign up.
        setState(identity.email ? 'ready' : 'collect');
      })
      .catch((e: Error) => {
        if (cancelled) return;
        console.warn('EmailGate: contact settings unavailable, not gating.', e);
        setState('ready');
      });
    return () => {
      cancelled = true;
      stopPolling();
    };
  }, [stopPolling]);

  const beginPolling = useCallback(() => {
    stopPolling();
    pollTimer.current = window.setInterval(() => {
      void pollVerification()
        .then((verified) => {
          if (verified) {
            stopPolling();
            setState('ready');
          }
        })
        .catch((e: unknown) => {
          stopPolling();
          setError(
            e instanceof Error
              ? e.message
              : 'The RDST verification service is temporarily unavailable.',
          );
        });
    }, POLL_INTERVAL_MS);
  }, [stopPolling]);

  const submit = async () => {
    const normalized = normalizeEmail(email);
    if (!isValidEmail(normalized)) {
      setError('Please enter a valid email address.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const result = await submitSignup(normalized);
      if (result.verified) {
        setState('ready');
        return;
      }
      if (!result.verification_started) {
        setError('The RDST verification service is temporarily unavailable. Please try again.');
        return;
      }
      setState('verifying');
      beginPolling();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not save your details.');
    } finally {
      setSaving(false);
    }
  };

  const checkNow = async () => {
    setSaving(true);
    try {
      if (await pollVerification()) {
        stopPolling();
        setState('ready');
      } else {
        setError('Not verified yet - click the link in the email we sent, then try again.');
      }
    } catch (e) {
      setError(
        e instanceof Error
          ? e.message
          : 'The RDST verification service is temporarily unavailable.',
      );
    } finally {
      setSaving(false);
    }
  };

  // Render nothing until the settings check says the gate is needed, so
  // users with a stored email never see the overlay flash on page load.
  if (state === 'ready' || state === 'checking') return null;

  const inputClass =
    'mt-2 h-11 w-full rounded-lg border border-border-layout-1 bg-surface-layout-soft px-3 text-sm text-content-layout-1 outline-none focus:border-border-primary-soft disabled:opacity-60';

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/55 p-4 backdrop-blur-sm" role="presentation">
      <div role="dialog" aria-modal="true" aria-labelledby="email-gate-title" className="w-full max-w-md rounded-lg border border-border-layout-1 bg-surface-layout-1 p-6 shadow-xl">
        {state === 'verifying' ? (
          <>
            <h2 id="email-gate-title" className="text-xl font-medium text-content-layout-1">Check your inbox</h2>
            <p className="mt-2 text-sm leading-relaxed text-content-layout-2">
              We sent a verification link to{' '}
              <span className="font-medium text-content-layout-1">{normalizeEmail(email)}</span>.
              Click it to continue.
            </p>
            {error && <p className="mt-3 text-sm text-content-negative-soft">{error}</p>}
            <button
              type="button"
              disabled={saving}
              onClick={() => void checkNow()}
              className="mt-5 inline-flex h-10 w-full items-center justify-center rounded-lg bg-surface-primary-solid px-4 text-sm font-medium text-content-primary-solid disabled:opacity-50"
            >
              {saving ? 'Checking...' : "I've clicked the link"}
            </button>
            <button
              type="button"
              disabled={saving}
              onClick={() => {
                stopPolling();
                setError(null);
                setState('collect');
              }}
              className="mt-3 inline-flex h-10 w-full items-center justify-center rounded-lg border border-border-layout-1 px-4 text-sm font-medium text-content-layout-2 disabled:opacity-50"
            >
              Use a different email
            </button>
          </>
        ) : (
          <>
            <h2 id="email-gate-title" className="text-xl font-medium text-content-layout-1">Enter your email to start the demo</h2>
            <p className="mt-2 text-sm leading-relaxed text-content-layout-2">
              We use your email to send the verification link and to understand
              product usage — no spam.{' '}
              <a
                href="https://readyset.io/privacy"
                target="_blank"
                rel="noreferrer"
                className="text-content-primary-soft hover:underline"
              >
                Privacy
              </a>
            </p>
            <label className="mt-5 block text-sm font-medium text-content-layout-2" htmlFor="rdst-email-gate">Email</label>
            <input
              id="rdst-email-gate"
              type="email"
              autoComplete="email"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              disabled={saving}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submit();
              }}
              className={inputClass}
              placeholder="you@company.com"
            />
            {error && <p className="mt-3 text-sm text-content-negative-soft">{error}</p>}
            <button
              type="button"
              disabled={saving}
              onClick={() => void submit()}
              className="mt-5 inline-flex h-10 w-full items-center justify-center rounded-lg bg-surface-primary-solid px-4 text-sm font-medium text-content-primary-solid disabled:opacity-50"
            >
              {saving ? 'Saving...' : 'Get started'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
