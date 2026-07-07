import { useEffect, useMemo, useRef, useState } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { Button } from '@rs/ui-new/button';
import { Card } from '@rs/ui-new/card';
import { Icon } from '@rs/ui-new/icon';
import type { IconStrokeName } from '@rs/ui-icons/icon-name';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { Tag } from '@rs/ui-new/tag';
import { Text } from '@rs/ui-new/text';
import { HStack, VStack } from '@rs/ui-new/stack';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { BaseInputSwitch } from '@rs/ui-new/base-input-switch';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { toast } from '@rs/ui-new/use-toast';
import { useSystemStatus } from '../lib/useSystemStatus';
import { useTarget } from '../hooks/useTarget';
import {
  checkGuardSql,
  deriveGuard,
  fetchGuard,
  useCreateGuard,
  useDeleteGuard,
  useGuardDetail,
  useGuardsList,
  useUpdateGuard,
} from '../lib/useGuards';
import { MASK_TYPES } from '../types/guards';
import type {
  GuardCheckLevel,
  GuardCheckResponse,
  GuardDetail,
  GuardSummary,
} from '../types/guards';

export const Route = createFileRoute('/guards')({
  component: GuardsPage,
});

// ---------------------------------------------------------------------------
// Editable form model
// ---------------------------------------------------------------------------
//
// GuardDetail nests masking/restrictions/rules/limits and uses record and list
// shapes that are awkward to bind directly to inputs. The form model flattens
// those into arrays of key/value rows and scalar fields, then converts back to
// a GuardDetail on save.

interface MaskRow {
  pattern: string;
  type: string;
}

interface FilterRow {
  table: string;
  columns: string; // comma-separated in the editor
}

interface GuardForm {
  name: string;
  description: string;
  intent: string;
  derived: boolean;
  masking: MaskRow[];
  deniedColumns: string; // newline / comma separated
  allowedTables: string;
  requiredFilters: FilterRow[];
  requireWhere: boolean;
  requireLimit: boolean;
  noSelectStar: boolean;
  maxTables: string;
  costLimit: string;
  maxEstimatedRows: string;
  maxRows: string;
  timeoutSeconds: string;
}

const GUARD_NAME_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_-]*$/;

const MASK_TYPE_OPTIONS: { value: string; label: string }[] = [
  { value: 'redact', label: 'Redact' },
  { value: 'email', label: 'Email' },
  { value: 'partial:4', label: 'Partial (last N)' },
  { value: 'hash', label: 'Hash' },
];

