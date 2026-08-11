# Spinner · Progress · Skeleton

The three loading indicators — one per kind of wait (guideline 8, see
patterns/loading). Skeleton for a container filling in, Spinner for an in-flight
action, Progress for a long op with a known end.

Imports:
- `import { Spinner } from '@rs/ui-new/spinner'`
- `import { Progress } from '@rs/ui-new/progress'`
- `import { CircularProgress } from '@rs/ui-new/circular-progress'`
- `import { Skeleton } from '@rs/ui-new/skeleton'`

---

## Usage

| Component | Use for | Not for |
| --- | --- | --- |
| `Skeleton` | a data container's shape while it loads | a running action |
| `Spinner` | an in-flight action (button, small fetch) | a whole container's content |
| `Progress` / `CircularProgress` | a long op with `value`/`max` | an unknown-length wait (use Spinner) |

**Do**
- Skeleton the layout you're replacing so the page doesn't jump on load.
- Prefer a button's built-in `loading` (Button/IconButton) over a standalone
  Spinner for actions.
- Gate any indicator behind a ~100ms show-delay at the call site so fast
  responses don't flash it.

**Don't**
- Use a determinate Progress without a real `value` — a fake bar misleads.
- Full-screen a spinner for a local wait.

---

## Style

- **Skeleton** — `animate-pulse` on `bg-surface-layout-disabled`; you size it
  with `className` to match the content it stands in for.
- **Spinner** — a rotating ring (`border-t-…`) at `duration`-linear; `size`
  `base` (16px) / `large` (24px); `color` picks the ring tone.
- **Progress** — a `w-60 h-4` track (`bg-border-layout-1`) with a
  `content-rising-plain` indicator that transitions on `value`.
- **CircularProgress** — an SVG ring that springs to `value`, showing the
  percentage; the ring turns `content-negative-plain` past 80%.

---

## Code

### Spinner

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `size` | `'base' \| 'large'` | `'base'` | 16px / 24px. |
| `color` | `'layout' \| '{tone}-solid' \| '{tone}-soft'` | `'layout'` | Ring color (tones: primary/rising/positive/negative/warning/info). |

### Progress

| Prop | Type | Notes |
| --- | --- | --- |
| `value` | `number` | Current value (required). |
| `max` | `number` | Maximum (required). |

### CircularProgress

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `value` | `number` | — | Current value (required). |
| `max` | `number` | — | Maximum (required). |
| `size` | `number` | `60` | SVG px. |
| `strokeWidth` | `number` | `8` | Ring thickness. |

### Skeleton

Accepts native `<div>` props; size and shape it via `className`.

### Example

```tsx
// Container loading
{isLoading ? <Skeleton className="h-6 w-40" /> : <Text>{name}</Text>}

// Action loading — prefer the button's own state
<Button label="Save" loading={pending} onClick={save} />

// Long op
<Progress value={done} max={total} />
```

---

## Accessibility

- `Progress` builds on Radix Progress (a `progressbar` with value semantics).
  Provide accurate `value`/`max`.
- `CircularProgress` is presentation-only SVG — no `progressbar` role, no
  `aria-value*`, and its percentage text is a bare SVG `<text>` node invisible
  to assistive tech. Pair it with an `aria-label` or a live region at the call
  site, exactly as you would a Spinner.
- `Spinner` and `Skeleton` are visual only. For a wait that assistive tech
  should hear, pair with a live region (`role="status"`, e.g. "Loading…") at the
  call site.
- All three animate; under `prefers-reduced-motion` the pulse/rotation/spring
  should reduce, and the loading meaning must not depend on the animation
  (foundations/motion).
