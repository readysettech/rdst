# Status indicators

## Overview

Status is communicated by **color + shape/icon + label**, never color alone
(guideline 4). A color-only dot fails for color-blind users and for anyone
scanning quickly. `StatusRipple` (`@rs/ui-new/status`) is the one status-dot
idiom; `Tag` carries status as a labelled chip. Don't hand-roll a colored
`<span>` for state.

## Rules

- **Never color alone.** A `StatusRipple` must have its `label` — the prop is
  optional in the API, so a label-less dot is possible and still violates this
  rule. Pair every status color with text (or a distinct icon + text).
- **≥3:1 contrast** for the status color against its background (the `content-*-plain`
  tokens the dot uses are tuned for this).
- **Cap the vocabulary.** Keep to ≤5–6 distinct statuses in one view; beyond
  that, users can't hold the mapping. The tone set is `positive` / `negative` /
  `warning` / `informative` / `rising` / `primary`.
- Map tone to meaning consistently across the app (green = healthy, red =
  failed, amber = attention) — see Color for the semantics.
- A `StatusRipple` animates a ripple; under `prefers-reduced-motion` the meaning
  must still be carried by the dot + label, not the animation (see Motion).

## StatusRipple vs Tag

- **StatusRipple** — a live/at-a-glance state next to a name (a target's
  health, a run's liveness). Dot + label, subtle pulsing ripple.
- **Tag** — a discrete labelled value or category (`remote-target`, a count, a
  TTL). Use `variant="neutral"` for values with no semantic tone rather than
  borrowing `warning`-amber.

## Usage rules (checkable)

- [ ] Every status has color + shape/icon + a text label.
- [ ] `StatusRipple` always receives `label`.
- [ ] Status color meets ≥3:1 contrast.
- [ ] ≤5–6 distinct statuses per view.
- [ ] Tone→meaning mapping is consistent app-wide.
- [ ] Meaning survives `prefers-reduced-motion` (not animation-only).

## Cross-references

- Component API: **components/status-ripple**, **components/tag**.
- Tone semantics and contrast: **foundations/color**.
- Reduced-motion rule: **foundations/motion**.
