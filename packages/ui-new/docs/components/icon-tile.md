# IconTile

The brand gradient tile that fronts a heading: a rounded square with a
`from-surface-primary-soft` gradient and a glyph in `content-primary-soft`.
Purely decorative — it dresses a title, it is not a control.

Import: `import { IconTile } from '@rs/ui-new/icon-tile'`

Retires the 20 pasted `bg-gradient-to-br from-surface-primary-soft to-surface-*`
tile copies counted in the P3 audit (page heroes, section headers, gradient
empty states), including the one off-token `from-violet-500/20` copy.

---

## Usage

Use an IconTile as the visual anchor beside a page or section heading, or inside
a first-use empty state where a little brand warmth is wanted.

**When to use**
- Page/section hero next to an `h1`/`h2` title.
- A first-use empty state that should feel inviting rather than neutral.

**When not to use**
- As a button or link. It has no interactive affordance — use `IconButton`.
- As the neutral icon inside a "no results" / "nothing here" `EmptyState`; that
  uses a flat `surface-layout-2` tile on purpose (save the gradient for
  first-use / hero moments).
- As a status indicator — use `StatusRipple` / `Tag` where color carries state.

**Do**
- Pair it with a visible heading; leave it decorative (default).
- Pick an `accent` that matches the section's semantic tone (`info` for neutral
  informational surfaces, `positive`/`warning` where the section already carries
  that meaning).

**Don't**
- Attach `onClick` and treat it as a button.
- Override the glyph color to a raw value — pass a semantic `content-*` token
  via `iconClassName` if you must deviate.

---

## Style

Always `bg-gradient-to-br` from `surface-primary-soft` to the accent's soft
surface. Glyph defaults to `text-content-primary-soft`.

| Size | Box | Rounding | Glyph |
| --- | --- | --- | --- |
| `sm` | `w-8 h-8` | `rounded-xl` | `w-4 h-4` |
| `base` | `w-10 h-10` | `rounded-xl` | `w-5 h-5` |
| `lg` (default) | `w-12 h-12` | `rounded-2xl` | `w-6 h-6` |

| Accent | Second gradient stop |
| --- | --- |
| `info` (default) | `to-surface-info-soft` |
| `warning` | `to-surface-warning-soft` |
| `positive` | `to-surface-positive-soft` |
| `negative` | `to-surface-negative-soft` |
| `rising` | `to-surface-rising-soft` |
| `primary` | `to-surface-primary-soft` (flat, single-tone) |

The default `lg` + `info` matches the most common existing call site (the
queries page hero: `w-12 h-12 rounded-2xl … to-surface-info-soft`).

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `icon` | `IconStrokeName` | — | Glyph (required). |
| `size` | `'sm' \| 'base' \| 'lg'` | `'lg'` | See table. |
| `accent` | `'info' \| 'warning' \| 'positive' \| 'negative' \| 'rising' \| 'primary'` | `'info'` | Second gradient stop. |
| `label` | `string` | `''` (decorative) | Set only when the tile is the sole carrier of meaning. |
| `iconClassName` | `string` | — | Override the glyph color/size (semantic tokens only). |
| `className` | `string` | — | Class on the tile container. |

### Example

```tsx
import { IconTile } from '@rs/ui-new/icon-tile'
import { HStack, VStack } from '@rs/ui-new/stack'
import { Text } from '@rs/ui-new/text'

<HStack className="gap-4 items-center">
  <IconTile icon="folder-file" accent="info" />
  <VStack className="gap-1 items-start">
    <Text as="h1" level="headline-3" className="text-content-layout-1">
      Queries
    </Text>
    <Text level="body-small" className="text-content-layout-3">
      Your database's queries — live, saved, and ready to analyze.
    </Text>
  </VStack>
</HStack>
```

---

## Accessibility

- Decorative by default: the glyph renders with `label=""` and
  `aria-hidden="true"`, so screen readers skip it and announce the adjacent
  heading only.
- Provide `label` only in the rare case where the tile is the sole carrier of
  meaning (no visible text names it); the glyph is then announced with that
  label.
- IconTile is not focusable and takes no keyboard interaction — it is not a
  control.
