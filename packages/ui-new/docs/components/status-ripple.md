# StatusRipple

The one status-dot idiom: a colored dot with a soft pulsing ripple and a text
label, for a live at-a-glance state (a target's health, a run's liveness).
Status is color + shape + label — never color alone (guideline 4, see
patterns/status-indicators).

Import: `import { StatusRipple } from '@rs/ui-new/status'`

---

## Usage

**When to use**
- A live/ongoing state shown next to a name or in a row.

**When not to use**
- A discrete value or category → `Tag`.
- A command → `Button`.
- A one-off decorative dot — this component implies "live status".

**Always pass `label`.** The prop is optional in the API, so a color-only dot is
possible — and it violates the never-color-alone rule. Give every StatusRipple a
short label ("Healthy", "Connected", "Failed").

**Do**
- Map `color` to meaning consistently (positive = healthy, negative = failed,
  warning = attention) — see foundations/color.
- Keep to ≤5–6 distinct statuses per view.

**Don't**
- Ship it without `label`.
- Rely on the ripple animation to convey state (it's suppressed under
  reduced-motion — the dot + label must still carry the meaning).

---

## Style

`tv()` slots (`container`, `dot`, `ripple`, `text`), all tokens. The dot and
ripple use the tone's `content-{tone}-plain` (or `-soft`) color; the label is
`label-small` in the matching content token. Two `motion/react` ripples scale and
fade on a 1.5s loop.

| `color` | Token family |
| --- | --- |
| `positive` (default) | `content-positive-plain` |
| `negative` | `content-negative-plain` |
| `warning` | `content-warning-plain` |
| `informative` | `content-info-plain` |
| `rising` | `content-rising-plain` |
| `primary` | `content-primary-soft` |

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `label` | `string` | — | Status text. Optional in the type, but **always supply it**. |
| `color` | `'primary' \| 'rising' \| 'positive' \| 'negative' \| 'warning' \| 'informative'` | `'positive'` | Semantic tone. |
| `className` | `string` | — | Class on the wrapping `HStack`. |

### Example

```tsx
import { StatusRipple } from '@rs/ui-new/status'

<StatusRipple color="positive" label="Healthy" />
<StatusRipple color="negative" label="Disconnected" />
```

---

## Accessibility

- The label is rendered as visible text (`Text level="label-small"`), so the
  status is read by both sighted and assistive-tech users — provided you pass it.
- The ripple is decorative animation; it carries no information the dot + label
  don't. Ensure meaning survives `prefers-reduced-motion` (foundations/motion).
- Color meets ≥3:1 against its background via the plain tokens, but color is
  never the sole signal — the label is.
