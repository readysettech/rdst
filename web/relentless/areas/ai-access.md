# Area: ai-access

A person needs AI for Ask and analysis: either their own Anthropic key or the
AI included with a Readyset account. Test every way into and out of it, and
whether the app is honest about where AI stands. Name what you find by what
the app shows; when a step below has no matching control in this build, cover
the closest thing it offers and say so.

Cover at least:

- A fresh install, signed out with no key: which pages work and which ask
  for AI first, whether a user can tell why and what to do, every way
  forward the app offers, keyboard-only use, the smallest window, and
  reload.
- Turning on monitoring signed out: signing in where the app asks should
  turn monitoring on with the choices made, and turning it on without AI
  should say what it leaves out. Cancel the sign-in once: does the dialog
  come back unchanged?
- Adding a key: empty save, whitespace, a key with surrounding spaces, an
  obviously invalid key (type it with `rt fill`, never the real one), a key
  with non-ASCII characters, then the real key with `rt fill-secret`. A bad
  key should be rejected with a clear reason and the real one accepted.
- Saving securely or for this session only: save each way, `rt restart`, and
  check whether the app still has AI. The keychain is real here.
- Settings, AI: testing and updating the key, switching between the included
  AI and your own key, and what it says in each state. Does every other page
  agree about which AI answers?
- The Readyset account path, when `rt status` says mail is available: sign
  up with an address from `rt mail address`, open the sign-in link with
  `rt mail open`, then use the included AI, sign out, and sign in again.
  Also an expired or reused link. Without mail, open the sign-in dialog only
  to check it: field validation, Escape, and focus.
- Clearing local data in Settings: the app should return to a genuine fresh
  install, with no key in the keychain after `rt restart`.

Leads:

- A reused sign-in link has shown raw JSON.
- A non-ASCII key has been accepted and its failure blamed on connectivity.
- When the backend is down, the app has said "Could not check your AI
  setup", blaming AI rather than the missing backend.
