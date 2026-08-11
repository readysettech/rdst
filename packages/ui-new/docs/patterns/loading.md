# Loading

## Overview

Three indicators, one per kind of wait (guideline 8). Choosing by kind keeps the
loading language predictable: a skeleton means "content is coming here", a
spinner means "your action is running", a progress bar means "this long job is
N% done".

| Indicator | Component | Use for |
| --- | --- | --- |
| **Skeleton** | `Skeleton` (`@rs/ui-new/skeleton`) | a data *container* filling in — table rows, cards, a panel's shape |
| **Spinner** | `Spinner` (`@rs/ui-new/spinner`) | an in-flight *action* — a button submit, a small inline fetch |
| **Progress** | `Progress` / `CircularProgress` (`@rs/ui-new/progress`, `/circular-progress`) | a long op with a known/estimable end (import, scan, benchmark) |

## Rules

- **Skeleton the shape you're replacing.** A skeleton should approximate the
  real content's layout so the page doesn't jump when data lands. Don't drop a
  lone spinner in the middle of a container that will hold a table.
- **Spinner an action, not a page.** For a button, use the component's built-in
  `loading` (`Button`, `IconButton`) rather than swapping in a standalone
  spinner.
- **Progress needs a value.** Use `Progress`/`CircularProgress` only when you can
  supply `value`/`max`; if the end is unknown, a spinner is honest and a fake
  progress bar isn't.
- **~100ms show-delay.** Gate the indicator behind a short timer at the call
  site so fast responses don't flash a loading state. Apply the delay yourself;
  the components render immediately when mounted.
- Inline vs full-screen by scope: a row/panel waits inline; a whole route may
  use a centered indicator. Never block the whole screen for a local wait.

## Usage rules (checkable)

- [ ] Container fill → Skeleton; action → Spinner; measurable long op → Progress.
- [ ] Skeleton approximates the real content's layout.
- [ ] Button loading uses the component's `loading` prop, not a separate spinner.
- [ ] Progress is used only with a real `value`/`max`; otherwise Spinner.
- [ ] Indicator is delayed ~100ms so fast responses don't flash.
- [ ] Loading scope (inline vs full-screen) matches what's actually waiting.

## Cross-references

- Component APIs: **components/spinner-progress-skeleton**.
- Button/IconButton `loading`: **components/button**.
- Empty (not loading) regions: **patterns/empty-states**.
