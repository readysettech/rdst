# Dialogs

## Overview

A dialog is a focus-trapping overlay for a task that must be finished or
abandoned before returning to the page. Built on `Modal`
(`@rs/ui-new/modal`, Radix Dialog), which supplies the scrim, focus trap,
Escape-to-close, and a required accessible title. `ConfirmDialog`
(`@rs/ui-new/confirm-dialog`) is the ready-made decision variant.

Use a dialog only for genuinely blocking work. Transient feedback is a toast and
inline feedback is an `Alert` (see patterns/notifications).

## Variant taxonomy

| Variant | Purpose | Primary button |
| --- | --- | --- |
| **passive** | show information, one way out | `Close` (neutral) |
| **transactional** | a form / choice the user completes | `{verb}+{noun}`, `variant="primary"` |
| **danger** | confirm a destructive or high-load action | `variant="negative"` |

## Layout

- **Size to content.** `Modal` sizes are `base` (`max-w-lg`), `large`
  (`max-w-2xl`), `extra-large` (`max-w-4xl`), `full`. Pick the smallest that
  fits; don't open a `full` modal for two fields.
- **Actions: cancel left, primary right.** `ConfirmDialog` renders its footer as
  `HStack justify-end` with Cancel then the confirm button — cancel is the
  left/first, the primary is the right/last.
- **Focus the safe choice.** `ConfirmDialog` moves focus to Cancel on open
  (`onOpenAutoFocus` → `cancelRef`), so Enter doesn't fire a destructive action.
- Every dialog has an accessible title — pass `ModalContent`'s `title` (or a
  visible `ModalTitle`); `Modal` injects an sr-only fallback if you forget, but
  supply a real one.

## Destructive confirmation tiers

Match the gate to the blast radius (guideline 10):

| Tier | Action | Treatment |
| --- | --- | --- |
| **none** | reversible (toggle, filter, save draft) | no dialog |
| **confirm** | moderate, hard-to-undo | `ConfirmDialog` naming the consequence in `title` + `notice`; `confirmVariant="negative"` |
| **typed-confirm** | high-consequence, unrecoverable / remote | `ConfirmDialog` + a `children` text input the user must match before `confirmDisabled` clears |

Reference implementations:

- `ConfirmDialog` — the shared shell. `notice` renders a tinted `InlineNotice`
  naming the cost; `confirmVariant` defaults to `negative`; `blockCloseWhileLoading`
  keeps a running action from being torn down by Escape/overlay click.
- `BenchmarkConfirmDialog` (`apps/rdst`) — the **typed-confirm** exemplar: a
  local target gets a single `confirm` gate (`confirmVariant="primary"`); a
  remote target escalates to a red `notice`, a `remote-target` `Tag` accessory,
  and a typed input that must equal the target name before confirm enables.

## Usage rules (checkable)

- [ ] Dialog only for blocking work; otherwise Alert or toast.
- [ ] Built on `Modal` / `ConfirmDialog`, not a hand-rolled overlay.
- [ ] Smallest `size` that fits the content.
- [ ] Cancel left, primary right; focus opens on the safe choice.
- [ ] Destructive tier matches consequence (none / confirm / typed-confirm).
- [ ] A running destructive action is protected with `blockCloseWhileLoading`.
- [ ] The dialog has a real accessible title.

## Cross-references

- Component APIs: **components/modal**, **components/confirm-dialog**.
- Button variants and one-primary rule: **components/button**.
- The inline/toast/modal decision: **patterns/notifications**.
