# TwoPane

Two separate rounded panels filling the shell: no gap while the second is
closed, 12px when it opens, each panel scrolling on its own.

Import: `import { TwoPane } from '@rs/ui-new/two-pane'`

---

## Usage

Use `TwoPane` on a page with a side pane (the Database page and its query
pane) or a queue beside its detail (the Inbox). The route declares
`staticData: { layout: 'split' }` so the shell gives it the full height and
never scrolls the page. The first panel scrolls `primary` in a
[`Scrollable`](scrollable.md); a `primary` that keeps its own header fixed
over a scrolling body (the Inbox queue) passes `primaryScrolls={false}` and
scrolls that body itself. `secondary` is usually a `SidePane`, which scrolls
its own body. `open` slides the second panel in and out; `primaryWidth` fixes
the first panel instead when the second takes the rest.

---

## Style

Each panel is the canvas: `surface-layout-1`, a `border-layout-soft` border
and `rounded-canvas` corners. The second panel animates its basis, margin and
opacity over `duration-slow`; the margin is 0 while closed so the first panel
keeps the full width. Under reduced motion the change is immediate.

---

## Code

```tsx
<TwoPane
  open={Boolean(openQuery)}
  secondaryWidth={560}
  primary={<DatabaseLive />}
  secondary={<SidePane onClose={close} ...>...</SidePane>}
/>
```

Props: `primary`, `secondary`, `open` (default true), `secondaryWidth`
(default 560), `primaryWidth`, `primaryScrolls` (default true), `className`.

---

## Accessibility

- The closed panel is `aria-hidden` and `inert`, so nothing inside it takes
  focus; its content is not rendered while closed.
