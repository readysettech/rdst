# Modal

A focus-trapping overlay for a task that must be finished or abandoned before
returning to the page. Built on Radix Dialog: it supplies the scrim, focus trap,
Escape-to-close, and a **required** accessible title (an sr-only fallback is
injected if you forget). For the ready-made decision variant, see
`ConfirmDialog` (components/confirm-dialog).

Import: `import { Modal, ModalContent, ModalTitle, ModalDescription, ModalClose, ModalTrigger } from '@rs/ui-new/modal'`

---

## Usage

**When to use**
- Genuinely blocking work: a short form, a required choice, a confirmation.

**When not to use**
- Transient feedback → `useToast`. Inline feedback → `Alert`.
- Secondary content that shouldn't block → `Disclosure`, a `Popover`, or a
  `Drawer`.

**Size to content.** Use the smallest `size` that fits (`base` `max-w-lg` →
`large` `max-w-2xl` → `extra-large` `max-w-4xl` → `full`). Don't open a `full`
modal for two fields.

**Do**
- Always give an accessible title (`ModalContent`'s `title` prop or a visible
  `ModalTitle`).
- Put the primary action on the right, cancel to its left (see patterns/dialogs).
- Use `blockClose` / `blockCloseWhileLoading` to protect an in-flight action.

**Don't**
- Nest modals, or stack a modal over a modal.
- Leave the dialog untitled (Radix warns and screen readers get an unlabelled
  dialog — the fallback title is a safety net, not a substitute for a real one).

---

## Style

`tv()`, all tokens. Overlay is `bg-surface-scrim` + `backdrop-blur`; content is
`bg-surface-layout-2`, `rounded-[1.25rem]`, `border-border-layout-1`, `p-6`,
capped at `max-h-[90dvh]`. Overlays that should read as the frontmost surface
use `bg-surface-overlay shadow-elevation-3` (as `ConfirmDialog` does — see
foundations/color layer model). Enter/exit animate opacity + scale via
`getTransition()`.

| Size | Width |
| --- | --- |
| `base` (default) | `max-w-lg` |
| `large` | `max-w-2xl` |
| `extra-large` | `max-w-4xl` |
| `full` | `max-w-full`, full height |

---

## Code

### Parts

| Export | Role |
| --- | --- |
| `Modal` | Root; controls open state. Props: `open`, `onOpenChange`, `blockClose`. |
| `ModalTrigger` | Radix `Dialog.Trigger`. |
| `ModalContent` | Portalled content panel. Key props below. |
| `ModalContentContainer` | Wraps content in `AnimatePresence` for exit anim. |
| `ModalTitle` / `ModalDescription` | Titled/description slots (accessible). |
| `ModalClose` | Radix `Dialog.Close`. |
| `ModalContentWithText` | Convenience shell with header/footer + Cancel. |

`ModalContent` props: `size`, `title` (sr-only accessible title fallback),
`description` (sr-only, wires `aria-describedby`), `hideClose`, `layoutId`,
`overlayClassName` / `innerClassName` / `closeClassName`, plus Radix
`Dialog.Content` props (`onOpenAutoFocus`, …).

### Example

```tsx
import {
  Modal, ModalContent, ModalTitle, ModalDescription, ModalClose,
} from '@rs/ui-new/modal'
import { Button } from '@rs/ui-new/button'

<Modal open={open} onOpenChange={setOpen}>
  <ModalContent size="base">
    <ModalTitle>Rename cache</ModalTitle>
    <ModalDescription>Give this cache a clearer name.</ModalDescription>
    {/* form fields */}
    <div className="flex justify-end gap-3">
      <ModalClose asChild>
        <Button label="Cancel" modifier="ghost" />
      </ModalClose>
      <Button label="Save name" onClick={save} />
    </div>
  </ModalContent>
</Modal>
```

---

## Accessibility

- Radix supplies the focus trap, `Escape` to close, scrim click to dismiss, and
  focus restoration to the trigger on close.
- A `Dialog.Title` is required; `Modal` injects an sr-only fallback when neither
  a `title` prop nor a `ModalTitle`/`Dialog.Title` child is present — but always
  supply a real, meaningful title.
- Pass `description` (or a visible `ModalDescription`) to wire `aria-describedby`
  when the dialog needs explanatory text.
- Primary-right / cancel-left action order; focus the safe choice on open for
  destructive dialogs (`ConfirmDialog` does this via `onOpenAutoFocus`).
