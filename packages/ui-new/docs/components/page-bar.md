# PageBar

The bar every page opens with: a 64px title row flush with the top of the
canvas, an optional tab row under it, and a bottom hairline that spans the
canvas edge to edge while everything inside sits on the content axis.

Import: `import { PageBar } from '@rs/ui-new/page-bar'`

---

## Usage

Render one `PageBar` as the first child of a page. `title` is the page name
(or a breadcrumb on a detail page), `meta` the third-tone facts beside it (a
date, a state dot, a count), `actions` the controls at the right, and `tabs`
the page's views. The one solid button of a page may live in the bar or in the
content, never both.

A shell rendering a bar on behalf of a page that still carries its own
heading passes `titleAs="p"`, so the outline keeps one level-1 heading.

**Global notices.** Inside a `PageNoticesProvider`, whatever the shell renders
in `PageNotices` (`@rs/ui-new/page-notices`) shows under the bar's hairline, one
quiet inline-alert line each, never above the bar. The notices stay mounted
while pages change; with no bar on screen they are not shown. A page without a
`PageBar` (a two-panel queue) places `<PageNoticeSlot className="..." />` under
its own header.

---

## Style

The title is `headline-3` (20px, medium) in the first tone; `meta` is
`body-small` in the third tone. The hairline is `border-layout-2` and runs the
full width of the bar; the title row and the tab row are each a
`PageContainer`, so they start where the page content starts. Tabs pull up 8px
so an active tab's 2px underline lands on the hairline; a `TabList` passed
here drops its own bottom border.

---

## Code

```tsx
<PageBar
  title="Inbox"
  meta={<span>2 open on 1 database</span>}
  actions={<IconButton icon="notification" label="Notifications" modifier="ghost" />}
  tabs={<TabList className="border-b-0">...</TabList>}
/>
```

Props: `title` (ReactNode), `titleAs` (`h1` default, or `p`), `leading` (a
mark before the title, outside the heading), `meta`, `actions`, `tabs`,
`className`.

```tsx
<PageNoticesProvider>
  <PageNotices><DesktopNotificationsBanner /></PageNotices>
  <Outlet />
</PageNoticesProvider>
```

The slot under the bar is a `PageContainer` column (`pt-9`, `gap-2`) that
takes no space when empty.

---

## Accessibility

- Renders a `<header>`; the title is the route's `<h1>` unless `titleAs="p"`.
- Carries no interactive behavior of its own; actions and tabs bring theirs.
