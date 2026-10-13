# ErrorState / InlineNotice

The failure surfaces of the shared error contract: what failed, the safe cause,
which results remain trustworthy, one routed recovery action, an optional retry,
and technical detail behind an expander. `ErrorState` is the section or page
state; `InlineNotice` is the compact sibling for a slot inside other content.

Import: `import { ErrorState, InlineNotice } from '@rs/ui-new/error-state'`

---

## Usage

**When to use**
- A region failed to load or an action failed, and the user needs the cause and
  the way back (`ErrorState`).
- A condition inside a panel or form blocks part of it: missing credentials, an
  unreachable database, a provider that is not set up (`InlineNotice`).

**When not to use**
- Nothing failed and the region is just empty: `EmptyState`.
- Feedback on the user's own action next to its control: `Alert`.
- A transient system message: a toast.

**Do**
- Pick `errorClass` for the failure; it sets the status tone and default glyph.
- Offer `onRetry` only when retrying can plausibly help.
- Use `tone="quiet"` where the surrounding page follows the quiet language (one
  inline-alert line, no tiles).

**Don't**
- Put a raw driver dump in `message`; use `detail`.

---

## Style

`tone="default"` (default):

- `ErrorState layout="card"`: a tinted bordered card (`rounded-xl`, accent soft
  surface and border, `shadow-glow-*`) with a 48px glyph tile, a `headline-4`
  title in the accent, an outline action and a ghost retry.
- `ErrorState layout="page"`: a centered state for boundaries with a 64px glowing
  badge, an optional `eyebrow` overline, a `headline-3` title and a solid action.
- `InlineNotice`: the tinted bordered box without glow, a 16px glyph, a
  `label-small` title in the accent and ghost actions.

`tone="quiet"` renders both components, in either layout, as the inline-alert
line: a `bg-surface-layout-2` row (`rounded-panel`, `px-3.5 py-3`, no border), a
14px glyph, then the title (or the message when there is no title) as a
`body-small` sentence in the status tone, the message in `content-layout-2`, the
trustworthy note as a third-tone caption, and the action and retry as small
ghost buttons without icons at the right. `negative` and `warning` keep their
tone; `info` (`valid-negative`) is neutral. The default glyph is `alert`
(`info` for neutral) unless `icon` is passed. No tile, badge, eyebrow or glow.

---

## Code

### Props (shared)

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `title` | `string` | - | What failed (required; `''` on InlineNotice for no heading). |
| `titleAs` | `'h1'` to `'h6'` | `'h3'` (`'h1'` on the page layout) | Heading level. |
| `message` | `string` | - | The safe cause (required). |
| `trustworthy` | `string` | - | Which results remain valid. |
| `action` | `{ label; onClick; icon? }` | - | Recovery action (`icon` ignored when quiet). |
| `onRetry` | `() => void` | - | Retry. |
| `retryLabel` | `string` | `'Try again'` | - |
| `detail` | `string` | - | Technical detail behind "Technical details". |
| `errorClass` | `ErrorClass` | `'database'` | Tone and glyph. |
| `icon` | `IconStrokeName` | - | Overrides the glyph. |
| `tone` | `'default' \| 'quiet'` | `'default'` | `quiet`: the inline-alert line. |
| `className` | `string` | - | Class on the root. |

`ErrorState` adds `layout: 'card' | 'page'` and `eyebrow` (default tone only);
`InlineNotice` adds `accent: 'negative' | 'warning' | 'info'`.

### Example

```tsx
<ErrorState
  tone="quiet"
  errorClass="rdst-service"
  title="The Inbox could not load"
  message="RDST could not read the items. Nothing was changed."
  onRetry={refetch}
/>
```

---

## Accessibility

- Both render `role="alert"`, so the failure is announced when it appears.
- The title is a real heading (`titleAs`) so the region shows in the outline.
- The glyph is decorative when a title is visible and labelled "Notice" when the
  title is empty.
- The detail expander is a button with `aria-expanded` / `aria-controls`.