function emptyForm(): GuardForm {
  return {
    name: '',
    description: '',
    intent: '',
    derived: false,
    masking: [],
    deniedColumns: '',
    allowedTables: '',
    requiredFilters: [],
    requireWhere: false,
    requireLimit: false,
    noSelectStar: false,
    maxTables: '',
    costLimit: '',
    maxEstimatedRows: '',
    maxRows: '1000',
    timeoutSeconds: '30',
  };
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function detailToForm(detail: GuardDetail): GuardForm {
  const masking = Object.entries(detail.masking ?? {}).map(([pattern, type]) => ({
    pattern,
    type,
  }));
  const restrictions = detail.restrictions ?? {};
  const requiredFilters = Object.entries(restrictions.required_filters ?? {}).map(
    ([table, columns]) => ({ table, columns: columns.join(', ') }),
  );
  const rules = detail.guards ?? {};
  const limits = detail.limits ?? {};
  const numOrEmpty = (value: number | null | undefined) =>
    value === null || value === undefined ? '' : String(value);
  return {
    name: detail.name,
    description: detail.description ?? '',
    intent: detail.intent ?? '',
    derived: detail.derived ?? false,
    masking,
    deniedColumns: (restrictions.denied_columns ?? []).join('\n'),
    allowedTables: (restrictions.allowed_tables ?? []).join('\n'),
    requiredFilters,
    requireWhere: rules.require_where ?? false,
    requireLimit: rules.require_limit ?? false,
    noSelectStar: rules.no_select_star ?? false,
    maxTables: numOrEmpty(rules.max_tables),
    costLimit: numOrEmpty(rules.cost_limit),
    maxEstimatedRows: numOrEmpty(rules.max_estimated_rows),
    maxRows: numOrEmpty(limits.max_rows) || '1000',
    timeoutSeconds: numOrEmpty(limits.timeout_seconds) || '30',
  };
}

function parseOptionalInt(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number.parseInt(trimmed, 10);
  return Number.isFinite(parsed) ? parsed : null;
}

function parseIntOr(value: string, fallback: number): number {
  const parsed = parseOptionalInt(value);
  return parsed === null ? fallback : parsed;
}

function formToDetail(form: GuardForm): GuardDetail {
  const masking: Record<string, string> = {};
  for (const row of form.masking) {
    const pattern = row.pattern.trim();
    if (pattern) masking[pattern] = row.type;
  }

  const requiredFilters: Record<string, string[]> = {};
  for (const row of form.requiredFilters) {
    const table = row.table.trim();
    const columns = splitList(row.columns);
    if (table && columns.length > 0) requiredFilters[table] = columns;
  }

  const deniedColumns = splitList(form.deniedColumns);
  const allowedTables = splitList(form.allowedTables);

  return {
    name: form.name.trim(),
    description: form.description.trim(),
    intent: form.intent.trim(),
    derived: form.derived,
    masking,
    restrictions: {
      denied_columns: deniedColumns.length > 0 ? deniedColumns : null,
      allowed_tables: allowedTables.length > 0 ? allowedTables : null,
      required_filters: Object.keys(requiredFilters).length > 0 ? requiredFilters : null,
    },
    guards: {
      require_where: form.requireWhere,
      require_limit: form.requireLimit,
      no_select_star: form.noSelectStar,
      max_tables: parseOptionalInt(form.maxTables),
      cost_limit: parseOptionalInt(form.costLimit),
      max_estimated_rows: parseOptionalInt(form.maxEstimatedRows),
    },
    limits: {
      max_rows: parseIntOr(form.maxRows, 1000),
      timeout_seconds: parseIntOr(form.timeoutSeconds, 30),
    },
  };
}

// ---------------------------------------------------------------------------
// Shared presentation helpers
// ---------------------------------------------------------------------------

function checkLevel(level: string): GuardCheckLevel {
  switch (level) {
    case 'block':
      return 'block';
    case 'warn':
      return 'warn';
    default:
      return 'info';
  }
}

function levelVariant(level: GuardCheckLevel): 'negative' | 'warning' | 'informative' {
  switch (level) {
    case 'block':
      return 'negative';
    case 'warn':
      return 'warning';
    case 'info':
      return 'informative';
    default: {
      const exhaustive: never = level;
      return exhaustive;
    }
  }
}

function levelIcon(level: GuardCheckLevel): IconStrokeName {
  switch (level) {
    case 'block':
      return 'close';
    case 'warn':
      return 'alert';
    case 'info':
      return 'info';
    default: {
      const exhaustive: never = level;
      return exhaustive;
    }
  }
}

// Maps the compact rule tokens emitted by the summary API to readable labels.
// `tbl:N` and `max_tables=N` carry a count, so they are handled dynamically.
const RULE_LABELS: Record<string, string> = {
  where: 'WHERE required',
  require_where: 'WHERE required',
  limit: 'LIMIT required',
  require_limit: 'LIMIT required',
  filters: 'row filters',
  est_rows: 'row cap',
  'no_select*': 'no SELECT *',
  no_select_star: 'no SELECT *',
  cost: 'cost limit',
};

function ruleLabel(rule: string): string {
  const tblMatch = rule.match(/^(?:tbl:|max_tables=)(\d+)$/);
  if (tblMatch) return `max ${tblMatch[1]} tables`;
  const estMatch = rule.match(/^max_estimated_rows=(\d+)$/);
  if (estMatch) return `row cap ${Number(estMatch[1]).toLocaleString()}`;
  const costMatch = rule.match(/^cost_limit=(\d+)$/);
  if (costMatch) return `cost limit ${costMatch[1]}`;
  return RULE_LABELS[rule] ?? rule;
}

function ruleTags(rules: string[] | undefined) {
  return (rules ?? []).map((rule) => (
    <Tag key={rule} size="small" variant="informative" modifier="ghost" label={ruleLabel(rule)} />
  ));
}

function SectionCard({
  icon,
  title,
  action,
  children,
}: {
  icon: IconStrokeName;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Content className="p-0">
        <div className="px-5 py-3 border-b border-border-layout-1 bg-surface-layout-2/50">
          <HStack className="gap-2 items-center justify-between">
            <HStack className="gap-2 items-center">
              <Icon name={icon} label={title} className="w-4 h-4 text-content-layout-3" />
              <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                {title}
              </Text>
            </HStack>
            {action}
          </HStack>
        </div>
        {children}
      </Card.Content>
    </Card>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <Text as="label" level="label-small" className="text-content-layout-2">
      {children}
    </Text>
  );
}

// ---------------------------------------------------------------------------
// Guard detail view (read-only expansion)
// ---------------------------------------------------------------------------

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <VStack className="gap-1 items-start">
      <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
        {label}
      </Text>
      {children}
    </VStack>
  );
}

