# Disclosure

A single collapsible section: a labelled trigger that shows and hides a panel
of supplementary content. Wraps the shared `useDisclosure` hook, a rotating
chevron, and the height animation the app's hand-rolled disclosures already
use.

Import: `import { Disclosure } from '@rs/ui-new/disclosure'`

Retires the four near-identical local `Disclosure` components (guards,
ConfigureForm, AskPanel's `SqlDisclosure`, agents) and the ~16 inline
`aria-expanded` hand-rolls counted in the P3 audit.

---

## Usage

Use a Disclosure to defer secondary content — advanced settings, an optional
detail block, "show the SQL that ran" — so the primary content leads and the
rest is one click away.

**When to use**
- Progressive disclosure of secondary form sections or settings.
- Optional detail that most users skip (raw output, extra context).
- A short list of independent, self-contained collapsible sections.

**When not to use**
- Required or critical inputs. The panel unmounts when closed; never hide a
  field the user must fill or an error they must see.
- Navigation between mutually exclusive views — that is a segmented control or
  tabs, not a disclosure.
- Rich technical/error detail inside an error surface — use `ErrorState`'s
  built-in `DetailExpander` (`@rs/ui-new/error-state`) so it stays on-contract.
- A dense set of many rows where an accordion (single-open) is expected.
  Disclosure is independent-open; compose several if you want an accordion, but
  coordinate open state yourself.

**Do**
- Open only in response to a user action (click / Enter / Space).
- Write a title that states what is inside, in sentence case.
- Keep panels flat — do not nest a Disclosure inside a Disclosure.

**Don't**
- Auto-open or auto-close a panel based on background state changes.
- Put the one primary action of a view behind a collapsed panel.
- Nest disclosures, or wrap a whole page section tree in one.

---

## Style

| Part | Token(s) |
| --- | --- |
| Container border | `border-border-layout-1`, `rounded-xl` |
| Trigger hover | `hover:bg-surface-layout-2/50` |
| Title text | `text-content-layout-1` (`label-small`) |
| Subtitle text | `text-content-layout-3` (`caption`) |
| Chevron | `text-content-layout-3`, rotates `-rotate-90` → `rotate-0` on open |
| Panel divider | `border-t border-border-layout-1` |
| Motion | height `0 → auto` + opacity, `duration: 0.2` (matches app hand-rolls) |

Disabled trigger: `opacity-50` + `cursor-not-allowed`; the panel keeps its
current state.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `title` | `ReactNode` | — | Summary text. Ignored if `trigger` is set. |
| `subtitle` | `ReactNode` | — | Secondary line under the title. |
| `trigger` | `ReactNode` | — | Replaces the default title/subtitle layout; the chevron still renders. |
| `children` | `ReactNode` | — | Panel content (required). |
| `defaultOpen` | `boolean` | `false` | Initial state (uncontrolled). |
| `open` | `boolean` | — | Controlled open state; pair with `onOpenChange`. |
| `onOpenChange` | `(open: boolean) => void` | — | Controlled change handler. |
| `disabled` | `boolean` | `false` | Disables the trigger. |
| `className` | `string` | — | Class on the container. |
| `panelClassName` | `string` | — | Class on the padded panel wrapper. |

### Example

```tsx
import { Disclosure } from '@rs/ui-new/disclosure'
import { Text } from '@rs/ui-new/text'

function AdvancedSettings() {
  return (
    <Disclosure title="Advanced settings" subtitle="Restrictions, filters, limits">
      <Text level="body-small" className="text-content-layout-2">
        These options are optional and rarely need changing.
      </Text>
    </Disclosure>
  )
}
```

Controlled:

```tsx
const [open, setOpen] = useState(false)

<Disclosure title="Show the SQL that ran" open={open} onOpenChange={setOpen}>
  <SqlBlock sql={sql} />
</Disclosure>
```

---

## Accessibility

- The trigger is a native `<button type="button">` with `aria-expanded`
  reflecting open state and `aria-controls` pointing at the panel `id`
  (generated with `useId`).
- The panel element carries the matching `id`.
- Keyboard: `Tab` reaches the trigger; `Enter` / `Space` toggle it — native
  button behavior, nothing custom.
- The chevron is decorative (`label=""`, `aria-hidden`) — state is announced
  once, by `aria-expanded` on the trigger; the title carries the section name.
- Because the panel unmounts when closed, its content is removed from the
  accessibility tree while collapsed — keep required content outside it.
