# @rs/ui-new guidelines — cheat sheet

**Scope**: ten rules every screen in `apps/rdst` (and beyond) should follow
when using `@rs/ui-new` + `@rs/tailwind-base`. This is the one-page version;
the fuller corpus below expands each area into checkable rules and token
references. Start here, then follow the links.

## The corpus

Foundations (`foundations/`) — each: Overview → Usage rules → Token reference:

- [Color](foundations/color.md) · [Spacing](foundations/spacing.md) ·
  [Typography](foundations/typography.md) · [Motion](foundations/motion.md) ·
  [Icons](foundations/icons.md) · [Content & writing](foundations/content.md)

Patterns (`patterns/`):

- [Forms](patterns/forms.md) · [Dialogs](patterns/dialogs.md) ·
  [Notifications](patterns/notifications.md) ·
  [Empty states](patterns/empty-states.md) · [Loading](patterns/loading.md) ·
  [Status indicators](patterns/status-indicators.md)
- Progressive disclosure lives in [components/disclosure](components/disclosure.md)
  and the SegmentedControl "related patterns" note — no separate pattern page.

Components (`components/`) — four-part template (Usage · Style · Code ·
Accessibility):

- [Button](components/button.md) · [IconButton](components/icon-button.md) ·
  [Tag](components/tag.md) · [Alert](components/alert.md) ·
  [Modal](components/modal.md) · [ConfirmDialog](components/confirm-dialog.md) ·
  [StatusRipple](components/status-ripple.md) ·
  [Spinner/Progress/Skeleton](components/spinner-progress-skeleton.md) ·
  [CopyButton](components/copy-button.md)
- [EmptyState](components/empty-state.md) · [IconTile](components/icon-tile.md) ·
  [SegmentedControl](components/segmented-control.md) · [Tab](components/tab.md) ·
  [Disclosure](components/disclosure.md) ·
  [InteractiveRow](components/interactive-row.md) ·
  [Pressable](components/pressable.md)

Structure modeled on Carbon Design System's doc format; the rules and visuals
are `@rs/ui-new`'s own.

---

## 1. Semantic tokens everywhere, raw values nowhere

No raw hex, no raw Tailwind palette classes (`emerald-400`, `bg-amber-500/10`,
…), no ad-hoc arbitrary color values. If a color is on screen, it's a token.

**How to comply**: use `@rs/tailwind-base`'s `surface-*` / `content-*` /
`border-*` families. `check-tokens.mjs` enforces this mechanically (undefined
tokens, ad-hoc values, and raw palette classes all fail if new).

## 2. One primary button per view; emphasis is a hierarchy

Primary > secondary > tertiary/ghost. Danger is reserved for destructive
actions and is never icon-only (a lone danger icon with no label is a trap).

**How to comply**: `Button` (`@rs/ui-new/button`) — emphasis is the
`modifier` prop: pick one `modifier="solid"` per view for the primary action;
use `modifier="outline"`/`"ghost"` for the rest (`variant` is the color axis:
`primary`/`rising`/`negative`). Icon-only actions use `IconButton` (P3-1)
once it lands, never a bare danger icon.

**Which solid.** A view has exactly one page primary and it is always
`variant="primary" modifier="solid"`, the cream solid, whatever the page
does. `variant="rising"` (the purple solid) is reserved for the single brand
moment: the one control per surface that asks the user to sign in to Readyset
or take the account offer that unlocks, and nothing else. A purple solid
therefore never competes with a cream solid for the same role, and a screen
with two solids is a bug in either case. Emphasis is the `modifier` axis, so
an action steps down by becoming `outline` or `ghost`, never by changing its
`variant`.

When a control genuinely needs bespoke anatomy (for example a scrim, option
card, or operating-system chrome), use `Pressable` rather than a raw
`<button>`. It carries shared button semantics and focus treatment while the
caller owns the visual surface. It is not a substitute for `Button`,
`IconButton`, or `InteractiveRow` when one of those components fits.

