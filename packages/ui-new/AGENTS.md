# UI Design System (@rs/ui-new)

**Stack**: Radix UI primitives, Motion (Framer), Tailwind 4

## STRUCTURE

```
packages/ui-new/src/
├── components/
│   ├── element/      # Base elements (Button, Card, Text, Tag)
│   ├── feedback/     # Spinner, Alert, Toast
│   ├── form/         # Input components (base/ + controlled/)
│   ├── overlay/      # Dialog, Dropdown, Modal, Tooltip
│   └── svg/          # Icons and logos
├── control-flow/     # React control-flow helpers
├── hooks/            # Shared hooks
├── motion/           # Animation utilities
└── helpers/          # Date, format, types
```

## CONVENTIONS

### Import Pattern
Components are exported individually - import by subpath:

```tsx
import { Button } from '@rs/ui-new/button'
import { Show } from '@rs/ui-new/show'
import { BaseInputText } from '@rs/ui-new/base-input-text'
```

### Control Flow Components (SolidJS-style)
Use declarative control flow instead of ternaries:

```tsx
// YES - use control flow components
import { Show } from '@rs/ui-new/show'
import { For } from '@rs/ui-new/for'

<Show when={data} fallback={<Loading />}>
  {(data) => <Content data={data} />}
</Show>

<For each={items}>{(item) => <Item {...item} />}</For>

// NO - avoid ternaries for conditionals
{data ? <Content /> : <Loading />}
```

### Form Components
Two patterns available:

```tsx
// Base (uncontrolled) - for simple forms
import { BaseInputText } from '@rs/ui-new/base-input-text'
<BaseInputText defaultValue="..." />

// Controlled - for react-hook-form
import { ControlledInputText } from '@rs/ui-new/controlled-input-text'
<ControlledInputText control={control} name="email" />
```

### Motion / Animation
Wrap app in `LazyMotion` for framer-motion features:

```tsx
import LazyMotion from '@rs/ui-new/lazy-motion'
import domMax from '@rs/ui-new/dom-max'

<LazyMotion features={domMax} strict>
  <App />
</LazyMotion>
```

### Radix Triggers
Triggers using `asChild` must forward refs - use native elements:

```tsx
// YES - native div forwards refs
<Dropdown.Trigger asChild>
  <div className="...">Trigger</div>
</Dropdown.Trigger>

// NO - custom components may not forward refs
<Dropdown.Trigger asChild>
  <HStack>Trigger</HStack>
</Dropdown.Trigger>
```

## ANTI-PATTERNS

- **NO direct Radix imports** - Use our wrappers in `components/overlay/`
- **NO hardcoded colors** - Use Tailwind tokens from `@rs/tailwind-base`
- **NO raw HTML form inputs** - Use `base-input-*` or `controlled-input-*`
- **NO inline ternaries for visibility** - Use `<Show>` component
- **NO `framer-motion` direct import** - Use `@rs/ui-new/motion`

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Add base component | `src/components/element/` |
| Add form input | `src/components/form/base/` |
| Add overlay/modal | `src/components/overlay/` |
| Add animation | `src/motion/` |
| Add hook | `src/hooks/` |
