# PageContainer

The content axis every page shares: a centered column with 48px side padding
and 1120px of content, or 860px for reading pages.

Import: `import { PageContainer } from '@rs/ui-new/page-container'`

---

## Usage

Wrap page content in one `PageContainer` under the `PageBar`. Reading pages
(a report, a full analysis) use `width="reading"` for the narrower column on
the same axis. Nothing in a page overrides the container's width locally.

---

## Style

`max-w-[1216px] px-12` (1120px of content plus the padding) centered with
`mx-auto`; `reading` is `max-w-[956px]`. The container carries no vertical
spacing; the page adds it.

---

## Code

```tsx
<PageBar title="Home" />
<PageContainer className="pt-9 pb-24">...</PageContainer>
```

Props: `width` (`content` default, `reading`) plus any `div` attributes.

---

## Accessibility

- A plain `div`; it adds no semantics.
