# SegmentedControl

A row of pill segments where exactly one is selected. One component, two
semantic modes: `tabs` for switching the visible view, `radio` for picking a
filter value.

## Usage

`SegmentedControl` is for **in-place behavior/filter switches** — the surface
stays put and a parameter changes. For **page/view navigation** (swapping which
view or route fills the page) reach for [Tab](tab.md) instead: it carries the
animated underline idiom and WAI-ARIA tab semantics for that job.

**When to use**

- `mode="radio"` — pick one value from a small mutually-exclusive set that
  parameterises the content below (e.g. a Historical / Realtime scope filter),
  rather than swapping the whole view. This is the primary mode.
- `mode="tabs"` (default) — kept as a documented API for a compact pill-style
  view switcher, but page-view switching should prefer [Tab](tab.md); the rdst
  workspaces moved their Slow / Saved / Analyze switchers there, so this mode
  currently has no in-repo consumer.

**When not to use**

- Linear or ordered steps (use a stepper/wizard) — segments are peers, not a
  sequence.
- Actions or commands (use `Button`) — a segment is a selection, not a verb.
- More than ~6 options, or options whose labels don't fit on one line (use a
  `BaseInputSelect`).

**Rules**

- Exactly one segment is always selected — the control is controlled, so keep
  `value` pointing at a real segment.
- Keep it to 2–6 segments.
- Always pass `aria-label` naming what the group selects.

## Style

Built with `tv()`; every color/shadow is a `@rs/tailwind-base` token.

A bordered, fully-rounded pill button-group: a tight container holds
rounded-full segments, and the active segment is a solid violet fill.

| Part | Tokens / classes |
| --- | --- |
| Container | `flex gap-1 p-1 rounded-full border border-border-layout-1 bg-surface-layout-2/60 w-fit` |
| Segment (base) | `px-4 h-8 rounded-full text-button-small` |
| Segment (small) | `px-3 h-7 rounded-full text-button-small` |
| Selected segment | `bg-surface-rising-solid text-content-rising-solid` |
| Unselected segment | `text-content-layout-3 hover:text-content-layout-2` |
| Focus | `focus-visible:outline-none focus-visible:shadow-focus` |
| Disabled | `opacity-50 cursor-not-allowed pointer-events-none` |
| Leading icon | `w-3.5 h-3.5` (base) / `w-3 h-3` (small) |

The selected pair `surface-rising-solid` + `content-rising-solid` clears WCAG
AA for text (5.39:1 in the dark theme, near-white on the purple `#6E56CF`
fill). Count chips inside a label (`Slow Queries 3`) ride the same fill, so
they stay legible when their segment is active.

## Code

**Props**

| Prop | Type | Notes |
| --- | --- | --- |
| `value` | `string` | Selected segment value (controlled). |
| `onValueChange` | `(value: string) => void` | Fired on click or keyboard selection. |
| `segments` | `Array<{ value; label; icon? }>` | `icon` is an `IconStrokeName`. |
| `mode` | `'tabs' \| 'radio'` | Default `'tabs'`. |
| `size` | `'base' \| 'small'` | Default `'base'`. |
| `disabled` | `boolean` | Disables the whole group. |
| `panelId` | `string` | Wires each tab's `id`/`aria-controls` to the panel (tabs mode) — see the panel-wiring recipe below. |
| `aria-label` | `string` | **Required** — names the group. |

**Filter example** (radio mode — the primary use)

```tsx
import { SegmentedControl } from '@rs/ui-new/segmented-control'

const SCOPE_SEGMENTS = [
  { value: 'historical', label: 'Historical', icon: 'observe' },
  { value: 'realtime', label: 'Realtime', icon: 'play' },
] as const

<SegmentedControl
  aria-label="Query scope"
  mode="radio"
  segments={SCOPE_SEGMENTS}
  value={scope}
  onValueChange={setScope}
/>
```

