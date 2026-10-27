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

1. Before signing in, connect PostgreSQL and try to ask a question. What
   does the app ask for, and does anything on screen claim AI is available?
2. Sign in to Readyset where Ask offers it, with a fresh address from
   `rt mail address`, open the link with `rt mail open ADDRESS`, and
   choose the included AI. Where does the app land, and does it say you
   are signed in?
3. Check the provider before using it: the backend's own account of the AI
   setup (find the request the page makes for it in `rt evidence --kinds
   api` and repeat it with `rt api`) must say the included AI is in use with
   no Anthropic key, and the screen must agree. The app shows no hosted balance by design, so
   its absence is not a finding.
4. Ask two questions with checkable answers in Ask
   (a count, a top-N by rating). Are the answers right? With no Anthropic
   key, only hosted inference can have produced them; `rt logs --grep
   inference` shows its failures if any.
5. `rt restart`: still signed in, still on the included AI, and Ask
   still answers.
6. `rt restart --offline`, then ask again. The app must say hosted AI is
   unreachable, not blame the key, the database, or the query. Restart
   online and confirm it recovers without signing in again.
7. Sign out from the sidebar account menu. What does the app ask for now,
   and does anything still claim the account's AI?

## Prove

- `selected_provider` is `readyset` from signing in onward, without
  choosing it twice.
- Answers arrive with no Anthropic key, and stop, with a message saying
  why, while the keyservice is unreachable: hosted inference served them.
- After signing out, nothing claims the account's AI, and the app says
  how to get AI back.

## Cleanup

Leave the app signed out.
