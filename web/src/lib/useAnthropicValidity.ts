import { useQuery } from '@tanstack/react-query';
import { validateAnthropicKey, type AnthropicKeyValidation } from './api';

/**
 * Validity (not just presence) of the configured Anthropic key.
 *
 * Presence tells us a key is set; this tells us whether it actually
 * authenticates, so the UI can distinguish "configured" from "working" and
 * surface a rejected key instead of a false-green claim. Pass `enabled` =
 * whether a key is present, so we never ping the provider when there's no key.
 * The long staleTime keeps this to at most one background ping per session
 * window; the server also caches the result briefly.
 *
 * While the query is `enabled` but not yet resolved, `data` is `undefined` —
 * consumers must treat that unknown window as a neutral "Checking…" state, not
 * a green "configured" claim (home.md chip rule).
 */
export function useAnthropicValidity(enabled: boolean) {
  return useQuery<AnthropicKeyValidation>({
    queryKey: ['anthropic-validity'],
    queryFn: validateAnthropicKey,
    enabled,
    staleTime: 5 * 60_000,
    retry: false,
  });
}
