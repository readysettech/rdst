# Tab

A flat row of view/route labels with an animated underline that slides to the
active one. This is the page-navigation idiom — switching which view or route
fills the page — ported from cloud's app-local `tab.tsx`.

## Usage

**When to use**

- **Page/view navigation** — swap which view or route occupies the page: a set
  of routed sub-pages (`TabItem`, link-based), or a set of views keyed off a
  URL search param (`TabItemButton`, button-based), as the rdst Queries / Ask /
  Caching workspaces do.
- The underline is a shared-layout animation, so it visibly tracks the move
  between tabs — a cue that reads as "you navigated", which is exactly the
  page-level affordance you want here.

**When not to use**

- In-place behavior or filter switches where the surface stays put and only a
  parameter changes (Historical / Realtime scope, a source filter) — use
  [SegmentedControl](segmented-control.md). SegmentedControl = in-place
  behavior/filter switches; Tab = page/view navigation.
- Ordered steps (use a stepper) or actions (use `Button`).

**Rules**

- Give each tab group a distinct `layoutPrefix`; it namespaces the underline's
  `layoutId` so two tab rows on one screen never animate into each other.
- Wrap the row in a `TabList` with an `aria-label` so it becomes a labelled
  `tablist`.
- For `TabItemButton` view tabs, pass `id` + `aria-controls` and point the
  panel's `aria-labelledby` at the active tab's `id` (see Accessibility).

## Style

Built with `tv()`; every color is a `@rs/tailwind-base` token. A tab is a flat,
underline-only label — no container fill, no pill.

| Part | Tokens / classes |
| --- | --- |
| Tab (base) | `relative flex items-center gap-2 h-12 px-0 py-1 rounded-none text-label-medium text-content-layout-2 select-none outline-none cursor-pointer` |
| Transition | `transition-[background,color] duration-slower ease-base` |
| Hover / focus | `hover:text-content-primary-soft focus:text-content-primary-soft` |
| Active label | `text-content-primary-soft` |
| Active underline | `absolute bottom-0 h-[2px] w-full rounded-full bg-content-primary-soft` (animated `m.div` with a shared `layoutId`) |
| Disabled | `disabled:pointer-events-none disabled:opacity-50` |
| Unavailable (`disabled` prop) | `aria-disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:hover:text-content-layout-2` |
| `TabList` row | `flex gap-6` (+ `role="tablist"` when `aria-label` is set) |

## Code

Exports: `TabItem` (link), `TabItemButton` (button), `TabList`,
`TabItemSkeleton`, `TabListSkeleton`.

**`TabItemButton` props** (view tabs over a URL param)

| Prop | Type | Notes |
| --- | --- | --- |
| `label` | `string` | Tab text (also its accessible name). |
| `active` | `boolean` | Whether this tab is selected — drives the underline and `aria-selected`. |
| `onClick` | `() => void` | Fired on selection. |
| `layoutPrefix` | `string` | Namespaces the underline `layoutId` per tab group. |
| `leftIcon` / `rightIcon` | `IconStrokeName` | Optional, decorative (`aria-hidden`). |
| `disabled` | `boolean` | The view exists but cannot be opened yet: `aria-disabled`, dimmed, activation ignored. |
| `hint` | `string` | Why the tab is unavailable — the button's `title`, shown on hover. |
| `id` | `string` | Tab id — wire the panel's `aria-labelledby` to it. |
| `aria-controls` | `string` | Id of the panel this tab controls. |

`TabItem` takes the same base props plus TanStack Router `LinkComponentProps`
(`to`, `activeProps`, …) and renders a `<Link>` with the underline shown on the
router's `isActive`.

**Workspace example** (button tabs over a URL view param)

```tsx
import { TabItemButton, TabList } from '@rs/ui-new/tab'

const VIEW_SEGMENTS = [
  { value: 'slow', label: 'Find', icon: 'observe' },
  { value: 'saved', label: 'Saved', icon: 'folder-file' },
  { value: 'analyze', label: 'Analyze', icon: 'speedometer' },
] as const

const PANEL_ID = 'queries-panel'

<TabList aria-label="Queries views">
  {VIEW_SEGMENTS.map((segment) => (
    <TabItemButton
      key={segment.value}
      layoutPrefix="queries"
      label={segment.label}
      leftIcon={segment.icon}
      active={segment.value === view}
      id={`${PANEL_ID}-tab-${segment.value}`}
      aria-controls={PANEL_ID}
      onClick={() =>
        navigate({ to: '/queries', search: { view: segment.value }, replace: false })
      }
    />
  ))}
</TabList>

<div role="tabpanel" id={PANEL_ID} aria-labelledby={`${PANEL_ID}-tab-${view}`}>
  {view === 'slow' && <TopPage embedded />}
  {view === 'saved' && <QueryRegistryPage embedded />}
  {view === 'analyze' && <AnalyzePage embedded />}
</div>
```

The underline animates because rdst wraps the tree in `LazyMotion features={domMax}`
(shared-layout requires the full DOM feature set).

## Accessibility

- `TabList` with an `aria-label` renders `role="tablist"` named by that label.
- `TabItemButton` renders a native `<button role="tab">` with
  `aria-selected={active}`. Native buttons keep every tab in the natural tab
  order (Tab moves between them; Enter/Space or click activates) — there is no
  roving tabindex, so arrow keys do not switch tabs.
- Icons are decorative (`aria-hidden`); the accessible name is the label text
  alone.
- A tab marked `disabled` uses `aria-disabled` rather than the native
  `disabled` attribute, so it keeps its place in the tab order and its `hint`
  stays hoverable — a tab nobody can reach cannot explain itself.
- **Panel wiring** — set `id` = `${panelId}-tab-${value}` and
  `aria-controls` = the panel id on each tab, wrap the revealed region in a
  `role="tabpanel"` element, and point its `aria-labelledby` at the active
  tab's `id`. One stable panel element around the existing conditional
  mount/unmount is enough.
- `TabItem` (link variant) is route navigation: the active state follows the
  router, and the accessible current-page cue is the link's own routing.
