# InteractiveRow

A full-width native button for dense selection or navigation rows whose visible
content needs more structure than a normal `Button` label can provide.

Import: `import { InteractiveRow } from '@rs/ui-new/interactive-row'`

---

## Usage

Use `InteractiveRow` when the entire row is one action but the row contains
several coordinated pieces of information, such as a status tag, title,
timestamp, metrics, and trailing affordance.

Use `Button` for ordinary labelled actions, `IconButton` for compact icon-only
actions, and a link when navigation is the only behavior and no button state is
needed. Do not place other interactive controls inside an `InteractiveRow`.

The required `label` is the row's accessible action name. Write it as a verb and
object, for example “Open production report” or “Continue analyzing query 42”.

---

## Style

The primitive supplies only shared interaction behavior: full-width alignment,
keyboard focus ring, a subtle press response, disabled state, and the selected
surface treatment exposed by `active`. The caller owns spacing, border, surface,
and internal layout through `className` and `children`.

Keep the whole row visually coherent as one target. A trailing chevron may
reinforce navigation, but it must be decorative because `label` already names
the action.

---

## Code

```tsx
<InteractiveRow
  label="Open production health-check report"
  active={selected}
  className="rounded-xl px-4 py-3 hover:bg-surface-layout-2/50"
  onClick={openReport}
>
  <div className="flex items-center justify-between gap-4">
    <ReportSummary />
    <Icon name="chevron-right" label="" aria-hidden="true" />
  </div>
</InteractiveRow>
```

Props extend native button attributes except `children`, `className`, and
`aria-label`. `label`, `children`, and `className` are explicit; `active`
applies the shared selected-row surface.

---

## Accessibility

- Renders a native `<button type="button">`, so Enter and Space work without
  custom keyboard handlers.
- `label` becomes the button's `aria-label` and is required.
- The focus ring uses the same semantic tokens as `Button` and `IconButton`.
- `disabled` uses the native disabled attribute and shared visual treatment.
- Never nest buttons, links, checkboxes, menus, or other controls inside the
  row. If a row needs independent actions, render a non-interactive container
  with separate `Button` or `IconButton` controls instead.
