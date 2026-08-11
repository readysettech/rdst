# EmptyState

The neutral empty state: a titled, centered replacement for a region that has
no content — not a blank area. The neutral sibling of `ErrorState`; it shares
the same centered composition but carries no accent, glow, or recovery
contract.

Import: `import { EmptyState } from '@rs/ui-new/empty-state'`

Retires the three local `EmptyState` definitions counted in the P3 audit
(`TopQueryTable`, `ScanResultsTable`, `SchemaEmptyState`) across ~7 render
sites. (Guideline 7: empty states replace the element with a fixed anatomy.)

---

## Usage

An empty state is title + body + (optional) CTA. Pick the copy for the reason
the content is missing — the anatomy is the same for all three cases, so this is
one component, not three:

- **First-use** — nothing has been created yet. Body sets expectations; the CTA
  starts the primary flow ("Create your first cache"). This is the one case
  where an `IconTile` (brand gradient) may replace the neutral icon via `media`.
- **No results** — a filter or search returned nothing. Body suggests loosening
  the query; the CTA (if any) clears filters.
- **Restricted / permissions** — the user cannot see this content. Body says
  what is needed; the CTA routes to request access or go back.

**When to use**
- A list, table, or panel that is legitimately empty (not loading, not errored).

**When not to use**
- A failure. Use `ErrorState` (`@rs/ui-new/error-state`) — it carries the
  accent, the "which results remain trustworthy" line, retry, and the technical
  detail expander.
- A loading state. Use `Skeleton` / `Spinner` (Guideline 8).
- A per-screen bespoke empty `div`. That is the adoption gap this closes.

**Do**
- Write the `title` as a short positive statement, sentence case.
- Offer at most one primary `action`; keep `secondaryAction` link-styled.
- Use `layout="compact"` inside a table cell or a short slot.

**Don't**
- Leave a raw blank area with no title or guidance.
- Stack two solid buttons — primary is solid, secondary is a link.
- Use it to report an error (that is `ErrorState`).

---

## Style

| Part | Token(s) |
| --- | --- |
| Icon tile | `bg-surface-layout-2`, `rounded-2xl` (neutral, not the brand gradient) |
| Glyph | `text-content-layout-3` |
| Title | `text-content-layout-1` (`subtitle-1`) |
| Body | `text-content-layout-3` (`body-small`), `max-w-md`, `leading-relaxed` |
| Primary action | `Button` `variant="primary" modifier="solid"` |
| Secondary action | `Button` `variant="primary" modifier="link"` |

| Layout | Padding | Tile | Glyph |
| --- | --- | --- | --- |
| `block` (default) | `px-6 py-16`, `gap-4` | `w-14 h-14` | `w-7 h-7` |
| `compact` | `px-4 py-8`, `gap-3` | `w-10 h-10` | `w-5 h-5` |

For the first-use brand-warm variant, pass an `IconTile` as `media` instead of
the neutral `icon` tile.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `icon` | `IconStrokeName` | — | Glyph shown in the neutral tile. Ignored if `media` is set. |
| `media` | `ReactNode` | — | Custom visual (e.g. an `IconTile` or illustration) replacing the icon tile. |
| `title` | `string` | — | Positive headline (required). |
| `body` | `ReactNode` | — | Supporting sentence. |
| `action` | `{ label; onClick; icon? }` | — | Primary CTA (solid button). |
| `secondaryAction` | `{ label; onClick; icon? }` | — | Secondary CTA (link-styled). |
| `layout` | `'block' \| 'compact'` | `'block'` | Page/panel vs in-table. |
| `className` | `string` | — | Class on the root. |

### Example

```tsx
import { EmptyState } from '@rs/ui-new/empty-state'

// No-results
<EmptyState
  icon="search"
  title="No queries match your filters"
  body="Try widening the time range or clearing the search."
  action={{ label: 'Clear filters', onClick: clearFilters }}
/>

// First-use, with a brand IconTile
<EmptyState
  media={<IconTile icon="folder-file" accent="info" />}
  title="No cached queries yet"
  body="Analyze a query to see whether Readyset can cache it."
  action={{ label: 'Analyze a query', icon: 'sparkles', onClick: analyze }}
  secondaryAction={{ label: 'Learn about caching', onClick: openDocs }}
/>
```

---

## Accessibility

- Renders as a neutral region with a visible `h3` title; it is not an `alert`
  (nothing failed) — unlike `ErrorState`, which uses `role="alert"`.
- The icon is decorative (`label=""` + `aria-hidden="true"`); the visible title
  carries the meaning, so it is announced once.
- Actions are real `Button`s: reachable with `Tab`, activated with
  `Enter` / `Space`, and labelled by their text.
- When supplying `media`, ensure any meaningful image inside it has its own
  accessible text; keep purely decorative art hidden from assistive tech.
