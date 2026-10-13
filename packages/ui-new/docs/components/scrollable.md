# Scrollable

The one scroll container: a Radix scroll area with the design-system
scrollbar (thin, on the surface tone, hidden until the pointer is over it).
Every surface that scrolls uses it; a native `overflow-auto` container would
show the operating system's scrollbar, which the Electron build renders on top
of the canvas.

Import: `import { Scrollable } from '@rs/ui-new/scrollable'`

---

## Usage

Wrap the part that scrolls, not the whole surface: a panel's header and
footer stay outside, so they stay in view while the body scrolls. The page
itself never scrolls; the canvas, each `TwoPane` panel, each `SidePane` body
and each dialog body is its own `Scrollable`.

- In a flex column (a panel, a dialog between its title and footer), size the
  root with `rootClassName="min-h-0 flex-1"`. The root lays its viewport out
  as a column, so this works inside a dialog whose height is only capped.
- For a capped box (a menu, a code block, a file list), put the cap on the
  viewport: `className="max-h-64"`.
- A box that scrolls sideways (a plan or a log with long lines) passes
  `orientation="horizontal"`.
- `type="auto"` keeps the scrollbar visible whenever the content overflows;
  use it where the overflow would otherwise go unnoticed (the `SidePane`
  body).

`viewportRef` hands out the scrolling element, to reset or read the scroll
position (`Main` returns to the top on a new page). `element.scrollIntoView`
works as with a native container, since the viewport is the scrolling
element; the clipping root is pinned at the origin so a scroll-into-view never
offsets it.

---

## Style

The track is `border-layout-2`, 8px wide and 16px on hover; the thumb is
`content-layout-disabled`, `content-layout-3` on hover, both easing over
`duration-fast`. The scrollbar overlays the content, so showing it never
shifts the layout. Surface classes (background, border, radius) go on the
root through `rootClassName`; padding goes on the viewport through
`className`.

---

## Code

```tsx
<section className="flex h-full min-h-0 flex-col">
  <header className="h-15 shrink-0">...</header>
  <Scrollable rootClassName="min-h-0 flex-1" className="pt-1 pb-6">
    {rows}
  </Scrollable>
</section>

<Scrollable
  rootClassName="rounded-control bg-surface-layout-2"
  className="max-h-48 px-3.5 py-3"
>
  <SQLDisplay sql={sql} wrap />
</Scrollable>
```

Props: `rootClassName`, `className` (viewport), `viewportRef`,
`orientation` (default `vertical`), `type` (default `hover`), and any
viewport attribute (`id`, `data-testid`, `aria-*`, `onScroll`).

---

## Accessibility

- The viewport is a native scrolling element: wheel, touch, keyboard paging
  and focus-driven scrolling behave as in any scroll container.
- Focus traps and Escape in dialogs are unaffected; the scroll area adds no
  focusable element of its own.
