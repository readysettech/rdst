# StatusLine

The first thing on a page: one 24px sentence built from data, with a live
caption under it.

Import: `import { StatusLine, statusLinkClass } from '@rs/ui-new/status-line'`

---

## Usage

Home and the Database header open with a `StatusLine`. The children are the
sentence; wrap its quieter half in `StatusLine.Quiet` and give a link inside
it `statusLinkClass`. `caption` says what is watched, the last reading and
what comes next; `tone` picks the caption's dot: `live` while readings
arrive, `warning`, `negative` or `off` otherwise.

---

## Style

The sentence is `headline-2` (24px, medium) in the first tone with
`-0.005em` tracking; `Quiet` is normal weight in the second tone. The caption
is `body-small` in the third tone, 10px under the sentence. The live dot is a
`StatusRipple`; the still dots are 8px in the tone's plain color.

---

## Code

```tsx
<StatusLine tone="live" caption="Watching 4 databases. Last reading 9:56:30. Next reports at 12:00 AM.">
  <Link to="/inbox" className={statusLinkClass}>1 item needs you</Link> on demo
  <StatusLine.Quiet>: a stuck query since 9:55 AM.</StatusLine.Quiet>
</StatusLine>
```

---

## Accessibility

- The sentence is a paragraph; the state it carries is in its words, so the
  dot is decorative (`aria-hidden` when still).
