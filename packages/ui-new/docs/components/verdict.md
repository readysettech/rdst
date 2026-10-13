# Verdict

A 20px sentence that opens a report or an analysis: the verdict, then why.

Import: `import { Verdict } from '@rs/ui-new/verdict'`

---

## Usage

Open a report, a full analysis or a dialog with one `Verdict`. `lead` is the
judgment in a few words; the children are the rest of the sentence. Say the
fact once: a verdict, a summary and a tag repeating it is one statement, not
three.

---

## Style

`headline-3` (20px); the lead is medium weight in the first tone, the rest
normal weight in the second tone, on one line that wraps as a sentence.

---

## Code

```tsx
<Verdict lead="One fix covers 54% of database time.">
  Nine indexes, ready to run; the rest of the day was quiet.
</Verdict>
```

---

## Accessibility

- A single paragraph; the lead is not a heading, so a page that needs a
  section title adds one.
