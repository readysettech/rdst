# Tag

A small labelled chip for a discrete value, category, or status — a TTL, a
count, `remote-target`, a query state. Not a control (it renders a `<div>`),
though it can be made `clickable` as a filter pill.

Import: `import { Tag } from '@rs/ui-new/tag'`

---

## Usage

**When to use**
- A discrete value or category displayed inline (metadata, labels, states).
- A compact, immediately-applied filter pill (with `clickable`).

**When not to use**
- A command → `Button` (a tag is a value, not a verb).
- A live at-a-glance health state next to a name → `StatusRipple`.
- A form selection among options → radio / `SegmentedControl`.

**Pick the tone for meaning.** `variant` carries semantic tone; use `neutral`
for a value with no status meaning (TTL, diff magnitude, "waiting") rather than
borrowing `warning`-amber. `muted` is the low-contrast placeholder tone (e.g.
un-annotated schema placeholders).

**Do**
- Sentence-case the `label`; keep it to a word or two.
- Use `variant="neutral"` for plain values; reserve status tones for status.

**Don't**
- Attach behavior expecting button semantics — it's a `div`; if it must be a
  real control, use `Button`/`IconButton`.
- Use a status tone decoratively where no status is implied.

---

## Style

`tv()`, all tokens. Each `variant × modifier` compound sets fill/text/border:
`solid` is a filled chip, `outline` a bordered transparent chip, `ghost` a soft
fill. `neutral`/`muted` map to `content-layout-*` greys.

| Size | Height | Padding | Radius | Text |
| --- | --- | --- | --- | --- |
| `base` (default) | `h-7` | `py-2 px-3` | `rounded-md` | `label-small` |
| `small` | `h-6` | `py-1 px-2` | `rounded-sm` | `label-extra-small` |

`clickable` adds `cursor-pointer` and an `active:scale-[0.98]` press; focus shows
`shadow-focus`.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | `string` | — | Chip text and icon accessible name (required). |
| `variant` | `'primary' \| 'rising' \| 'negative' \| 'informative' \| 'warning' \| 'positive' \| 'neutral' \| 'muted'` | `'primary'` | Semantic tone. |
| `modifier` | `'solid' \| 'outline' \| 'ghost'` | `'solid'` | Emphasis. |
| `size` | `'base' \| 'small'` | `'base'` | See table. |
| `icon` | `IconStrokeName` | — | Glyph; position via `iconPosition`. |
| `iconPosition` | `'none' \| 'left' \| 'left-full' \| 'right' \| 'right-full' \| 'icon'` | `'none'` | `icon` = icon-only. |
| `clickable` | `boolean` | `false` | Adds pointer affordance + press. |
| `disabled` | `boolean` | `false` | Sets `aria-disabled`, dims, blocks pointer. |
| `fullWidth` | `boolean` | `false` | Stretches to container width. |
| `className` / `classMerge` | `string` | — | Extra classes. |

Also accepts native `<div>` attributes (`onClick`, `id`, `data-*`, …).

### Example

```tsx
import { Tag } from '@rs/ui-new/tag'

<Tag variant="neutral" label="TTL 30s" />
<Tag variant="negative" modifier="solid" size="small" label="remote-target" />
<Tag variant="positive" icon="tick" iconPosition="left" label="Cached" />
```

---

## Accessibility

- Renders a `<div>`. When `disabled`, it sets `aria-disabled` and blocks pointer
  events, but it is not a focusable control.
- If a tag needs to be operable (a real filter toggle), prefer a `Button` /
  `IconButton` so it gets button semantics and keyboard support; `clickable`
  alone provides visual affordance, not roles.
- The `icon`'s accessible name is the `label`; a purely decorative icon beside
  visible text is announced once via that shared label.