function GuardDetailView({ detail }: { detail: GuardDetail }) {
  const masking = Object.entries(detail.masking ?? {});
  const restrictions = detail.restrictions ?? {};
  const requiredFilters = Object.entries(restrictions.required_filters ?? {});
  const rules = detail.guards ?? {};
  const limits = detail.limits ?? {};
  const activeRuleFlags = [
    rules.require_where ? 'require_where' : null,
    rules.require_limit ? 'require_limit' : null,
    rules.no_select_star ? 'no_select_star' : null,
    rules.max_tables != null ? `max_tables=${rules.max_tables}` : null,
    rules.cost_limit != null ? `cost_limit=${rules.cost_limit}` : null,
    rules.max_estimated_rows != null ? `max_estimated_rows=${rules.max_estimated_rows}` : null,
  ].filter((flag): flag is string => flag !== null);

  return (
    <div className="p-5">
      <VStack className="gap-5 items-stretch">
        {detail.description && (
          <DetailRow label="Description">
            <Text level="body-small" className="text-content-layout-2">
              {detail.description}
            </Text>
          </DetailRow>
        )}
        {detail.intent && (
          <DetailRow label="Intent">
            <Text level="body-small" className="text-content-layout-3 italic">
              {detail.intent}
            </Text>
          </DetailRow>
        )}

        <Show when={masking.length > 0}>
          <DetailRow label={`Masking (${masking.length})`}>
            <VStack className="gap-1.5 items-stretch w-full">
              {masking.map(([pattern, type]) => (
                <HStack key={pattern} className="gap-2 items-center">
                  <Text level="mono-small" className="text-content-layout-1">
                    {pattern}
                  </Text>
                  <Icon name="arrow-right" label="maps to" className="w-3 h-3 text-content-layout-3" />
                  <Tag size="small" variant="informative" modifier="ghost" label={type} />
                </HStack>
              ))}
            </VStack>
          </DetailRow>
        </Show>

        <Show when={(restrictions.denied_columns?.length ?? 0) > 0}>
          <DetailRow label="Denied Columns">
            <HStack className="gap-1.5 items-center flex-wrap">
              {(restrictions.denied_columns ?? []).map((col) => (
                <Tag key={col} size="small" variant="negative" modifier="ghost" label={col} />
              ))}
            </HStack>
          </DetailRow>
        </Show>

        <Show when={(restrictions.allowed_tables?.length ?? 0) > 0}>
          <DetailRow label="Allowed Tables">
            <HStack className="gap-1.5 items-center flex-wrap">
              {(restrictions.allowed_tables ?? []).map((table) => (
                <Tag key={table} size="small" variant="positive" modifier="ghost" label={table} />
              ))}
            </HStack>
          </DetailRow>
        </Show>

        <Show when={requiredFilters.length > 0}>
          <DetailRow label="Required Filters">
            <VStack className="gap-1.5 items-stretch w-full">
              {requiredFilters.map(([table, columns]) => (
                <HStack key={table} className="gap-2 items-center">
                  <Text level="mono-small" className="text-content-layout-1">
                    {table}
                  </Text>
                  <Icon name="arrow-right" label="requires" className="w-3 h-3 text-content-layout-3" />
                  <HStack className="gap-1 items-center flex-wrap">
                    {columns.map((col) => (
                      <Tag key={col} size="small" variant="warning" modifier="ghost" label={col} />
                    ))}
                  </HStack>
                </HStack>
              ))}
            </VStack>
          </DetailRow>
        </Show>

        <Show when={activeRuleFlags.length > 0}>
          <DetailRow label="Rules">
            <HStack className="gap-1.5 items-center flex-wrap">{ruleTags(activeRuleFlags)}</HStack>
          </DetailRow>
        </Show>

        <DetailRow label="Limits">
          <HStack className="gap-4 items-center">
            <Text level="body-small" className="text-content-layout-2 tabular-nums">
              max_rows: {limits.max_rows ?? 1000}
            </Text>
            <Text level="body-small" className="text-content-layout-2 tabular-nums">
              timeout: {limits.timeout_seconds ?? 30}s
            </Text>
          </HStack>
        </DetailRow>
      </VStack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Editable guard form
// ---------------------------------------------------------------------------

function GuardEditor({
  form,
  setForm,
  nameEditable,
  showName,
}: {
  form: GuardForm;
  setForm: React.Dispatch<React.SetStateAction<GuardForm>>;
  nameEditable: boolean;
  // Hidden in intent mode, where the name field lives above the derive button.
  showName: boolean;
}) {
  const update = <K extends keyof GuardForm>(key: K, value: GuardForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const updateMask = (index: number, patch: Partial<MaskRow>) =>
    setForm((prev) => ({
      ...prev,
      masking: prev.masking.map((row, i) => (i === index ? { ...row, ...patch } : row)),
    }));

  const updateFilter = (index: number, patch: Partial<FilterRow>) =>
    setForm((prev) => ({
      ...prev,
      requiredFilters: prev.requiredFilters.map((row, i) =>
        i === index ? { ...row, ...patch } : row,
      ),
    }));

  const nameValid = !form.name.trim() || GUARD_NAME_PATTERN.test(form.name.trim());

  return (
    <VStack className="gap-5 items-stretch p-5">
      {/* Name + description */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <Show when={showName}>
          <VStack className="gap-1.5 items-start">
            <FieldLabel>Name</FieldLabel>
            <BaseInputText
              name="guard-name"
              value={form.name}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => update('name', e.target.value)}
              placeholder="pii-guard"
              disabled={!nameEditable}
              error={!nameValid}
            />
            <Show when={!nameValid}>
              <Text level="caption" className="text-content-negative-soft">
                Use letters, digits, _ or -; must start with a letter or underscore.
              </Text>
            </Show>
          </VStack>
        </Show>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Description</FieldLabel>
          <BaseInputText
            name="guard-description"
            value={form.description}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              update('description', e.target.value)
            }
            placeholder="Masks PII and blocks unbounded reads"
          />
        </VStack>
      </div>

      {/* Masking */}
      <VStack className="gap-2 items-stretch">
        <HStack className="justify-between items-center">
          <FieldLabel>Masking Patterns</FieldLabel>
          <Button
            label="Add pattern"
            icon="add"
            iconPosition="left"
            modifier="ghost"
            size="small"
            onClick={() =>
              update('masking', [...form.masking, { pattern: '', type: MASK_TYPES[0] }])
            }
          />
        </HStack>
        <Show when={form.masking.length === 0}>
          <Text level="caption" className="text-content-layout-3">
            No masking patterns. Add a pattern like <code>*.email</code> or{' '}
            <code>users.ssn</code>.
          </Text>
        </Show>
        {form.masking.map((row, index) => (
          <HStack key={index} className="gap-2 items-center">
            <div className="flex-1">
              <BaseInputText
                name={`mask-pattern-${index}`}
                value={row.pattern}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  updateMask(index, { pattern: e.target.value })
                }
                placeholder="*.email"
              />
            </div>
            <div className="w-44">
              <BaseInputSelect
                name={`mask-type-${index}`}
                options={MASK_TYPE_OPTIONS}
                value={row.type}
                onValueChange={(value) => updateMask(index, { type: value })}
              />
            </div>
            <Button
              label=""
              icon="trash"
              iconPosition="icon"
              modifier="ghost"
              variant="negative"
              size="small"
              onClick={() =>
                update(
                  'masking',
                  form.masking.filter((_, i) => i !== index),
                )
              }
            />
          </HStack>
        ))}
      </VStack>

      {/* Restrictions: denied columns + allowed tables */}
      <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Denied Columns</FieldLabel>
          <BaseInputTextarea
            name="denied-columns"
            value={form.deniedColumns}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
              update('deniedColumns', e.target.value)
            }
            placeholder={'users.password\nusers.ssn'}
            rows={3}
          />
          <Text level="caption" className="text-content-layout-3">
            One per line (or comma-separated).
          </Text>
        </VStack>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Allowed Tables</FieldLabel>
          <BaseInputTextarea
            name="allowed-tables"
            value={form.allowedTables}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
              update('allowedTables', e.target.value)
            }
            placeholder={'orders\nline_items'}
            rows={3}
          />
          <Text level="caption" className="text-content-layout-3">
            Empty means all tables are allowed.
          </Text>
        </VStack>
      </div>

      {/* Required filters */}
      <VStack className="gap-2 items-stretch">
        <HStack className="justify-between items-center">
          <FieldLabel>Required Filters</FieldLabel>
          <Button
            label="Add filter"
            icon="add"
            iconPosition="left"
            modifier="ghost"
            size="small"
            onClick={() =>
              update('requiredFilters', [...form.requiredFilters, { table: '', columns: '' }])
            }
          />
        </HStack>
        <Show when={form.requiredFilters.length === 0}>
          <Text level="caption" className="text-content-layout-3">
            No required filters. Add a table plus the columns any query must filter on.
          </Text>
        </Show>
        {form.requiredFilters.map((row, index) => (
          <HStack key={index} className="gap-2 items-center">
            <div className="w-48">
              <BaseInputText
                name={`filter-table-${index}`}
                value={row.table}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  updateFilter(index, { table: e.target.value })
                }
                placeholder="orders"
              />
            </div>
            <div className="flex-1">
              <BaseInputText
                name={`filter-columns-${index}`}
                value={row.columns}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  updateFilter(index, { columns: e.target.value })
                }
                placeholder="tenant_id, region"
              />
            </div>
            <Button
              label=""
              icon="trash"
              iconPosition="icon"
              modifier="ghost"
              variant="negative"
              size="small"
              onClick={() =>
                update(
                  'requiredFilters',
                  form.requiredFilters.filter((_, i) => i !== index),
                )
              }
            />
          </HStack>
        ))}
      </VStack>

      {/* Rule toggles */}
      <VStack className="gap-3 items-stretch">
        <FieldLabel>Rules</FieldLabel>
        <div className="grid grid-cols-1 tablet:grid-cols-3 gap-3">
          <HStack className="gap-2 items-center">
            <BaseInputSwitch
              name="require-where"
              checked={form.requireWhere}
              onCheckedChange={(checked) => update('requireWhere', checked === true)}
            />
            <Text level="label-small" className="text-content-layout-2">
              Require WHERE
            </Text>
          </HStack>
          <HStack className="gap-2 items-center">
            <BaseInputSwitch
              name="require-limit"
              checked={form.requireLimit}
              onCheckedChange={(checked) => update('requireLimit', checked === true)}
            />
            <Text level="label-small" className="text-content-layout-2">
              Require LIMIT
            </Text>
          </HStack>
          <HStack className="gap-2 items-center">
            <BaseInputSwitch
              name="no-select-star"
              checked={form.noSelectStar}
              onCheckedChange={(checked) => update('noSelectStar', checked === true)}
            />
            <Text level="label-small" className="text-content-layout-2">
              No SELECT *
            </Text>
          </HStack>
        </div>
      </VStack>

      {/* Numeric limits */}
      <div className="grid grid-cols-2 tablet:grid-cols-5 gap-4">
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Max Tables</FieldLabel>
          <BaseInputText
            name="max-tables"
            type="number"
            value={form.maxTables}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              update('maxTables', e.target.value)
            }
            placeholder="—"
          />
        </VStack>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Cost Limit</FieldLabel>
          <BaseInputText
            name="cost-limit"
            type="number"
            value={form.costLimit}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              update('costLimit', e.target.value)
            }
            placeholder="—"
          />
        </VStack>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Max Est. Rows</FieldLabel>
          <BaseInputText
            name="max-estimated-rows"
            type="number"
            value={form.maxEstimatedRows}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              update('maxEstimatedRows', e.target.value)
            }
            placeholder="—"
          />
        </VStack>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Max Rows</FieldLabel>
          <BaseInputText
            name="max-rows"
            type="number"
            value={form.maxRows}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) => update('maxRows', e.target.value)}
            placeholder="1000"
          />
        </VStack>
        <VStack className="gap-1.5 items-start">
          <FieldLabel>Timeout (s)</FieldLabel>
          <BaseInputText
            name="timeout-seconds"
            type="number"
            value={form.timeoutSeconds}
            onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
              update('timeoutSeconds', e.target.value)
            }
            placeholder="30"
          />
        </VStack>
      </div>
    </VStack>
  );
}

