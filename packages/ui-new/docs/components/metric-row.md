# MetricRow and Metric

Numbers on the page, side by side, divided by hairlines rather than boxes.

Import: `import { MetricRow, Metric } from '@rs/ui-new/metric-row'`

---

## Usage

`MetricRow` holds the `Metric`s of one subject: the four health numbers of a
database, the day's numbers on the Home card, a report's figures. Each
`Metric` is a label, a numeral with its unit, and one line under it: a peak,
a comparison word or a share (`detail`), or why the number is out of range
(`reason`). Pass `onSelect` when picking a metric changes the chart below;
`selected` marks the one the chart shows. `value` may be a link when the
number leads somewhere.

A card that already frames the row passes `bordered={false}` and
`size="small"`.

---

## Style

Numerals use the metric scale: `small` 22px (`stat-small`), `base` 28px
(`stat-large`), `large` 40px (`display-small`), `hero` 48px (`stat-hero`),
all tabular. Label, detail and reason are `caption` (13px); the unit is
`body-small` in the third tone. `tone` colors only the numeral and the reason
(`warning`, `negative`, `positive`); the default is the first tone. A selected
metric carries a 2px underline in the first tone on the row's lower hairline.

---

## Code

```tsx
<MetricRow>
  <Metric label="Queries per second" value="11.7" detail="peak 15.9 at 9:50" />
  <Metric label="Avg latency" value="15.9" unit="ms" detail="same as yesterday" />
  <Metric label="Fixes ready" value={<Link to="/inbox">9</Link>} detail="54% of database time" />
  <Metric label="Cache hit" value="12.5" unit="%" tone="warning" reason="shared_buffers is the default" />
</MetricRow>
```

---

## Accessibility

- A `Metric` with `onSelect` renders a `Pressable` with `aria-pressed` set
  from `selected`, so the chosen metric is announced; without `onSelect` it
  is static text.
- Status is never color alone: a toned numeral always carries its `reason`.
