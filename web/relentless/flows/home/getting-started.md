# Flow: home/getting-started

Capability: home
Areas: home, onboarding-connect, ai-access
Modes: desktop, browser
Minutes: 15
Covers: web-apps/apps/rdst/src/features/home web-apps/apps/rdst/src/components/GetStarted.tsx

Home leads a new user from an empty install to a watched database in three
steps: AI access, a database, monitoring. Every step it marks done, and every
caption under a done step, must match what is really set up.

## Mission

1. On a fresh install, read Home before doing anything. Then choose "Use my
   own Anthropic key instead" and add your key with `rt fill-secret`. What
   does the finished first step say, and does it match Settings and
   `rt api GET /api/env/requirements`?
2. Add PostgreSQL without turning monitoring on. Read Home again, then
   reload and `rt restart`: the steps, their captions and the one action
   offered must stay true at each point.
3. Turn monitoring on, from Home if it offers a way and otherwise from the
   Database page; say which. Does Home move to the watched-database view, and
   does every link on it open what it names, on the right database?
4. When `rt status` says mail is available, sign in by email and repeat
   steps 1 to 3 on a second fresh home: does Home now describe the Readyset
   account instead, and say whose it is?

## Prove

- No step is marked done that is not, and no caption names an AI setup that
  is not the one in use.
- Each stage survives reload and restart unchanged.

## Cleanup

Turn monitoring off, remove the databases you added, and sign out if you
signed in.
