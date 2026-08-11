# Pressable

## Usage

`Pressable` is the narrow escape hatch for bespoke button anatomy: dialog
scrims, option cards, operating-system chrome, and other controls whose visual
surface cannot be represented by `Button`, `IconButton`, or `InteractiveRow`.

Do not use it for ordinary actions. A labeled action is a `Button`, an
icon-only action is an `IconButton`, and a dense interactive row is an
`InteractiveRow`.

## Style

`Pressable` deliberately owns no radius, padding, color, or typography. The
caller owns those visual decisions. It does provide the shared focus-visible
ring and disabled behavior so bespoke controls remain consistent with the
rest of the system.

## Code

```tsx
import { Pressable } from '@rs/ui-new/pressable'

<Pressable
  aria-label="Close navigation"
  className="fixed inset-0 bg-surface-overlay"
  onClick={close}
/>
```

## Accessibility

- `type="button"` is the default.
- Provide visible text or an `aria-label`.
- Do not nest another interactive control inside a `Pressable`.
- Preserve a meaningful disabled state when the action is unavailable.
