# Color

## Overview

Every color on screen is a semantic token from `@rs/tailwind-base`
(`style.css`). Tokens name a *role*, not a hue, so the same component reads
correctly in light and dark (all `apps/rdst` shells ship
`data-color-mode="dark"`, so the dark values are the ones you see). There are
three token prefixes, one per surface a pixel can belong to:

| Prefix | Applies to | Tailwind utility |
| --- | --- | --- |
| `surface-*` | backgrounds and fills | `bg-surface-…` |
| `content-*` | text, icons, glyphs (foreground) | `text-…`, `border-t-content-…` |
| `border-*` | strokes, dividers, outlines | `border-border-…` |

Within each prefix there are two kinds of family: **layout** (neutral chrome)
and the six **semantic tones** — `primary`, `rising`, `positive`, `negative`,
`warning`, `info` (spelled `informative` on component `variant` props).

## The layer model

Depth is carried by a lightness step, not by borders — the only depth cue that
reads on a dark canvas (`style.css` §4.2). Pick the surface for what the element
*is*, then pair the matching elevation shadow:

| Surface token | Use for | Pair with |
| --- | --- | --- |
| `surface-layout-1` | the resting card / page canvas | `shadow-elevation-0` (none) |
| `surface-layout-2` | secondary panels, insets, toolbars | — |
| `surface-raised` | the one hero / selected row / active panel | `shadow-elevation-1` |
| `surface-overlay` | dropdown, popover, modal, drawer | `shadow-elevation-2` / `-3` |
| `surface-scrim` | the dim backdrop behind an overlay | — |

Rules:

- One `surface-raised` element per view — raising two things flattens both.
- Modals/popovers use `surface-overlay` + `shadow-elevation-3`; `ConfirmDialog`
  already does this (`bg-surface-overlay shadow-elevation-3`).
- Never hand-roll a backdrop with `bg-[black]/50`; use `surface-scrim`.

## Status-color semantics

Tone is meaning, not decoration — do not pick a tone for its hue:

| Tone | Hue (dark) | Means |
| --- | --- | --- |
| `primary` | sand / neutral | the default, highest-emphasis action; neutral chrome |
| `rising` | purple | brand accent, in-progress, featured (progress bars use it) |
| `positive` | grass green | success, healthy, cached |
| `negative` | tomato red | error, failure, destructive |
| `warning` | orange | caution, needs attention, reversible risk |
| `info` | indigo | neutral information, not-good-not-bad |
| `neutral` (Tag only) | grey | a value with no semantic tone (TTL, counts) |

Do not borrow `warning`-amber for a neutral value — use `neutral` (see Tag).

## solid / soft / plain

Each tone ships three emphasis treatments. Match content to surface:

- **solid** — high-emphasis filled: `bg-surface-{tone}-solid` +
  `text-content-{tone}-solid`. `content-{tone}-solid` is the on-color tuned for
  AA on the fill (e.g. white on `surface-rising-solid`).
- **soft** — tinted low-emphasis: `bg-surface-{tone}-soft` +
  `text-content-{tone}-soft` (± `border-border-{tone}-soft`).
- **plain** — foreground only, no fill: `text-content-{tone}-plain` on a neutral
  surface. `StatusRipple` dots and `Progress` use the plain tokens (the
  `primary` StatusRipple color is the exception — it maps to `-soft`).

Interactive fills add `-hover` / `-active` steps (`surface-{tone}-solid-hover`,
…). Don't set hover colors by hand.

## Usage rules (checkable)

- [ ] No raw hex, no palette classes (`emerald-400`, `bg-amber-500/10`), no
      ad-hoc arbitrary color values. `check-tokens.mjs` fails on new ones.
- [ ] AA-critical text on `surface-layout-1/2` uses `content-layout-1` or
      `content-layout-2`. `content-layout-3` (tertiary grey) is AA there
      (≈4.87:1 dark) but **fails AA on `surface-raised`/`surface-overlay`**
      (≈4.22:1 on overlay). `check-tokens.mjs` fails any element that puts
      `text-content-layout-3` on `bg-surface-raised`/`bg-surface-overlay`; use
      `content-layout-2` there.
- [ ] Text on a `-solid` fill uses that tone's `content-{tone}-solid` on-color,
      never `content-layout-*`.
- [ ] Status/semantic tone is chosen for meaning, not hue.
- [ ] One `surface-raised` element per view; overlays use `surface-overlay`.

## Token reference

Layout family (dark values):

| Token | Backs |
| --- | --- |
| `surface-layout-1` | resting card / page canvas |
| `surface-layout-2` | secondary panel / toolbar |
| `surface-layout-soft` | subtle inset |
| `surface-layout-disabled` | disabled fills, skeletons |
| `surface-raised` | hero / selected / active (one per view) |
| `surface-overlay` | dropdown / popover / modal / drawer |
| `surface-scrim` | overlay backdrop dim |
| `content-layout-1` | primary text (highest contrast) |
| `content-layout-2` | secondary text; AA-critical text on raised/overlay |
| `content-layout-3` | tertiary text (layout surfaces only) |
| `content-layout-disabled` | disabled text |
| `border-layout-1` | default hairline / divider |
| `border-layout-2` | stronger divider |
| `border-layout-soft` | faint divider |

Per-tone (repeat for `primary` · `rising` · `positive` · `negative` ·
`warning` · `info`):

| Token pattern | Role |
| --- | --- |
| `surface-{tone}-solid` (+ `-hover`/`-active`) | filled high-emphasis fill |
| `surface-{tone}-soft` (+ `-hover`/`-active`) | tinted low-emphasis fill |
| `content-{tone}-solid` | on-color for the solid fill |
| `content-{tone}-soft` | text/icon on the soft fill |
| `content-{tone}-plain` | text/icon on a neutral surface (no fill) |
| `border-{tone}-solid` / `border-{tone}-soft` | tone strokes |

Data-viz series (compared series differ by contrast + label, not hue alone):

| Token | Series |
| --- | --- |
| `content-viz-cache` | Readyset / cached (teal-green) |
| `content-viz-origin` | Postgres / origin (amber) |
