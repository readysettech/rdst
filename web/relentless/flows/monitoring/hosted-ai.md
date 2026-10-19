# Flow: monitoring/hosted-ai

Capability: monitoring
Areas: monitoring, ai-access
Modes: desktop, browser
Minutes: 25
Covers: rdst/features/monitor/analyze_queue.py rdst/features/monitor/judge.py rdst/features/monitor/database_limit.py rdst/shared/llm_manager/hosted_provider.py

A user signed in to Readyset, with no Anthropic key, turns on AI monitoring
and expects RDST to analyze problem queries by itself with Readyset-hosted
AI. Every analysis must come from the schedule and from hosted inference.

## Mission

This flow needs mail: if `rt status` says mail is not available, record the
flow as blocked. Start from a fresh install and add no Anthropic key. Then:

1. Sign in to Readyset with a fresh address from `rt mail address`, opening
   the link with `rt mail open ADDRESS`, and choose the included AI.
   `rt api GET /api/env/requirements` must report `selected_provider`
   `readyset` and no Anthropic key.
2. Connect PostgreSQL and turn on monitoring with deep analysis. Run varied
   SELECTs, some slow, and wait. Analyses must start without an "Analyze
   now" click; follow one from queued to finished and read its plan, numbers,
   and parameter provenance. `rt api GET /api/monitor/limit` shows what the
   account allows.
3. Turn deep analysis off in "Monitoring settings" and run "Analyze now" by
   hand: it still works, and the result says how it was measured.
4. `rt restart`, then check the provider, account, and monitoring settings
   are unchanged, and that automatic analysis resumes from the schedule.

## Prove

- Hosted responses only, with no Anthropic key before or after.
- Automatic analyses are scheduler-started and reach a finished state; a
  queued or running one is not a finished analysis.
- The page and the API agree on what was analyzed and what remains.

## Cleanup

Turn monitoring off, leave no analysis running, and sign out.
