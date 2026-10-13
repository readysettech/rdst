# DividedList and ListRow

Rows on one grid, separated by 1px hairlines, with no boxes around them. The
open row is the only raised element in the list.

Import: `import { DividedList, ListRow } from '@rs/ui-new/divided-list'`

---

## Usage

`DividedList` sets the grid (`columns`, a `grid-template-columns` value) and
optional column labels (`head`); every `ListRow` inside shares it. A row that
is a link or a button passes `interactive`; render the router link as the row
itself with `asChild`. The row the page is showing passes `open` and its
`detail` content.

A column that drops out on narrow screens takes a `className` on its `head`
entry (`max-desktop:hidden`), the same class on its cell in each row, and a
`columns` value without it at that width, for example a CSS variable the list's
`className` sets per breakpoint.

Use `InteractiveRow` for a standalone button row with custom content;
`ListRow` is for rows that belong to a divided list.

---

## Style

Rows are 52px minimum with a 10px vertical padding and a `border-layout-2`
hairline under each; the list opens with the same hairline. An interactive
row lights with the `row-hover` gradient (the raised tone fading at both
ends). An open row drops its hairline, steps up to `surface-primary-soft`
inside a `rounded-panel` border and hosts its `detail` below the cells.

---

## Code

```tsx
<DividedList
  columns="minmax(220px,1.4fr) 1fr 1fr auto"
  head={[{ label: 'Database' }, { label: 'State' }, { label: 'Fixes ready', align: 'end' }, { label: '' }]}
>
  <ListRow asChild interactive>
    <Link to="/database">...</Link>
  </ListRow>
  <ListRow open detail={<FixDetail />}>...</ListRow>
</DividedList>
```

---

## Accessibility

- `asChild` keeps the caller's element, so a link row is a real `<a>` and a
  button row a real `<button>`; the shared focus ring applies when
  `interactive` is set.
- Put independent controls (Fix connection) in a cell of a non-interactive
  row, never inside a link row.
