# CopyButton

A one-tap "copy to clipboard" control. Wraps `Button` (`modifier="link"`,
`size="small"`) with a copy glyph that flips to a tick and the label "Copied"
for 2s after a successful copy, then resets. It can show a visible resting
label when the copied value needs a clearer affordance.

Import: `import { CopyButton } from '@rs/ui-new/copy-button'`

---

## Usage

**When to use**
- Beside a copyable value — a SQL snippet, a connection string, an ID, a hash.

**When not to use**
- As the primary action of a view (it's a low-emphasis link button).
- For non-clipboard actions (it only writes `text` to the clipboard).

**Sibling, not nested (composition rule, p1).** Place the CopyButton as a
**sibling** of the content it copies — never nest it inside another interactive
element (a clickable row, a card that's itself a button, another `<button>`).
Nesting produces invalid interactive-in-interactive markup and a broken tab/click
target. When a value sits inside a clickable container, lift the copy control out
to sit next to the value, not within the clickable region.

- Do — `<div class="row"><code>{sql}</code><CopyButton text={sql} /></div>` where
  the row is a plain container.
- Don't — put `<CopyButton>` inside a `<button>`/clickable card, or wrap the
  whole row in an `onClick` that swallows the copy.

**Do**
- Pass the exact string to copy as `text`.
- Keep it adjacent to what it copies so the affordance is obvious.
- Add a short visible `label` when an icon alone would be ambiguous.

**Don't**
- Rely on it as the only way to obtain a critical value — also show the value.

---

## Style

Renders a `Button` with `modifier="link"`, `size="small"`,
`className="no-underline"`. Resting state: `copy` icon, icon-only unless
`label` is provided. Copied state (2s): `tick-double` icon with the
`copiedLabel` shown to the left. Inherits Button's press/focus affordances.

---

## Code

### Props

| Prop | Type | Notes |
| --- | --- | --- |
| `text` | `string` | The string written to the clipboard (required). |
| `label` | `string` | Optional visible resting label. |
| `copiedLabel` | `string` | Optional confirmation label; defaults to "Copied". |

The component manages its own copied/reset state; there are no other props.

### Example

```tsx
import { CopyButton } from '@rs/ui-new/copy-button'
import { HStack } from '@rs/ui-new/stack'

<HStack className="gap-2 items-center">
  <code className="text-mono-small">{connectionString}</code>
  <CopyButton text={connectionString} label="Copy connection string" />
</HStack>
```

---

## Accessibility

- Its resting accessible name is "Copy" (not "Copied") — the label reflects the
  action available, not the last outcome (p1 QW6).
- It is a real `Button` (`<button>`): reachable with `Tab`, activated with
  `Enter` / `Space`.
- Keep it a sibling of the copyable content so it isn't nested inside another
  control — nested interactive elements break keyboard and screen-reader
  navigation.
- The copied state is a transient visual confirmation; for a change assistive
  tech must hear, add a polite live region ("Copied") at the call site.
