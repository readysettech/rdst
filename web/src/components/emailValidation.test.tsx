import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { isValidEmail } from './emailValidation';

// Single source of truth shared with the Python server test
// (rdst/tests/unit/test_settings_email_route.py). If the client EMAIL_RE and
// the server _EMAIL_RE ever diverge, one of the two suites goes red on the same
// fixture.
const fixturePath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../../../rdst/tests/unit/fixtures/email_validation_cases.json',
);

interface Case {
  email: string;
  valid: boolean;
}

const cases: Case[] = JSON.parse(readFileSync(fixturePath, 'utf-8')).cases;

describe('email validation parity', () => {
  it('has a non-empty shared fixture', () => {
    expect(cases.length).toBeGreaterThan(0);
  });

  it.each(cases)('agrees with the server on %o', ({ email, valid }) => {
    expect(isValidEmail(email)).toBe(valid);
  });
});
