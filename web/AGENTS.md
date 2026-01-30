# RDST Web App

**Stack**: TanStack Router, Vite, Tailwind 4, React 19

**Backend**: FastAPI at `rdst/lib/api/` (Python)

## STRUCTURE

```
web-apps/apps/rdst/src/
├── components/       # UI components
├── layout/           # Sidebar, Header, Main
├── routes/           # TanStack Router pages
└── lib/              # API types, hooks
```

## COMMANDS

```bash
pnpm dev          # Start full local dev (FastAPI + Vite)
pnpm dev:vite     # Start Vite-only dev server (port 3001)
pnpm build        # Production build
```

## CONVENTIONS

### Use @rs/ui-new Components

**NEVER use raw HTML form elements:**

```tsx
// NO
<textarea className="..." />
<input type="text" />

// YES
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea'
import { BaseInputText } from '@rs/ui-new/base-input-text'
```

### Design Tokens Only

**NEVER hardcode colors:**

```tsx
// NO
className="bg-[#1a1a1a] text-gray-400"

// YES
className="bg-surface-layout-2 text-content-layout-2"
```

**Token categories:**
- `bg-surface-*` - backgrounds
- `text-content-*` - text colors  
- `border-border-*` - borders

### LazyMotion Required

Wrap app in LazyMotion for dropdown support:

```tsx
import LazyMotion from '@rs/ui-new/lazy-motion'
import domMax from '@rs/ui-new/dom-max'

<LazyMotion features={domMax} strict>
  <App />
</LazyMotion>
```

### Radix Trigger Pattern

Use native elements for `asChild` triggers:

```tsx
// YES - native div forwards refs
<Dropdown.Trigger asChild>
  <div>Content</div>
</Dropdown.Trigger>

// NO - custom components may not forward refs
<Dropdown.Trigger asChild>
  <HStack>Content</HStack>
</Dropdown.Trigger>
```

## BACKEND API

The FastAPI backend lives at `rdst/lib/api/`:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | Get targets, config |
| `/api/analyze` | POST | SSE streaming analysis |

## ANTI-PATTERNS

- **NO raw HTML inputs** - Use @rs/ui-new
- **NO hardcoded colors** - Use design tokens
- **NO missing LazyMotion** - Dropdowns won't work
