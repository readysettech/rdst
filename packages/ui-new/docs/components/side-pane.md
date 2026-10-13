# SidePane

The second panel of a TwoPane: a 60px header with the state chip, the meta
and previous / next / close, then a body that scrolls on its own.

Import: `import { SidePane } from '@rs/ui-new/side-pane'`

---

## Usage

Render a `SidePane` as the `secondary` of a `TwoPane`. `chip` is the item's
state (a `Tag`), `meta` the third-tone facts (database, filed time),
`onPrev` / `onNext` move through the list the pane came from, `onClose`
returns to the single panel. `footer` holds the pane's actions; at most one
of them is solid.

---

## Style

Header `h-15` with a `border-layout-2` hairline, 24px left and 16px right
padding; prev, next and close are small ghost `IconButton`s. The body has
24px side padding and scrolls with a persistent scrollbar; the footer sits
above the panel's bottom edge behind its own hairline.

---

## Code

```tsx
<SidePane
  aria-label="Stuck query on demo"
  chip={<Tag size="small" variant="negative" modifier="ghost" label="Act now" />}
  meta={<span>demo, filed 9:55 AM</span>}
  onPrev={prev}
  onNext={next}
  onClose={close}
  footer={<Button label="Resolve" modifier="solid" />}
>
  ...
</SidePane>
```

---

## Accessibility

- Renders a `<section>` named by the required `aria-label`.
- The three header controls are labelled Previous, Next and Close.
