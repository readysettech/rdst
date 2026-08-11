# Empty states

## Overview

An empty region is never left blank — it is replaced by a titled state that says
why it's empty and what to do next (guideline 7). Two components cover this:
`EmptyState` (`@rs/ui-new/empty-state`) for the neutral cases, and `ErrorState`
(`@rs/ui-new/error-state`) for failures. Don't write a per-screen empty `div`.

## Anatomy

Every empty state is the same shape: **icon/media → title → body → (optional)
CTA**. The anatomy doesn't change between cases — only the copy and the CTA do,
which is why this is one component, not three.

- Title: a short, positive statement, sentence case.
- Body: one supporting sentence setting expectations or suggesting the next
  move.
- CTA: at most one primary `action` (solid button); a `secondaryAction` is
  link-styled. Never two solid buttons.

## The three cases

| Case | Why it's empty | Body + CTA |
| --- | --- | --- |
| **First-use** | nothing created yet | set expectations; CTA starts the primary flow ("Create your first cache"). The one case where a brand `IconTile` via `media` is appropriate. |
| **No results** | a filter/search matched nothing | suggest widening the query; CTA clears filters. |
| **Restricted / permissions** | the user can't see this | say what's needed; CTA requests access or routes back. |

## Replace-the-element rule

The empty state occupies the same slot the content would have — the table body,
the panel, the card. Use `layout="compact"` inside a table cell or short slot;
`layout="block"` (default) for a page or full panel.

## EmptyState vs ErrorState

The boundary is *nothing here* vs *something failed*:

- **EmptyState** — nothing is wrong; there's just no content yet. Neutral tile,
  no accent, no retry, `role` is a plain region (not `alert`).
- **ErrorState** — a fetch/permission/dependency failure. It carries the accent
  glow, the "which results remain trustworthy" line, a retry path, and the
  technical `DetailExpander`. It announces as `role="alert"`.

If you're unsure: did a request fail? → `ErrorState`. Otherwise → `EmptyState`.

## Usage rules (checkable)

- [ ] No blank region — the slot is replaced by a titled state.
- [ ] Anatomy is icon/media + title + body + at most one primary CTA.
- [ ] Copy matches the case (first-use / no-results / restricted).
- [ ] One solid button max; secondary is link-styled.
- [ ] Failures use `ErrorState`; absence uses `EmptyState`.
- [ ] `compact` in a cell/short slot, `block` for a page/panel.

## Cross-references

- Component API and props: **components/empty-state** (existing doc).
- Brand tile for first-use: **components/icon-tile** (existing doc).
- Loading (not empty) states: **patterns/loading**.
