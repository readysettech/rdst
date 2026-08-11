# Spacing

## Overview

`style.css` does not override `--spacing-*`, so the spacing scale is Tailwind
4's default: a single 0.25rem (4px) step, where `1rem = 16px` is the root
rhythm. Every gap, padding, and margin is a step on that scale (`gap-2` = 8px,
`p-6` = 24px, …) — never an arbitrary in-between pixel value.

Think in the 16px base rhythm and step up or down by whole scale values. To
change density at a breakpoint, jump to a different step
(`gap-2 laptop:gap-4`) rather than reaching for a bespoke `[13px]`.

## The group-spacing rule

Space *between* groups is larger than space *within* a group — that gap
hierarchy is what makes structure legible without borders. Concretely, in the
components today: tight clusters (icon + label, dot + text) use `gap-1`/`gap-2`;
form rows and button rows use `gap-2`/`gap-3`; stacked sections and dialog
bodies use `gap-4`/`gap-5`; panel padding is `p-6`.

- Do — `gap-1` inside a control, `gap-5` between the sections that hold them.
- Don't — one uniform `gap-3` everywhere; groups stop reading as groups.

## Usage rules (checkable)

- [ ] Spacing is a scale step; no arbitrary `[Npx]` outside the token check's
      allowlist.
- [ ] Within-group gap < between-group gap on the same screen.
- [ ] Density changes at a breakpoint jump to another scale step, not a custom
      value.
- [ ] Breakpoints come from the named tokens below, not raw `min-width`.

## Token reference

Common steps (Tailwind default, 4px base):

| Utility | Value | Typical use |
| --- | --- | --- |
| `gap-1` / `p-1` | 4px | icon-to-label, dot-to-text |
| `gap-2` / `p-2` | 8px | button rows, tag internals |
| `gap-3` / `p-3` | 12px | form-control padding |
| `gap-4` / `p-4` | 16px | base rhythm, card padding |
| `gap-5` | 20px | between stacked sections |
| `gap-6` / `p-6` | 24px | dialog / panel padding |

Breakpoints (`style.css`):

| Token | Min width |
| --- | --- |
| `tablet` | 48rem (768px) |
| `laptop` | 64rem (1024px) |
| `desktop` | 80rem (1280px) |
