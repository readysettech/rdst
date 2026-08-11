# Icons

## Overview

Icons render through `Icon` (`@rs/ui-new`, from `../svg/icon`), backed by an SVG
sprite. Every icon is monochromatic (`stroke-current`) and takes its color from
the surrounding `content-*` token — icons never carry their own palette. The
`name` is typed to the sprite variant (`IconStrokeName`), so a wrong name is a
compile error, not a blank glyph.

## Sizes

`Icon` has a `size` variant, but in practice glyphs are sized to their host:

| Context | Size | Pixels |
| --- | --- | --- |
| Standalone / list glyph | `size="small"` | 16px (`h-4 w-4`) |
| Inside `Button` / `Tag` (default) | `size="base"` | 20px (`h-5 w-5`) |
| Inline in a dense control (SegmentedControl) | `w-3.5` | 14px |
| Section header / feature glyph | `size="medium"` / `large` | 24px / 32px |
| Empty-state / hero tile glyph | via the tile | 20–28px |

16px is the default standalone size; 14px (`w-3.5`) is the inline size for
dense controls where 16px would crowd the label. Don't invent in-between px.

## Accessibility & touch

- `Icon`'s `label` is **required**. A decorative icon — one whose meaning is
  already carried by adjacent visible text — passes `label=""`, which hides it
  from assistive tech (via Radix `AccessibleIcon`). A meaningful icon gets a
  real `{verb}+{noun}` label.
- Interactive icons need a **44px minimum touch target** even when the glyph is
  ~16–20px. `IconButton` `base` (`w-11 h-11`) clears 44px; pad smaller triggers
  to reach it.

## Usage rules (checkable)

- [ ] Icons come from `Icon`; no inline `<svg>` or raster icons.
- [ ] Color is a `content-*` token via `stroke-current`; icons are single-color.
- [ ] Decorative icon → `label=""`; meaningful icon → descriptive label.
- [ ] Standalone glyph 16px, in-control glyph 20px, dense inline 14px (`w-3.5`).
- [ ] Interactive icon has a ≥44px hit target (use `IconButton`).
- [ ] Icon reinforces a text label; it is not the only carrier of a destructive
      action's meaning (see Button / Status).

## Token reference

`Icon` `size` variants (`icon.tsx`):

| Size | Box | Stroke |
| --- | --- | --- |
| `small` | 16px (`h-4 w-4`) | `stroke-2` |
| `base` (default) | 20px (`h-5 w-5`) | `stroke-[1.5px]` |
| `medium` | 24px (`h-6 w-6`) | `stroke-[1.5px]` |
| `large` | 32px (`h-8 w-8`) | `stroke-[1.25px]` |
| `xlarge` | 40px (`h-10 w-10`) | `stroke-[1.25px]` |
| `xxlarge` | 80px (`h-20 w-20`) | `stroke-1` |