## 3. Notification decision tree

Inline (`Alert`) for feedback on a user's own action, in the flow where they
took it. Toast for transient, system-generated messages. Modal only for
critical, task-blocking interruptions. Critical notices never auto-dismiss.

**How to comply**: `Alert` (`@rs/ui-new/alert`) for inline, `useToast`
(`@rs/ui-new/use-toast`) for transient, `Modal`/`ConfirmDialog`
(`@rs/ui-new/modal`, `@rs/ui-new/confirm-dialog`) for blocking. Don't
hand-roll a notice `div` — that's the adoption gap this rule closes.

## 4. Never color alone for status

Status is color + icon/shape + label, at minimum 3:1 contrast. A status dot
with no label or icon fails this rule even if the color is a real token.

**How to comply**: `StatusRipple` (`@rs/ui-new/status`) is the one status-dot
idiom — always supply its `label` prop (the prop is optional, so a color-only
dot is possible and still violates this rule). Don't hand-roll a colored
`<span>` or dot for state.

## 5. Sentence case for all UI text; fixed verb vocabulary

Sentence case, no colons after labels, `{verb}+{noun}` action labels. Verbs
aren't interchangeable: Add ≠ Create, Delete ≠ Remove, Cancel ≠ Close,
Clear ≠ Reset — pick the one that matches what actually happens.

Re-running something has exactly two verbs. **Try again** re-runs what
failed — it is `ErrorState`/`InlineNotice`'s own default, so a failed read
takes `onRetry` and no `retryLabel`. **Check again** re-runs a check that
completed, to refresh its verdict.

