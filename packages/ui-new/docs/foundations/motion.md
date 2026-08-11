# Motion

## Overview

Motion has two vocabularies that share a duration ladder:

- **CSS transitions** for state changes — `transition duration-{token}
  ease-{token}` on hover/press/open. The `style.css` default is
  `duration-fast` (150ms) + `ease-base`, which is what `Button`, `Tag`, and
  `Alert` use.
- **framer-motion** (`motion/react`) for enter/exit and physical animation —
  driven through `getTransition()` (`@rs/ui-new` motion helper), whose default
  is `cubicFast` (0.3s). `Modal` animates this way; the Radix-driven overlays
  (dropdown, popover, tooltip, toast) animate via `data-state` utilities and
  the `--rs-overlay-*` custom properties in `style.css`.

We adopt Carbon's *duration-ladder + easing-roles* concept, not its curves.

## Roles

Assign a role, then pick the duration/easing that matches:

| Role | When | Duration | Easing |
| --- | --- | --- | --- |
| **standard** | in-place state change (hover, press, color, toggle) | `duration-fast` (150ms) | `ease-base` |
| **entrance** | an element appearing (menu, toast, modal, disclosure open) | `duration-normal`–`slow` (200–300ms) | `ease-out` |
| **exit** | an element leaving | shorter than its entrance (`duration-fast`) | `ease-in` |

Entrances are decelerating (`ease-out`) and can be slightly longer so the eye
catches them; exits are accelerating (`ease-in`) and quicker so dismissal feels
responsive. Keep durations short — nothing routine should exceed
`duration-slowest` (500ms).

## Reduced motion

Honor `prefers-reduced-motion`. Non-essential motion — the `StatusRipple`
ripples, spring physics, slide/scale entrances — should collapse to an instant
or opacity-only change; never gate information behind an animation that a
reduced-motion user won't see.

```css
@media (prefers-reduced-motion: reduce) {
  *, ::before, ::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

For framer-motion, guard with the `useReducedMotion()` hook and drop
transforms, keeping opacity.

## Usage rules (checkable)

- [ ] Durations and easings are tokens (`duration-*`, `ease-*`), not raw ms.
- [ ] State changes use the standard role; entrances/exits use their roles.
- [ ] Exit is not slower than its matching entrance.
- [ ] Every non-essential animation degrades under `prefers-reduced-motion`.
- [ ] No animation is the sole carrier of state (pair with a static cue).

## Token reference

Durations (`style.css`):

| Token | Value |
| --- | --- |
| `duration-fastest` | 50ms |
| `duration-faster` | 100ms |
| `duration-fast` | 150ms (default transition) |
| `duration-normal` | 200ms |
| `duration-slow` | 300ms |
| `duration-slower` | 400ms |
| `duration-slowest` | 500ms |

Easings (`style.css`):

| Token | Curve | Role |
| --- | --- | --- |
| `ease-base` | `cubic-bezier(0.2, 0.4, 0, 1)` | default / standard |
| `ease-out` | `cubic-bezier(0, 0, 0.2, 1)` | entrance |
| `ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | exit |
| `ease-in-out` | `cubic-bezier(0.4, 0, 0.2, 1)` | symmetric moves |
| `ease-default` | `cubic-bezier(0.4, 0, 0.2, 1)` | generic |
| `ease-linear` | `linear` | spinners, marquees |