// ---------------------------------------------------------------------------
// Test SQL panel
// ---------------------------------------------------------------------------

// Radix Select forbids empty-string option values, so a sentinel stands in
// for "no target"; it is mapped back to undefined before the check request.
const NO_TARGET = '__none__';

function TestSqlPanel({ guardNames }: { guardNames: string[] }) {
  const { target: globalTarget } = useTarget();
  const { data: status } = useSystemStatus();
  const targetOptions = useMemo(
    () => [
      { value: NO_TARGET, label: 'No target (skip EXPLAIN checks)' },
      ...(status?.targets ?? []).map((t) => ({ value: t.name, label: t.name })),
    ],
    [status?.targets],
  );

  const [guard, setGuard] = useState<string>('');
  const [sql, setSql] = useState('');
  const [selectedTarget, setSelectedTarget] = useState<string>(globalTarget ?? NO_TARGET);
  // The global target resolves asynchronously; follow it until the user
  // picks a target themselves, so EXPLAIN checks aren't silently skipped.
  const userPickedTargetRef = useRef(false);
  useEffect(() => {
    if (!userPickedTargetRef.current && globalTarget) {
      setSelectedTarget(globalTarget);
    }
  }, [globalTarget]);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<GuardCheckResponse | null>(null);

  const effectiveGuard = guard || guardNames[0] || '';
  const guardOptions = guardNames.map((name) => ({ value: name, label: name }));

  const handleRun = async () => {
    if (!effectiveGuard || !sql.trim()) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      const response = await checkGuardSql(
        effectiveGuard,
        sql,
        selectedTarget === NO_TARGET ? undefined : selectedTarget,
      );
      setResult(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setRunning(false);
    }
  };

  return (
    <SectionCard icon="play" title="Test SQL">
      <VStack className="gap-4 items-stretch p-5">
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <VStack className="gap-1.5 items-start">
            <FieldLabel>Guard</FieldLabel>
            <BaseInputSelect
              name="test-guard"
              options={guardOptions}
              value={effectiveGuard}
              onValueChange={setGuard}
              placeholder="Select a guard"
              disabled={guardNames.length === 0}
            />
          </VStack>
          <VStack className="gap-1.5 items-start">
            <FieldLabel>Target (optional)</FieldLabel>
            <BaseInputSelect
              name="test-target"
              options={targetOptions}
              value={selectedTarget}
              onValueChange={(value) => {
                userPickedTargetRef.current = true;
                setSelectedTarget(value);
              }}
            />
          </VStack>
        </div>

        <VStack className="gap-1.5 items-start">
          <FieldLabel>SQL</FieldLabel>
          <BaseInputTextarea
            name="test-sql"
            value={sql}
            onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setSql(e.target.value)}
            placeholder="SELECT id FROM users WHERE id = 1"
            rows={4}
          />
        </VStack>

        <HStack className="justify-end">
          <Button
            label="Run Check"
            icon="play"
            iconPosition="left"
            variant="primary"
            onClick={handleRun}
            loading={running}
            disabled={running || !effectiveGuard || !sql.trim()}
          />
        </HStack>

        <Show when={!!error}>
          <div className="px-4 py-3 bg-surface-negative-soft/30 border border-border-negative-soft rounded-xl">
            <HStack className="gap-2 items-center">
              <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
              <Text level="body-small" className="text-content-negative-soft">
                {error}
              </Text>
            </HStack>
          </div>
        </Show>

        <Show when={!!result}>
          {result && <CheckResultView result={result} />}
        </Show>
      </VStack>
    </SectionCard>
  );
}

