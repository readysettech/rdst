# Content & writing

## Overview

UI copy is part of the design system. Consistent wording is what makes an
action's outcome predictable before the user commits to it. These rules apply to
every string in the product: button labels, dialog titles, `Alert`/`Tag` text,
form labels, empty states, toasts.

## Rules (checkable)

- [ ] **Sentence case** everywhere — "Save changes", not "Save Changes" or
      "SAVE CHANGES". (`overline` is the one uppercased level, by token.)
- [ ] **No colons after labels** — "Email", not "Email:".
- [ ] **`{verb}+{noun}` action labels** — "Add cache", "Delete row",
      "Clear filters". A lone verb ("Submit", "OK") is a last resort.
- [ ] **Second person** — "your queries", "you don't have access".
- [ ] **Contractions are fine** — "don't", "you're", "can't". They read as the
      product's voice, not a lapse.
- [ ] Sentence-final punctuation on body/help text; none on labels, titles, or
      single-line captions.

## Verb vocabulary

Verbs are not interchangeable — pick the one that matches what actually happens,
because the user reads the verb to predict the outcome:

| Verb | Means | Not |
| --- | --- | --- |
| **Add** | attach an existing thing to a set | Create |
| **Create** | bring a new thing into existence | Add |
| **Delete** | destroy permanently, unrecoverable | Remove |
| **Remove** | detach from a set; the thing still exists | Delete |
| **Cancel** | abandon an in-progress action, no changes kept | Close |
| **Close** | dismiss a surface; the state behind it is unchanged | Cancel |
| **Clear** | empty a value / selection | Reset |
| **Reset** | return to a default state | Clear |

Do — "Remove from cache" (the query still exists) vs "Delete query" (it's gone).
Don't — a "Delete" button that only detaches, or a "Close" that discards edits.

## Do / don't pairs

| Do | Don't |
| --- | --- |
| Clear filters | Reset Search: |
| No queries match your filters | Empty. |
| You don't have access to this fleet | User is not authorized. |
| Run benchmark | SUBMIT |

## Cross-references

- Action-label placement and emphasis: **Button**, **patterns/dialogs**.
- Empty-state copy per case: **patterns/empty-states**.
- Notification copy (inline vs toast vs modal): **patterns/notifications**.
