# Button

The labelled action control. Carries the design system's radii, focus ring, and
emphasis hierarchy — a raw `<button>` bypasses all of it, which is why raw
buttons are guardrailed (see below).

Import: `import { Button } from '@rs/ui-new/button'`

For icon-only actions use `IconButton` (`@rs/ui-new/icon-button`) — same color
matrix, a required `label`, and a built-in tooltip.

---

## Usage

**When to use**
- Any command the user triggers — submit, run, open a dialog, apply.

**When not to use**
- Icon-only actions → `IconButton`.
- A selection among peers → `SegmentedControl` / radio (a segment is a
  selection, not a verb).
- Navigation between distinct URLs-as-pages → a real link/route, not a Button.

**One primary per view.** Emphasis is a hierarchy: exactly one high-emphasis
action per view, the rest stepped down. Two solid buttons competing for the eye
is the most common misuse.

**The two axes.** Color and emphasis are separate props:

- `variant` — the **color** axis: `primary` (default, neutral/brand), `rising`
  (purple accent), `negative` (destructive).
- `modifier` — the **emphasis** axis: `solid` (default) > `outline` > `ghost` >
  `link`.

Pick one `modifier="solid"` per view for the primary action; use `outline` /
`ghost` for secondary and tertiary.

**`link` modifier** — styles the button as an inline text link (underlined, no
fill) for low-emphasis, navigation-ish actions inside prose ("Learn more",
"View details"). Note it still renders a `<button>` element (see Accessibility);
for real cross-URL navigation use a route link, not a `link`-modifier Button.

**Danger.** `variant="negative"` is for destructive actions and is never
icon-only — a lone danger icon with no label is a trap. Route serious
destruction through `ConfirmDialog` (see patterns/dialogs).

**When a raw `<button>` is legitimately bespoke.** Almost never in app code.
`check-components.mjs` count-pins raw `<button>` per file: a small set of
non-Button interactive elements — dialog scrims, listbox/option rows,
dropdown-menu triggers — render their own `<button>` for event-target reasons
and aren't Button candidates; those are already in the pinned baseline. A raw
`<button>` in a new file fails the check. Reach for `Button`/`IconButton` unless
you're building one of those primitives.

**Do**
- Give a `{verb}+{noun}` `label` in sentence case ("Run benchmark").
- Use the button's `loading` prop for in-flight actions (it disables + shows a
  spinner) instead of a separate spinner.

**Don't**
- Ship two `solid` buttons competing as primary.
- Use `variant` for emphasis or `modifier` for color — they're orthogonal.
- Make a destructive action icon-only.

---

## Style

Built with `tv()`; all colors are `@rs/tailwind-base` tokens. Color comes from
the `variant × modifier` compounds — e.g. `primary`+`solid` is
`bg-surface-primary-solid` / `text-content-primary-solid` with `-hover`/`-active`
steps; `outline` uses `border-border-{tone}-soft` on a transparent fill; `ghost`
a soft fill; `link` an underlined transparent text button.

| Size | Height | Padding | Radius | Text |
| --- | --- | --- | --- | --- |
| `large` | `h-12` | `py-3 px-4` | `rounded-3xl` | `button-medium` |
| `base` (default) | `h-10` | `py-3 px-4` | `rounded-2xl` | `button-medium` |
| `small` | `h-8` | `py-3 px-3` | `rounded-xl` | `button-small` |

Press: `active:scale-[0.98]`. Focus: a 2px `ring-border-primary-soft` with
offset. Loading swaps the icon for a `Spinner` whose color inherits from the
active modifier's `[&_#loader]:border-t-*` token.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | `string` | — | Visible text and accessible name (required). |
| `variant` | `'primary' \| 'rising' \| 'negative'` | `'primary'` | Color axis. |
| `modifier` | `'solid' \| 'outline' \| 'ghost' \| 'link'` | `'solid'` | Emphasis axis. |
| `size` | `'large' \| 'base' \| 'small'` | `'base'` | See table. |
| `icon` | `IconStrokeName` | — | Glyph; position via `iconPosition`. |
| `iconPosition` | `'none' \| 'left' \| 'left-full' \| 'right' \| 'right-full' \| 'icon'` | `'none'` | `*-full` justifies label and icon apart; `icon` is icon-only (prefer `IconButton`). |
| `loading` | `boolean` | `false` | Shows spinner and disables. |
| `disabled` | `boolean` | `false` | — |
| `fullWidth` | `boolean` | `false` | Stretches to container width. |
| `children` | `ReactNode` | — | Extra inner content beside the label. |
| `className` / `classMerge` / `innerClassName` | `string` | — | Extra classes. |

Also accepts native `<button>` attributes (`onClick`, `type`, `id`, `data-*`, …).

### Example

```tsx
import { Button } from '@rs/ui-new/button'

// Primary action
<Button label="Run benchmark" icon="play" iconPosition="left" onClick={run} />

// Secondary
<Button label="Cancel" modifier="ghost" onClick={close} />

// Destructive, in a confirm flow
<Button label="Delete cache" variant="negative" onClick={confirm} loading={pending} />
```

---

## Accessibility

- Renders a native `<button type="button">` (override `type` for form submits).
  Reachable with `Tab`, activated with `Enter` / `Space`.
- `label` is the accessible name; a `label`-less button is not possible (the prop
  is required).
- Focus shows a visible 2px ring with offset — don't remove it.
- Caveat: `modifier="link"` is a visual style on a `<button>`, not an `<a>`. It
  gives no `href` and no link semantics; for navigation to a URL use a route
  link so it's announced and behaves as a link (open-in-new-tab, etc.).
- A disabled button doesn't fire pointer events; if users need to know *why*
  it's unavailable, explain it nearby rather than relying on a tooltip.
