# Typography

## Overview

Type comes from one component: `Text` (`@rs/ui-new/text`). Pick a `level` and
the token sets size, line-height, letter-spacing, and weight together — you
never hand-set `font-size` or `font-weight`. Set the semantic element with `as`
(`h1`…`h6`, `p`, `span`, `label`, …); `level` and `as` are independent, so a
visual size never forces the wrong tag.

```tsx
<Text as="h1" level="headline-3" className="text-content-layout-1">Queries</Text>
<Text level="body-small" className="text-content-layout-3">Running text.</Text>
```

## Level roles

Every level defined in `text.tsx` / `style.css`:

| Level | Role |
| --- | --- |
| `stat-hero` | the single big number per screen (tabular-nums) |
| `display-large` / `-medium` / `-small` | marketing / hero display type |
| `headline-1` … `headline-5` | page and section headings |
| `subtitle-1` / `subtitle-2` | card titles, medium-emphasis leads |
| `button-large` / `-medium` / `-small` | button and control labels |
| `label-large` … `label-extra-small` | form labels, tags, chips, metadata |
| `body-large` / `-medium` / `-small` | running paragraph text |
| `caption` | secondary metadata under a value |
| `overline` | uppercased eyebrow label |
| `mono-small` / `-medium` / `-large` | code, SQL, IDs, hashes |

## Weight discipline

The scale carries exactly **two weights**, and the level chooses for you:

- **400 (regular)** — `body-*`, `caption`, and `mono-*` (running/read text).
- **500 (medium)** — everything else: displays, headlines, subtitles, buttons,
  labels, overline, `stat-hero`.

Don't add `font-bold` / `font-semibold` to reach for a third weight — emphasis
comes from moving up a level (e.g. `subtitle-1` over `body-medium`), a color
token, or `strong`, not from a heavier face the scale doesn't define.

## Neutral running text

Body copy is neutral: `content-layout-1` for primary text, `content-layout-2`
for secondary, `content-layout-3` for tertiary (on layout surfaces only — see
Color). Reserve tone colors (`content-positive-*`, `content-warning-*`, …) for
status and emphasis, never for whole paragraphs.

## Usage rules (checkable)

- [ ] Text renders through `Text`; no ad-hoc `text-[15px]` / `font-[600]`.
- [ ] `as` sets the semantic tag; `level` sets the look — chosen independently.
- [ ] No `font-bold` / `font-semibold`; emphasis is level, color, or `strong`.
- [ ] Running text uses a neutral `content-layout-*`, not a tone color.
- [ ] Exactly one `stat-hero` per screen.
- [ ] Numeric columns/metrics use `mono-*` or `stat-hero` for tabular digits.

## Token reference

Sizes (rem → px at 16px root), from `style.css`:

| Level | Size | Line-height | Weight |
| --- | --- | --- | --- |
| `stat-hero` | 3rem / 48px | 3.6rem | 500 |
| `display-large` | 4rem / 64px | 4.8rem | 500 |
| `display-medium` | 3rem / 48px | 3.6rem | 500 |
| `display-small` | 2.5rem / 40px | 3rem | 500 |
| `headline-1` | 2rem / 32px | 3rem | 500 |
| `headline-2` | 1.5rem / 24px | 2rem | 500 |
| `headline-3` | 1.25rem / 20px | 1.75rem | 500 |
| `headline-4` | 1.125rem / 18px | 1.5rem | 500 |
| `headline-5` | 1.0625rem / 17px | 1.5rem | 500 |
| `subtitle-1` | 0.9375rem / 15px | 1.5rem | 500 |
| `subtitle-2` | 0.8125rem / 13px | 1.25rem | 500 |
| `button-large` | 0.9375rem / 15px | 1.5rem | 500 |
| `button-medium` | 0.875rem / 14px | 1.5rem | 500 |
| `button-small` | 0.8125rem / 13px | 1.25rem | 500 |
| `label-large` | 0.9375rem / 15px | 1.5rem | 500 |
| `label-medium` | 0.875rem / 14px | 1.5rem | 500 |
| `label-small` | 0.8125rem / 13px | 1.25rem | 500 |
| `label-extra-small` | 0.75rem / 12px | 1.25rem | 500 |
| `body-large` | 1rem / 16px | 1.5rem | 400 |
| `body-medium` | 0.9375rem / 15px | 1.5rem | 400 |
| `body-small` | 0.875rem / 14px | 1.5rem | 400 |
| `caption` | 0.8125rem / 13px | 1.25rem | 400 |
| `overline` | 0.75rem / 12px | 1.25rem | 500 (uppercased) |
| `mono-large` | 0.875rem / 14px | 1.25rem | 400 |
| `mono-medium` | 0.8125rem / 13px | 1.25rem | 400 |
| `mono-small` | 0.75rem / 12px | 1.25rem | 400 |