**Tabs-mode example** — the retained pill view-switcher API. Page-view
switching in the rdst workspaces now uses [Tab](tab.md) instead; this block
documents the mode that remains available.

```tsx
const VIEW_SEGMENTS = [
  { value: 'slow', label: 'Find', icon: 'observe' },
  { value: 'saved', label: 'Saved', icon: 'folder-file' },
  { value: 'analyze', label: 'Analyze', icon: 'speedometer' },
] as const

<SegmentedControl
  aria-label="Queries views"
  segments={VIEW_SEGMENTS}
  value={view}
  onValueChange={(next) =>
    navigate({ to: '/queries', search: { view: next }, replace: false })
  }
/>
```

## Accessibility

**Roles per mode**

| Mode | Container | Segment | Selected state |
| --- | --- | --- | --- |
| `tabs` | `role="tablist"` | `role="tab"` | `aria-selected` |
| `radio` | `role="radiogroup"` | `role="radio"` | `aria-checked` |

In `tabs` mode a `panelId` adds `id`/`aria-controls` wiring between each tab
and the panel it reveals — see the panel-wiring recipe below.

**Keyboard** — roving tabindex; the activation model differs by mode:

- `tabs` — **manual activation**, per WAI-ARIA: arrow keys move focus only,
  without selecting; the roving segment resets to the selected one once focus
  leaves the group. Enter/Space (or click) select the focused segment. This
  suits view switching, where moving focus shouldn't fire navigation on every
  keystroke.
- `radio` — **activation follows focus**, the native radio-button convention:
  arrow keys both move focus and select immediately.

| Key | Action (`tabs`) | Action (`radio`) |
| --- | --- | --- |
| `Tab` | Move into / out of the group (lands on the selected segment). | Same. |
| `ArrowRight` / `ArrowDown` | Move focus to next segment (wraps). | Select next segment (wraps). |
| `ArrowLeft` / `ArrowUp` | Move focus to previous segment (wraps). | Select previous segment (wraps). |
| `Home` / `End` | Move focus to first / last segment. | Select first / last segment. |
| `Enter` / `Space` | Select the focused segment. | Native button activation. |

**aria-label** is required — a segmented control with no group label leaves
assistive tech without context for what the choice controls.

### Panel wiring (`tabs` mode)

Pass `panelId` and wrap the region it reveals in a `tabpanel`. Each tab gets
an `id` of `${panelId}-tab-${value}`; the panel's `aria-labelledby` points at
the id of the currently selected tab.

```tsx
const PANEL_ID = 'queries-panel'

<SegmentedControl
  aria-label="Queries views"
  segments={VIEW_SEGMENTS}
  value={view}
  panelId={PANEL_ID}
  onValueChange={(next) =>
    navigate({ to: '/queries', search: { view: next }, replace: false })
  }
/>

<div
  role="tabpanel"
  id={PANEL_ID}
  aria-labelledby={`${PANEL_ID}-tab-${view}`}
>
  {view === 'slow' && <TopPage embedded />}
  {view === 'saved' && <QueryRegistryPage embedded />}
  {view === 'analyze' && <AnalyzePage embedded />}
</div>
```

The wrapper is one stable element around the existing conditional
mount/unmount — each pane still mounts only for its own view.

## Related patterns (choose deliberately)

- **`BaseInputRadioGroup` with `renderButton`/`renderFilterButton`**
  (`@rs/ui-new` form family, Radix RadioGroup) — button-styled radio
  selection as a full-width **form field** (labels, RHF integration, tick
  indicator). Use it when the choice is part of a form being submitted;
  use `SegmentedControl mode="radio"` for compact toolbar filters that
  apply immediately.
- **[Tab](tab.md)** (`@rs/ui-new/tab`) — the underline view/route tab, ported
  from cloud's app-local component and now living here. Use it for page-level
  **navigation**: swapping which view fills the page (link-based routes, or
  button tabs over a URL view param, as the rdst workspaces do). Reserve
  `SegmentedControl` for compact, in-place behavior/filter switches where the
  surface stays put.