function CheckResultView({ result }: { result: GuardCheckResponse }) {
  return (
    <VStack className="gap-3 items-stretch">
      <div
        className={`px-4 py-3 rounded-xl border ${
          result.passed
            ? 'bg-surface-positive-soft/20 border-border-positive-soft'
            : 'bg-surface-negative-soft/30 border-border-negative-soft'
        }`}
      >
        <HStack className="gap-2 items-center">
          <Icon
            name={result.passed ? 'tick-double' : 'close'}
            label={result.passed ? 'Allowed' : 'Blocked'}
            className={`w-5 h-5 ${
              result.passed ? 'text-content-positive-soft' : 'text-content-negative-soft'
            }`}
          />
          <Text
            level="label-medium"
            className={result.passed ? 'text-content-positive-soft' : 'text-content-negative-soft'}
          >
            {result.passed ? 'ALLOWED' : 'BLOCKED'} by {result.guard}
          </Text>
        </HStack>
      </div>

      <Show when={result.results.length === 0}>
        <Text level="body-small" className="text-content-layout-3">
          No rule findings.
        </Text>
      </Show>

      <VStack className="gap-2 items-stretch">
        {result.results.map((finding, index) => {
          const level = checkLevel(finding.level);
          return (
            <div
              key={`${finding.guard_name}-${index}`}
              className="px-4 py-3 rounded-xl border border-border-layout-1 bg-surface-layout-2/40"
            >
              <HStack className="gap-3 items-start">
                <Icon
                  name={finding.passed ? 'tick' : levelIcon(level)}
                  label={level}
                  className={`w-4 h-4 mt-0.5 shrink-0 ${
                    finding.passed
                      ? 'text-content-positive-soft'
                      : level === 'block'
                        ? 'text-content-negative-soft'
                        : level === 'warn'
                          ? 'text-content-warning-soft'
                          : 'text-content-layout-3'
                  }`}
                />
                <VStack className="gap-1 items-start min-w-0">
                  <HStack className="gap-2 items-center">
                    <Tag
                      size="small"
                      variant={finding.passed ? 'positive' : levelVariant(level)}
                      modifier="ghost"
                      label={finding.passed ? 'PASS' : level.toUpperCase()}
                    />
                    <Text level="mono-small" className="text-content-layout-3">
                      {finding.guard_name}
                    </Text>
                  </HStack>
                  <Text level="body-small" className="text-content-layout-1">
                    {finding.message}
                  </Text>
                  {finding.suggestion && (
                    <Text level="caption" className="text-content-layout-3">
                      Suggestion: {finding.suggestion}
                    </Text>
                  )}
                </VStack>
              </HStack>
            </div>
          );
        })}
      </VStack>
    </VStack>
  );
}

