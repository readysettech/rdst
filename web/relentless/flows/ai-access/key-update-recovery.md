# Flow: ai-access/key-update-recovery

Capability: ai-access
Areas: ai-access, settings-targets
Modes: desktop, browser
Minutes: 12
Covers: web-apps/apps/rdst/src/components/EnvSecretsDialog.tsx web-apps/apps/rdst/src/components/AiProviderGate.tsx web-apps/apps/rdst/src/components/AiAccessBoundary.tsx web-apps/apps/rdst/src/lib/useAnthropicValidity.ts web-apps/apps/rdst/src/lib/useEnvRequirements.ts rdst/shared/api/routes/env.py rdst/shared/llm_manager/key_resolution.py
Direct: web-apps/apps/rdst/src/features/settings/SettingsPage.tsx rdst/shared/anthropic_env.py

A person's Anthropic key goes bad or gets replaced. The app must say so,
let them recover from wherever they are, and never lose a working key to
an unchecked one.

## Mission

Start from a fresh install and add the real key where the app first asks
for AI (`rt fill-secret TARGET anthropic-api-key`). Then:

1. In Settings, AI, use "Update key" with a key that is plainly wrong
   (`sk-ant-not-a-real-key`). What does the app check before saving, and
   what does it say? Does the working key survive?
2. Make the saved key invalid the way it happens in life: save the wrong
   key if the app allows it, or restart after saving it. What does the
   gate show, and can the person reach Settings to fix it?
3. Recover with the real key from each place the app offers (the gate's
   key dialog, Settings), and confirm Ask works afterward
   (`rt api GET /api/env/requirements` should agree with the screen).
4. `rt restart`: the recovered key must still be there.

## Prove

- The screen and `/api/env/requirements` agree at every step.
- A wrong key never replaces a working one without the person being told.
- After recovery and a restart, the gate does not return.

## Cleanup

Leave the real key saved.
