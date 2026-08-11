# Forms

## Overview

Forms are built from the `@rs/ui-new` form family — `Field` / `Label`
(`@rs/ui-new/field`, `@rs/ui-new/label`) plus the `controlled-input-*` inputs
(`controlled-input-text`, …), which already wire blur-validation and the
helper/error-text swap. Reach for a raw `<input>` + hand-written error `<p>`
only when no form component fits; that is the adoption gap this pattern closes.

## Layout & labels

- **Top-aligned labels**, one per field, above the control. Keep them **1–3
  words** in sentence case, no trailing colon (see Content).
- One field per row by default; group related fields with the between-group
  spacing rule (see Spacing) so sections read apart.

## Mark the minority

Don't mark every field. Mark whichever set is smaller:

- Mostly-required form → mark the **optional** fields ("Notes (optional)").
- Mostly-optional form → mark the **required** fields (an asterisk or
  "(required)").

State the convention once near the form so the marks are unambiguous.

## Validation

- **Validate on blur**, not on every keystroke — don't flag a field the user is
  still typing into.
- On failure, **replace the helper text with the error text**; don't stack the
  error beneath the helper. The `controlled-input-*` components do this swap.
- Error text names the fix ("Enter a valid email"), not just the fault
  ("Invalid").
- Re-validate on the next blur / on submit so a corrected field clears promptly.

## Buttons

- The submit button is the form's one primary action (see Button).
- **In dialogs, the primary action sits on the right**, cancel to its left —
  matching `ConfirmDialog` (see patterns/dialogs).
- Disable submit while the request is in flight and show the button's `loading`
  state rather than a separate spinner.

## Usage rules (checkable)

- [ ] Labels are top-aligned, 1–3 words, sentence case, no colon.
- [ ] The minority (required *or* optional) is marked, with the convention
      stated once.
- [ ] Validation fires on blur, not per keystroke.
- [ ] Error text replaces helper text; the two never stack.
- [ ] Built from `Field`/`Label` + `controlled-input-*`, not raw inputs.
- [ ] In dialogs, primary right / cancel left; submit shows `loading`.

## Cross-references

- Button emphasis and placement: **components/button**.
- Dialog action layout and destructive tiers: **patterns/dialogs**.
- Inline vs toast feedback on submit: **patterns/notifications**.
