# Alert

An inline notification banner — feedback on the user's own action, placed in the
flow where they took it (a form error, the result of a save). The inline branch
of the notification decision tree (see patterns/notifications).

Import: `import { Alert } from '@rs/ui-new/alert'`

---

## Usage

**When to use**
- Feedback on a user action, shown next to its cause (not floating page-top).
- A persistent inline notice the user should see until they resolve it.

**When not to use**
- Transient, system-generated messages → `useToast` (auto-dismisses).
- Critical, task-blocking interruptions → `Modal` / `ConfirmDialog`.
- A hand-rolled notice `div` — that's the adoption gap this closes.

**Match tone to status** (see patterns/notifications taxonomy): `positive`
success, `negative` error, `warning` caution, `informative` neutral info,
`primary` plain, `rising` accent.

**Do**
- Place it inline at the point of the action.
- Keep the `label` a sentence-case, specific message.
- Use `children` for a trailing action or extra detail.

**Don't**
- Auto-dismiss an Alert carrying something the user must act on.
- Use it for background/system messages (that's a toast).

---

## Style

`tv()`, all tokens. `variant × modifier` sets the fill/text/border the same way
Button/Tag do: `solid` filled, `outline` bordered (`border-(length:--border-base)`),
`ghost` soft-filled. Exception: `informative` defines only `solid` and
`outline` — `informative` + `ghost` type-checks but renders toneless, so don't
use it (source gap, tracked in the P3 plan).

| Size | Padding | Radius | Text |
| --- | --- | --- | --- |
| `base` (default) | `py-3 px-3` | `rounded-2xl` | `label-small` |
| `small` | `py-1 px-2` | `rounded-md` | `label-extra-small` |

Content is top-aligned (`items-start`) so multi-line messages read well. Loading
swaps the leading icon for a `Spinner`.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | `string` | — | Message text and icon accessible name (required). |
| `variant` | `'primary' \| 'rising' \| 'negative' \| 'informative' \| 'warning' \| 'positive'` | `'primary'` | Semantic tone. |
| `modifier` | `'solid' \| 'outline' \| 'ghost'` | `'solid'` | Emphasis. |
| `size` | `'base' \| 'small'` | `'base'` | See table. |
| `icon` | `IconStrokeName` | — | Leading/trailing glyph via `iconPosition`. |
| `iconPosition` | `'none' \| 'left' \| 'left-full' \| 'right' \| 'right-full' \| 'icon'` | `'none'` | — |
| `loading` | `boolean` | `false` | Shows a spinner in the icon slot. |
| `disabled` | `boolean` | `false` | Dims and blocks pointer. |
| `fullWidth` | `boolean` | `false` | Stretches to container width. |
| `onClick` | `() => void` | — | Optional; the whole banner is clickable. |
| `children` | `ReactNode` | — | Trailing content (action, detail). |
| `className` | `string` | — | Extra classes. |

### Example

```tsx
import { Alert } from '@rs/ui-new/alert'

<Alert
  variant="negative"
  icon="alert"
  iconPosition="left"
  label="Couldn't save changes. Check your connection and try again."
/>
```

---

## Accessibility

- Alert renders a `<div>` (with an `onClick` handler), styled as a banner. It
  does **not** set `role="alert"` and is not a focusable control, so it is not
  automatically announced or keyboard-operable on its own.
- For a message that must be announced to assistive tech, wrap it in a live
  region (`role="status"` for polite, `role="alert"` for assertive) at the call
  site, or use `ErrorState` (which announces as `role="alert"`) for failures.
- If the whole Alert is clickable, provide an explicit interactive child
  (a `Button`) for keyboard users rather than relying on the div's `onClick`.
- The icon's accessible name is the `label`; a decorative icon beside the text is
  announced once via that shared label.
