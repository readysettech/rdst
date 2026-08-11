# Notifications

## Overview

Three surfaces carry messages, and they are not interchangeable. Picking the
wrong one either buries a message the user must act on, or interrupts them for
one they don't care about. This is the decision tree (guideline 3).

## The decision tree

```
Is the message a response to an action the user just took?
├─ Yes → Does the user have to act on it before continuing?
│        ├─ Yes, it blocks the task → Modal / ConfirmDialog
│        └─ No, just confirm/explain → Alert, inline where they acted
└─ No (system-generated, background) → useToast (transient)
```

| Surface | Component | Use for | Dismissal |
| --- | --- | --- | --- |
| **Inline** | `Alert` (`@rs/ui-new/alert`) | feedback on the user's own action, in the flow where they took it (form error, result of a save) | stays until resolved |
| **Toast** | `useToast` (`@rs/ui-new/use-toast`) | transient, system-generated messages ("Cache refreshed") | auto after a few seconds |
| **Modal** | `Modal` / `ConfirmDialog` | critical, task-blocking interruption | user action only |

## Rules

- **Never auto-dismiss a critical message.** If the user must know or act, use
  an `Alert` or `Modal` — not a toast that disappears.
- Toast auto-dismiss timing comes from Radix Toast's own `duration` default —
  the store's `TOAST_REMOVE_DELAY` constant does not govern it; tune duration
  at the Radix layer, not the store.
- **One toast at a time** (`useToast` pins `TOAST_LIMIT = 1`); don't stack
  system messages.
- Inline `Alert` belongs next to its cause, not floating at the top of the page.
- Don't hand-roll a notice `div` — that's the adoption gap this rule closes.

## Status taxonomy

Match the tone to the message, using each component's `variant`:

| Tone | `Alert` / `Toast` variant | Means |
| --- | --- | --- |
| positive | `positive` | success, completed |
| negative | `negative` | error, failed |
| warning | `warning` | caution, needs attention |
| informative | `informative` | neutral information |
| neutral | `primary` | plain acknowledgement |

`Alert` also has `rising`; `Toast` defaults to `primary` (neutral).

## Usage rules (checkable)

- [ ] Message routed by the decision tree: inline / toast / modal.
- [ ] Critical or actionable messages never auto-dismiss.
- [ ] Inline `Alert` sits at the point of action, not page-top.
- [ ] Toasts are system-generated and transient; at most one shown.
- [ ] `variant` matches the status taxonomy.
- [ ] No bespoke notice `div`.

## Cross-references

- Component APIs: **components/alert**, **components/modal**.
- Blocking-decision layout and destructive tiers: **patterns/dialogs**.
- Status color + icon + label rule: **patterns/status-indicators**.
