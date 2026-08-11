# IconButton

An icon-only action button. Shares `Button`'s color matrix and press/focus
affordances, requires an accessible `label`, and wraps itself in a Tooltip by
default so the action is discoverable without a visible text label.

Import: `import { IconButton } from '@rs/ui-new/icon-button'`

Retires the ~8 icon-only `<button>` hand-rolls counted in the P3 audit (toolbar
actions, row overflow triggers, close buttons).

---

## Usage

Use an IconButton for a compact, recognizable action where a full `Button` with
text would crowd the layout — toolbars, table rows, card headers, dense panels.

**When to use**
- Space-constrained actions with a well-understood glyph (copy, edit, close,
  more/overflow, refresh).
- Repeated per-row or per-item actions where text labels would be noise.

**When not to use**
- The single primary action of a view — that deserves a labelled `Button`
  (Guideline 2: one primary button per view, emphasis is a hierarchy).
- Destructive/danger actions. A lone danger icon with no visible label is a
  trap (Guideline 2). If the action is destructive, use a labelled `Button`
  with `variant="negative"`, or route it through `ConfirmDialog`. `variant`
  `negative` exists here only for rare, unmistakable cases (e.g. a labelled
  "remove row" that also shows a confirmation) — reach for it deliberately.
- An unfamiliar or ambiguous glyph where users cannot predict the outcome.

**Do**
- Give every IconButton a `label` that is a `{verb}+{noun}` action phrase
  ("Copy SQL", "Delete row"), in sentence case.
- Keep the tooltip on (default) so the label is discoverable on hover/focus.
- Use `modifier` for emphasis (`solid` > `outline` > `ghost`), matching the
  surrounding Button hierarchy.

**Don't**
- Ship a bare icon button with no `label` (the prop is required for this
  reason).
- Use a `negative` solid IconButton as the go-to for delete — prefer a labelled
  button for destructive intent.
- Disable the tooltip unless a visible adjacent label already names the action.

---

## Style

Color comes from `Button`'s `variant × modifier` compounds (see the Button
docs) — `IconButton` only overrides sizing to meet the touch-target rule.

| Size | Box | Rounding | Glyph | Hit target |
| --- | --- | --- | --- | --- |
| `large` | `w-12 h-12` | `rounded-3xl` | `w-5 h-5` (20px) | 48px |
| `base` (default) | `w-11 h-11` | `rounded-2xl` | `w-4 h-4` (16px) | 44px |
| `small` | `w-8 h-8` | `rounded-xl` | `w-4 h-4` (16px) | 32px |

`base` clears the 44px minimum touch target (Carbon icon rule) while the glyph
stays ~16px. `small` is below 44px — reserve it for pointer-dense desktop
contexts (dense tables), not primary touch surfaces.

Loading swaps the glyph for a `Spinner`; the spinner border color is inherited
from the active modifier's `[&_#loader]:border-t-*` token.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `icon` | `IconStrokeName` | — | Glyph to render (required). |
| `label` | `string` | — | Accessible name → `aria-label` + default tooltip (required). |
| `variant` | `'primary' \| 'rising' \| 'negative'` | `'primary'` | Color axis. |
| `modifier` | `'solid' \| 'outline' \| 'ghost'` | `'solid'` | Emphasis axis. |
| `size` | `'large' \| 'base' \| 'small'` | `'base'` | See table above. |
| `disabled` | `boolean` | `false` | — |
| `loading` | `boolean` | `false` | Shows a spinner and disables the button. |
| `tooltip` | `boolean \| string` | `true` | `true` = show `label`; string = custom copy; `false` = no tooltip. |
| `className` / `classMerge` | `string` | — | Extra classes on the button. |

Also accepts native `<button>` attributes (`onClick`, `id`, `data-*`, …).

### Example

```tsx
import { IconButton } from '@rs/ui-new/icon-button'

<IconButton
  icon="copy"
  label="Copy SQL"
  variant="primary"
  modifier="ghost"
  onClick={copy}
/>
```

Custom tooltip copy, or none:

```tsx
<IconButton icon="refresh" label="Refresh results" tooltip="Refresh (last run 2m ago)" />
<IconButton icon="settings" label="Open settings" tooltip={false} />
```

---

## Accessibility

- Renders a native `<button type="button">` with `aria-label={label}`; the glyph
  is decorative (`label=""` + `aria-hidden`) so it is announced once.
- The default Tooltip (Radix) makes the label visible on hover and keyboard
  focus. Keep it on unless a visible adjacent label already provides the name.
- Keyboard: reachable with `Tab`, activated with `Enter` / `Space`.
- Caveat: a `disabled` button does not fire pointer events, so its tooltip will
  not open on hover. If users must read why an action is unavailable, keep the
  button enabled and explain on click, or place the explanation elsewhere.
