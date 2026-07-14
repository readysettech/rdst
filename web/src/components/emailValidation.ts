// Email format check for the signup gate. Kept byte-for-byte in sync with the
// server regex in rdst/shared/api/routes/settings.py (_EMAIL_RE). The parity
// test (emailValidation.test.tsx + tests/unit/test_settings_email_route.py)
// drives both against the same fixture so a divergence turns a suite red.
export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export function isValidEmail(email: string): boolean {
  return EMAIL_RE.test(email);
}

// Canonicalize before storing/sending so the same human maps to one identity;
// the server lowercases too, this keeps the client's view consistent.
export function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}
