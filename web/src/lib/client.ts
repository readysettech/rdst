import createClient from 'openapi-fetch';
import type { paths } from './api.generated';

// openapi-fetch builds `new Request(baseUrl + path)`, and Node/undici's
// Request constructor requires an absolute URL.
const baseUrl =
  typeof window !== 'undefined' && window.location?.origin
    ? window.location.origin
    : 'http://localhost';

export const api = createClient<paths>({
  baseUrl,
  // Re-resolve `fetch` on every call so vi.stubGlobal('fetch', ...) in tests
  // is observed — otherwise openapi-fetch captures a stale reference at
  // createClient time, before the test runs.
  fetch: (...args) => globalThis.fetch(...args),
});