**How to comply**: when writing a `Button` label, `Alert` title, or dialog
title, check this list before typing a verb, and take the noun from the
[Copy](#copy--the-words-decided-once) glossary below.

## 6. One spacing scale, fixed values

Use the 16px-base spacing scale; jump steps at breakpoints rather than
picking an arbitrary in-between value.

**How to comply**: Tailwind's default spacing scale as configured in
`@rs/tailwind-base` — no arbitrary `[Npx]` spacing utilities outside the
scale's existing allowlisted exceptions.

**Radius is a role, not a value.** A container's corner comes from what it
is: `rounded-card` (a page-level card or section), `rounded-panel` (a
container nested inside one), `rounded-control` (inputs, buttons, code
shells), `rounded-pill` (chips, tags, avatars). Reach for the role name, not
`rounded-xl` and never `rounded-[20px]`.

## 7. Empty states replace the element, with fixed anatomy

An empty state is title + body + CTA, not a blank area. Three variants:
first-use, no-results, error-or-permissions — pick the one that matches why
the content is missing.

**How to comply**: `EmptyState` (P3-1) is the neutral sibling of
`error-state` (`@rs/ui-new/error-state`, already shipped) — use `EmptyState`
for the neutral/no-results/first-use cases and `ErrorState` for failures.
Don't write a local empty-state component per screen.

## 8. Skeletons for containers, spinners for actions, progress for long ops

Skeleton the data container while it loads. Spinner an in-flight action.
Progress bar a long-running operation with a known/estimable end. Show
in-flight indicators inline or full-screen depending on scope, after a short
delay (~100ms) so fast responses don't flash a loading state. An operation
measured in minutes also says how far along it is and how long it has been
running, in words the user can act on.

**How to comply**: `Skeleton` (`@rs/ui-new/skeleton`), `Spinner`
(`@rs/ui-new/spinner`), `Progress`/`CircularProgress`
(`@rs/ui-new/progress`, `@rs/ui-new/circular-progress`). Apply the ~100ms
show-delay at the call site (e.g. gate the spinner behind a short timer)
rather than showing it immediately on every state change. `Progress` fills
the container it is given; the call site sizes it. A long phase with no
countable end takes `Progress` without a `value` (indeterminate) rather than
an invented percentage.

## 9. Form contract

Top-aligned 1–3-word labels. Mark the minority (if most fields are required,
mark the optional ones, and vice versa). Validate on blur, not on every
keystroke. Helper text is swapped for error text on failure, not stacked
alongside it. In dialogs, the primary action sits on the right.

**How to comply**: `Field`/`Label` (`@rs/ui-new/field`,
`@rs/ui-new/label`) plus the `controlled-input-*` family
(`@rs/ui-new/controlled-input-text`, etc.) already wire blur-validation and
helper/error-text swapping — use them instead of a raw `<input>` + manual
error `<p>`.

## 10. Confirmation tiers follow blast radius

Blast radius picks the tier, not the screen the action happens to sit on. The
same destination and the same real work get the same tier everywhere.

| Blast radius | Tier | Shape |
|---|---|---|
| Cheap and reversible | none | Act on the click. |
| Costly but reversible — spends real time, money or database traffic, leaves nothing behind | confirm | `ConfirmDialog` naming the cost, `confirmVariant="primary"`, warning notice. Never red, never typed. |
| Destructive or irreversible — deletes data, changes a security boundary, or runs load against a database the user does not own | destructive confirm | `ConfirmDialog` with the noun in the title (`Delete guard "pii-mask"?`), `accent: 'negative'` notice, `confirmVariant="negative"`. |
| Destructive *and* not recreatable from the UI, or aims sustained real load at a database the user does not own | typed confirm | The destructive confirm plus `requireTyped={name}`. |

State the consequence once. The title names the thing, the notice names what
happens to it, and the confirm label repeats the title's verb — a dialog that
says the same fact three times, or flips Delete into "removed" halfway down,
is one statement badly spent.

A confirm must never open on a dead button with no way forward. The typed tier
opens with its input focused, since typing the name is the one thing that makes
the confirm live. Any other reason the confirm is unavailable is named on screen
next to it (`confirmDisabledReason`) — and where the caller can simply restore
what the run needs, it does that instead of opening a dialog the user cannot
finish.

**How to comply**: `ConfirmDialog` (`@rs/ui-new/confirm-dialog`) carries all
three dialog tiers, `requireTyped` included — pick the tier that matches the
blast radius, don't build a bespoke confirmation modal and don't hand-roll the
typed-confirm input.

---

## Copy — the words, decided once

Rule 5 fixes the shape of a string; this fixes the words in it. One name per
thing, in the user's vocabulary, everywhere it is rendered — including
`aria-label`s, toasts, tooltips and empty states.

**Casing.** Sentence case for every rendered string, including page titles,
tab labels, section headings, table column headers, form labels and tags.
Only the `overline` token uppercases, and only over a short category word —
never over a sentence or a database identifier. Names keep their own case:
Readyset, PostgreSQL, MySQL, Docker, Anthropic, YAML, and SQL keywords
(`EXPLAIN ANALYZE`, `WHERE`).

**Voice.** Second person, present tense, active. Say what the user gets, not
how it is built: no "hosted", no "inference", no "capped".

**Glossary.**

| Term | Means | Not |
|---|---|---|
| target | a database RDST is configured against | connection, database connection, instance |
| database | the user's actual database engine or data | target (when you mean the configured entry) |
| included AI | the free AI that comes with a Readyset account; first mention "the free AI included with a Readyset account" | Readyset-hosted AI, hosted inference, trial credits |
| Analyze | the single-query EXPLAIN ANALYZE run and its report | analysis run, query analysis |
| Compare against Readyset | measuring a query on the origin and on Readyset | Compare, Compare & test, benchmark |
| Load test | running concurrent load against origin and Readyset | benchmark, stress test |
| Health check | the whole-database audit | audit, health report |

A page title equals the nav label or breadcrumb that led there. Say each
fact once per screen: a verdict, a summary and a tag repeating the same
sentence is one statement, not three.

---

These ten rules were extracted from an audit of Carbon Design System's
documented usage rules, adapted for `@rs/ui-new`'s own visual identity and
components (not Carbon's).
