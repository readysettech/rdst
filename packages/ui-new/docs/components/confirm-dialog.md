# ConfirmDialog

The one shared destructive/decision-confirmation dialog. Names the target and
the consequence next to the action, offers a red confirm and a safe Cancel, and
focuses Cancel on open so the safe choice is the default. Built on `Modal`, so it
inherits the focus trap, Escape-to-close, and a required accessible title. Use it
instead of a bespoke confirmation modal (guideline 10, see patterns/dialogs).

Import: `import { ConfirmDialog } from '@rs/ui-new/confirm-dialog'`

Folds the previously-bespoke confirms (SchemaReinitDialog, BenchmarkConfirmDialog,
demo teardown) onto one shell.

---

## Usage

Pick the tier that matches the action's blast radius:

| Tier | Action | How |
| --- | --- | --- |
| **none** | reversible | no dialog at all |
| **confirm** | moderate, hard-to-undo | `ConfirmDialog` with `title` naming the consequence + a `notice`; `confirmVariant="negative"` |
| **typed-confirm** | high-consequence / remote / unrecoverable | add a `children` text input the user must match before `confirmDisabled` clears |

**Reference implementations**

- **`ConfirmDialog`** itself — the confirm tier. `notice` renders a tinted
  `InlineNotice` naming the cost; `confirmVariant` defaults to `negative`.
- **`BenchmarkConfirmDialog`** (`apps/rdst/src/components/BenchmarkConfirmDialog.tsx`)
  — the typed-confirm exemplar. A local target gets one `confirm`
  (`confirmVariant="primary"`); a remote target escalates to a red `notice`, a
  `remote-target` `Tag` via `titleAccessory`, and a typed input (passed as
  `children`) that must equal the target name before confirm enables.

**Do**
- Phrase `title` as the consequence, as a question ("Run benchmark against X?").
- Name the cost/consequence in `notice`, next to the action.
- Set `blockCloseWhileLoading` so a running destructive action isn't torn down
  by Escape/overlay click.

**Don't**
- Build a one-off confirmation modal.
- Default to the destructive color for a non-destructive confirm — set
  `confirmVariant="primary"`.

---

## Style

Renders through `Modal` with `bg-surface-layout-1 shadow-elevation-3`. Its
anatomy matches the product's transactional
dialogs: a compact bordered identity header, an optional focused body for the
`InlineNotice` and typed-confirm content, then a low-contrast bordered footer.
When an action or notice icon is provided, the header shows it in a semantic
`IconTile`. The footer keeps Cancel (ghost) to the left of the confirm button.
There is no close X (`hideClose`) — a confirm is a two-button decision.

---

## Code

### Props

| Prop | Type | Default | Notes |
| --- | --- | --- | --- |
| `isOpen` | `boolean` | — | Open state (required). |
| `onClose` | `() => void` | — | Cancel / dismiss (required). |
| `onConfirm` | `() => void` | — | Confirm action (required). |
| `title` | `string` | — | Visible + accessible title, phrased as the consequence (required). |
| `titleAccessory` | `ReactNode` | — | Node beside the title (e.g. a `remote-target` Tag). |
| `subtitle` | `ReactNode` | — | Sub-line (target, planned cost); becomes the accessible description. |
| `notice` | `{ accent?: 'negative' \| 'warning' \| 'info'; icon?; title?; message }` | — | Tinted `InlineNotice` naming the consequence. |
| `children` | `ReactNode` | — | Extra body — the typed-confirm input, an alt-path hint. |
| `confirmLabel` | `string` | — | Confirm button text (required). |
| `confirmVariant` | `'primary' \| 'negative'` | `'negative'` | Confirm color. |
| `confirmIcon` | `IconStrokeName` | — | Leading icon on confirm. |
| `confirmDisabled` | `boolean` | — | Gate confirm (used for typed-confirm). |
| `cancelLabel` | `string` | `'Cancel'` | — |
| `loading` | `boolean` | — | Confirm shows spinner; Cancel disabled. |
| `blockCloseWhileLoading` | `boolean` | — | Ignore Escape/overlay dismiss while `loading`. |
| `description` | `string` | — | Explicit sr-only `aria-describedby` when there's no `subtitle`. |
| `size` | `'base' \| 'large'` | `'base'` | — |

### Example (typed-confirm)

```tsx
<ConfirmDialog
  isOpen={open}
  onClose={close}
  onConfirm={run}
  title={`Run benchmark against ${target}?`}
  titleAccessory={<Tag size="small" variant="negative" label="remote-target" />}
  subtitle={`${queryCount} queries · ${loadSummary}`}
  notice={{ accent: 'negative', icon: 'alert', title: 'This is a remote database',
            message: `Type the target name to confirm load against ${target}.` }}
  confirmLabel="Run against remote"
  confirmVariant="negative"
  confirmIcon="play"
  confirmDisabled={typed.trim() !== target}
>
  <BaseInputText value={typed} onChange={(e) => setTyped(e.target.value)} />
</ConfirmDialog>
```

---

## Accessibility

- Inherits Radix's focus trap, `Escape`, and focus restoration from `Modal`.
- Focus opens on **Cancel** (`onOpenAutoFocus` → `cancelRef`), so `Enter` never
  fires the destructive confirm by default.
- The title is the accessible name; `subtitle` (or an explicit `description`)
  wires `aria-describedby`. When neither exists, `aria-describedby` is opted out
  cleanly (no dev warning).
- `blockCloseWhileLoading` prevents a mid-flight destructive action from being
  interrupted; the running button shows `loading` and Cancel disables.