// ---------------------------------------------------------------------------
// Guard list row
// ---------------------------------------------------------------------------

function GuardListRow({
  guard,
  expanded,
  onToggle,
  onEdit,
  onDelete,
  deleting,
}: {
  guard: GuardSummary;
  expanded: boolean;
  onToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  const detailQuery = useGuardDetail(expanded ? guard.name : null);

  return (
    <div className={expanded ? 'bg-surface-primary-soft/5' : ''}>
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left px-5 py-3 hover:bg-surface-layout-2/50 transition-colors cursor-pointer"
      >
        <HStack className="justify-between items-center gap-4">
          <HStack className="gap-3 items-center min-w-0 flex-1">
            <Icon
              name={expanded ? 'chevron-down' : 'chevron-right'}
              label="Toggle"
              className="w-4 h-4 text-content-layout-3 shrink-0"
            />
            <VStack className="gap-0.5 items-start min-w-0 w-full">
              <HStack className="gap-2 items-center">
                <Text level="label-medium" className="text-content-layout-1 truncate">
                  {guard.name}
                </Text>
                <Tag
                  size="small"
                  variant={guard.derived ? 'informative' : 'primary'}
                  modifier="ghost"
                  label={guard.derived ? 'derived' : 'manual'}
                />
              </HStack>
              <Show when={!!guard.description}>
                <Text level="caption" className="text-content-layout-3 line-clamp-1">
                  {guard.description}
                </Text>
              </Show>
            </VStack>
          </HStack>
          <HStack className="gap-2 items-center shrink-0">
            <HStack className="gap-1.5 items-center flex-wrap justify-end max-w-md">
              {ruleTags(guard.rules)}
            </HStack>
            {(guard.mask_count ?? 0) > 0 && (
              <Tag
                size="small"
                variant="warning"
                modifier="ghost"
                label={`${guard.mask_count} mask${guard.mask_count === 1 ? '' : 's'}`}
              />
            )}
            <Text level="mono-small" className="text-content-layout-3 tabular-nums">
              max {(guard.max_rows ?? 1000).toLocaleString()} rows
            </Text>
          </HStack>
        </HStack>
      </button>

      <AnimatePresence>
        {expanded && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden border-t border-border-layout-1"
          >
            <Show
              when={!detailQuery.isLoading && !!detailQuery.data}
              fallback={
                <div className="p-5">
                  <HStack className="gap-2 items-center">
                    <Show when={!detailQuery.isError} fallback={
                      <Text level="body-small" className="text-content-negative-soft">
                        Failed to load guard: {detailQuery.error instanceof Error ? detailQuery.error.message : ''}
                      </Text>
                    }>
                      <Spinner size="base" />
                      <Text level="body-small" className="text-content-layout-3">
                        Loading guard…
                      </Text>
                    </Show>
                  </HStack>
                </div>
              }
            >
              {detailQuery.data && <GuardDetailView detail={detailQuery.data} />}
            </Show>
            <div className="px-5 py-3 border-t border-border-layout-1 bg-surface-layout-2/30">
              <HStack className="gap-2 justify-end">
                <Button
                  label="Edit"
                  icon="edit"
                  iconPosition="left"
                  modifier="outline"
                  size="small"
                  onClick={onEdit}
                  disabled={!detailQuery.data}
                />
                <Button
                  label="Delete"
                  icon="trash"
                  iconPosition="left"
                  modifier="outline"
                  variant="negative"
                  size="small"
                  onClick={onDelete}
                  loading={deleting}
                />
              </HStack>
            </div>
          </m.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

type EditorMode = 'closed' | 'intent' | 'manual' | 'edit';

function GuardsPage() {
  const guardsQuery = useGuardsList();
  const createGuard = useCreateGuard();
  const updateGuard = useUpdateGuard();
  const deleteGuard = useDeleteGuard();

  const guards = guardsQuery.data?.guards ?? [];
  const guardNames = guards.map((g) => g.name);

  const [expandedName, setExpandedName] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);

  const [editorMode, setEditorMode] = useState<EditorMode>('closed');
  const [form, setForm] = useState<GuardForm>(emptyForm);
  const [deriving, setDeriving] = useState(false);
  const [deriveError, setDeriveError] = useState<string | null>(null);

  const isEditing = editorMode === 'edit';
  const editorOpen = editorMode !== 'closed';

  const openIntent = () => {
    setForm(emptyForm());
    setDeriveError(null);
    setEditorMode('intent');
  };
  const openManual = () => {
    setForm(emptyForm());
    setDeriveError(null);
    setEditorMode('manual');
  };
  const closeEditor = () => {
    setEditorMode('closed');
    setForm(emptyForm());
    setDeriveError(null);
  };

  const handleEdit = async (name: string) => {
    try {
      // Fetch fresh detail so the editor loads current config before editing.
      const detail = await fetchGuard(name);
      setForm(detailToForm(detail));
      setEditorMode('edit');
      window.scrollTo({ top: 0, behavior: 'smooth' });
    } catch (err) {
      toast({
        title: 'Failed to load guard',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    }
  };

  const handleDerive = async () => {
    if (!form.name.trim() || !form.intent.trim()) return;
    setDeriving(true);
    setDeriveError(null);
    try {
      const detail = await deriveGuard(form.name.trim(), form.intent.trim());
      // Keep the user's name/intent, fill the rest from the derived preview.
      setForm({ ...detailToForm(detail), name: form.name.trim(), intent: form.intent.trim() });
      toast({ title: 'Guard derived', description: 'Review the rules below, then save.' });
    } catch (err) {
      setDeriveError(err instanceof Error ? err.message : String(err));
    } finally {
      setDeriving(false);
    }
  };

  const handleSave = async () => {
    const detail = formToDetail(form);
    if (!detail.name || !GUARD_NAME_PATTERN.test(detail.name)) {
      toast({
        title: 'Invalid name',
        description: 'Provide a valid guard name before saving.',
        variant: 'negative',
      });
      return;
    }
    try {
      if (isEditing) {
        await updateGuard.mutateAsync({ name: detail.name, detail });
        toast({ title: 'Guard updated', description: detail.name });
      } else {
        await createGuard.mutateAsync(detail);
        toast({ title: 'Guard created', description: detail.name });
      }
      closeEditor();
    } catch (err) {
      toast({
        title: isEditing ? 'Failed to update guard' : 'Failed to create guard',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    }
  };

  const handleDelete = async (name: string) => {
    setPendingDelete(name);
    try {
      await deleteGuard.mutateAsync(name);
      toast({ title: 'Guard deleted', description: name });
      if (expandedName === name) setExpandedName(null);
    } catch (err) {
      toast({
        title: 'Failed to delete guard',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    } finally {
      setPendingDelete(null);
    }
  };

  const saving = createGuard.isPending || updateGuard.isPending;

  return (
    <div className="space-y-6 w-full">
      {/* Hero */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="justify-between items-start">
          <HStack className="gap-4 items-center">
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-info-soft to-surface-primary-soft flex items-center justify-center">
              <Icon name="user-shield" label="Guards" className="w-6 h-6 text-content-info-soft" />
            </div>
            <VStack className="gap-1 items-start">
              <Text as="h1" level="headline-3" className="text-content-layout-1">
                Query Guards
              </Text>
              <Text level="body-small" className="text-content-layout-3">
                Policies that mask columns, restrict tables, and enforce query rules.
              </Text>
            </VStack>
          </HStack>
          <Show when={!editorOpen}>
            <HStack className="gap-2 items-center">
              <Button
                label="Describe Intent"
                icon="sparkles"
                iconPosition="left"
                variant="rising"
                modifier="outline"
                onClick={openIntent}
              />
              <Button
                label="New Guard"
                icon="add"
                iconPosition="left"
                variant="primary"
                onClick={openManual}
              />
            </HStack>
          </Show>
        </HStack>
      </m.div>

      {/* Create / edit panel */}
      <AnimatePresence>
        {editorOpen && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25 }}
          >
            <SectionCard
              icon={isEditing ? 'edit' : editorMode === 'intent' ? 'sparkles' : 'add'}
              title={
                isEditing
                  ? `Edit Guard: ${form.name}`
                  : editorMode === 'intent'
                    ? 'Describe Intent'
                    : 'New Guard'
              }
              action={
                <Button
                  label="Cancel"
                  icon="close"
                  iconPosition="left"
                  modifier="ghost"
                  size="small"
                  onClick={closeEditor}
                />
              }
            >
              {/* Intent mode: name + intent + derive */}
              <Show when={editorMode === 'intent'}>
                <div className="px-5 pt-5">
                  <VStack className="gap-4 items-stretch">
                    <VStack className="gap-1.5 items-start">
                      <FieldLabel>Name</FieldLabel>
                      <BaseInputText
                        name="intent-name"
                        value={form.name}
                        onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                          setForm((prev) => ({ ...prev, name: e.target.value }))
                        }
                        placeholder="pii-guard"
                      />
                    </VStack>
                    <VStack className="gap-1.5 items-start">
                      <FieldLabel>Intent</FieldLabel>
                      <BaseInputTextarea
                        name="intent-text"
                        value={form.intent}
                        onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) =>
                          setForm((prev) => ({ ...prev, intent: e.target.value }))
                        }
                        placeholder="Mask all email and SSN columns, and never allow unbounded reads."
                        rows={3}
                      />
                    </VStack>
                    <HStack className="justify-between items-center">
                      <Show when={!!deriveError}>
                        <HStack className="gap-2 items-center">
                          <Icon name="alert" label="Error" className="w-4 h-4 text-content-negative-soft" />
                          <Text level="body-small" className="text-content-negative-soft">
                            {deriveError}
                          </Text>
                        </HStack>
                      </Show>
                      <div className="ml-auto">
                        <Button
                          label="Derive"
                          icon="sparkles"
                          iconPosition="left"
                          variant="rising"
                          onClick={handleDerive}
                          loading={deriving}
                          disabled={deriving || !form.name.trim() || !form.intent.trim()}
                        />
                      </div>
                    </HStack>
                  </VStack>
                  <div className="mt-4 border-t border-border-layout-1 -mx-5" />
                </div>
              </Show>

              {/* Editable form (all modes). In intent mode the name field lives
                  above the derive button, so it is hidden here. */}
              <GuardEditor
                form={form}
                setForm={setForm}
                nameEditable={!isEditing}
                showName={editorMode !== 'intent'}
              />

              <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
                <HStack className="justify-end gap-2">
                  <Button label="Cancel" modifier="ghost" onClick={closeEditor} />
                  <Button
                    label={isEditing ? 'Save Changes' : 'Create Guard'}
                    icon="tick"
                    iconPosition="left"
                    variant="primary"
                    onClick={handleSave}
                    loading={saving}
                  />
                </HStack>
              </div>
            </SectionCard>
          </m.div>
        )}
      </AnimatePresence>

      {/* Guard list */}
      <SectionCard icon="user-shield" title={`Guards (${guards.length})`}>
        <Show
          when={!guardsQuery.isLoading}
          fallback={
            <div className="p-5">
              <HStack className="gap-2 items-center">
                <Spinner size="base" />
                <Text level="body-small" className="text-content-layout-3">
                  Loading guards…
                </Text>
              </HStack>
            </div>
          }
        >
          <Show when={!guardsQuery.isError} fallback={
            <div className="p-5">
              <Text level="body-small" className="text-content-negative-soft">
                Failed to load guards: {guardsQuery.error instanceof Error ? guardsQuery.error.message : ''}
              </Text>
            </div>
          }>
            <Show
              when={guards.length > 0}
              fallback={
                <div className="p-8">
                  <VStack className="gap-3 items-center">
                    <Icon name="user-shield" label="No guards" className="w-8 h-8 text-content-layout-3" />
                    <Text level="body-small" className="text-content-layout-3">
                      No guards yet. Create one manually or describe your intent.
                    </Text>
                    <Show when={!editorOpen}>
                      <Button
                        label="New Guard"
                        icon="add"
                        iconPosition="left"
                        variant="primary"
                        size="small"
                        onClick={openManual}
                      />
                    </Show>
                  </VStack>
                </div>
              }
            >
              <div className="divide-y divide-border-layout-1">
                {guards.map((guard) => (
                  <GuardListRow
                    key={guard.name}
                    guard={guard}
                    expanded={expandedName === guard.name}
                    onToggle={() =>
                      setExpandedName((prev) => (prev === guard.name ? null : guard.name))
                    }
                    onEdit={() => handleEdit(guard.name)}
                    onDelete={() => handleDelete(guard.name)}
                    deleting={pendingDelete === guard.name}
                  />
                ))}
              </div>
            </Show>
          </Show>
        </Show>
      </SectionCard>

      {/* Test SQL */}
      <TestSqlPanel guardNames={guardNames} />
    </div>
  );
}
