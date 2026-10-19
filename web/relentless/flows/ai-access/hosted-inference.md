# Flow: ai-access/hosted-inference

Capability: ai-access
Areas: ai-access, ask
Modes: desktop, browser
Minutes: 15
Covers: rdst/shared/llm_manager/hosted_provider.py rdst/shared/keyservice.py rdst/features/account rdst/shared/env_requirements_service.py web-apps/apps/rdst/src/lib/accountAuth.ts web-apps/apps/rdst/src/layout/SidebarAccountMenu.tsx web-apps/apps/rdst/src/routes/account-login.tsx web-apps/apps/rdst/src/routes/-sign-in-page.tsx

A person with no Anthropic key signs in to Readyset and uses the included
AI. Every answer must come from Readyset-hosted inference, and when it is
unavailable the app must say so rather than fall back or blame something
else.

## Mission

This flow needs mail: if `rt status` says mail is not available, record
the flow as blocked. Start from a fresh install and do not add an
Anthropic key at any point. Then:

1. Sign in to Readyset from the AI gate with a fresh address from
   `rt mail address`, open the link with `rt mail open ADDRESS`, and
   choose the included AI. Where does the app land, and does it say you
   are signed in?
2. Check the provider before using it: `rt api GET /api/env/requirements`
   must report `selected_provider` `readyset` and no Anthropic key, and
   the screen must agree. The app shows no hosted balance by design, so
   its absence is not a finding.
3. Connect PostgreSQL and ask two questions with checkable answers in Ask
   (a count, a top-N by rating). Are the answers right? With no Anthropic
   key, only hosted inference can have produced them; `rt logs --grep
   inference` shows its failures if any.
4. `rt restart`: still signed in, still on the included AI, and Ask
   still answers.
5. `rt restart --offline`, then ask again. The app must say hosted AI is
   unreachable, not blame the key, the database, or the query. Restart
   online and confirm it recovers without signing in again.
6. Sign out from the sidebar account menu. What does the gate ask for,
   and does anything still claim AI is available?

## Prove

- `selected_provider` is `readyset` from the first choice onward, without
  choosing it twice.
- Answers arrive with no Anthropic key, and stop, with a message saying
  why, while the keyservice is unreachable: hosted inference served them.
- After signing out, no AI feature works and the gate says what to do.

## Cleanup

Leave the app signed out.
