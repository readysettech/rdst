// Agents workspace component — moved out of the route config into this
// route-ignored sibling (TanStack skips `-`-prefixed files) so the code-splitter
// can relocate the chat/markdown stack out of the eager entry chunk, and so the
// page can be embedded as the Ask workspace's Conversations view. The /agents
// route file is now a thin redirect to /ask?view=chat. See top.tsx §Defect D-1.

import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate } from '@tanstack/react-router';
import { ExperimentalBanner } from '../components/ExperimentalBanner';
import { Button } from '@rs/ui-new/button';
import { Card } from '@rs/ui-new/card';
import { TableHeaderCell } from '../components/TableHeaderCell';
import { ConfirmDialog } from '@rs/ui-new/confirm-dialog';
import { Disclosure } from '@rs/ui-new/disclosure';
import { Icon } from '@rs/ui-new/icon';
import { Markdown } from '@rs/ui-new/markdown';
import type { IconStrokeName } from '@rs/ui-icons/icon-name';
import { Show } from '@rs/ui-new/show';
import { Spinner } from '@rs/ui-new/spinner';
import { Tag } from '@rs/ui-new/tag';
import * as ScrollArea from '@rs/ui-new/scroll';
import { Text } from '@rs/ui-new/text';
import { HStack, VStack } from '@rs/ui-new/stack';
import { BaseInputText } from '@rs/ui-new/base-input-text';
import { BaseInputTextarea } from '@rs/ui-new/base-input-textarea';
import { BaseInputSelect } from '@rs/ui-new/base-input-select';
import { m, AnimatePresence } from '@rs/ui-new/motion';
import { toast } from '@rs/ui-new/use-toast';
import { InlineNotice } from '@rs/ui-new/error-state';
import { useSystemStatus } from '../lib/useSystemStatus';
import { fetchGuards } from '../lib/useGuards';
import {
  useAgentChat,
  useAgentsList,
  useCreateAgent,
  useDeleteAgent,
} from '../lib/useAgents';
import type {
  AgentCreateRequest,
  AgentSummary,
  ChatEvent,
  ChatToolCallEvent,
  ChatToolResultEvent,
  QueryToolData,
  SchemaToolData,
  TranscriptItem,
} from '../types/agents';

const AGENT_NAME_PATTERN = /^[a-zA-Z_][a-zA-Z0-9_-]*$/;

// ---------------------------------------------------------------------------
// Shared presentation helpers
// ---------------------------------------------------------------------------

function SectionCard({
  icon,
  title,
  action,
  children,
  className,
}: {
  icon: IconStrokeName;
  title: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <Card className={`w-full overflow-hidden ${className ?? ''}`}>
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

// Chat responses are untrusted LLM output: render markdown with the design
// system component but never raw HTML.
function MarkdownText({ text }: { text: string }) {
  return (
    <div className="text-body-small text-content-layout-1">
      <Markdown options={{ disableParsingRawHTML: true }}>{text}</Markdown>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create agent form
// ---------------------------------------------------------------------------

const NO_GUARD = '__none__';

interface AgentForm {
  name: string;
  target: string;
  guard: string;
  description: string;
  maxRows: string;
  timeoutSeconds: string;
  deniedColumns: string;
  allowedTables: string;
}

function emptyForm(defaultTarget: string): AgentForm {
  return {
    name: '',
    target: defaultTarget,
    guard: NO_GUARD,
    description: '',
    maxRows: '1000',
    timeoutSeconds: '30',
    deniedColumns: '',
    allowedTables: '',
  };
}

function splitList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function parseIntOr(value: string, fallback: number): number {
  const parsed = Number.parseInt(value.trim(), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function CreateAgentForm({ onClose }: { onClose: () => void }) {
  const { data: status } = useSystemStatus();
  const createAgent = useCreateAgent();

  const targets = status?.targets ?? [];
  const [form, setForm] = useState<AgentForm>(() => emptyForm(targets[0]?.name ?? ''));
  const [guardNames, setGuardNames] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchGuards()
      .then((res) => {
        if (cancelled) return;
        setGuardNames(res.guards.map((g) => g.name));
      })
      .catch(() => {
        // Guards are optional; leave the "no guard" default in place.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const update = <K extends keyof AgentForm>(key: K, value: AgentForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  const nameValid = !form.name.trim() || AGENT_NAME_PATTERN.test(form.name.trim());
  const targetOptions = targets.map((t) => ({ value: t.name, label: t.name }));

  // Plain-language "Can see" options: no-guard reads as full access to the
  // chosen database; a guard reads as its name. Values are unchanged, so the
  // request body is identical to before.
  const guardOptions = useMemo(
    () => [
      {
        value: NO_GUARD,
        label: form.target ? `Everything in ${form.target}` : 'Everything (no guard)',
      },
      ...guardNames.map((name) => ({ value: name, label: name })),
    ],
    [guardNames, form.target]
  );

  const handleCreate = async () => {
    const name = form.name.trim();
    if (!name || !AGENT_NAME_PATTERN.test(name)) {
      toast({
        title: 'Invalid name',
        description: 'Use letters, digits, _ or -; must start with a letter or underscore.',
        variant: 'negative',
      });
      return;
    }
    if (!form.target) {
      toast({ title: 'Target required', description: 'Pick a target.', variant: 'negative' });
      return;
    }

    const denied = splitList(form.deniedColumns);
    const allowed = splitList(form.allowedTables);
    const body: AgentCreateRequest = {
      name,
      target: form.target,
      description: form.description.trim(),
      max_rows: parseIntOr(form.maxRows, 1000),
      timeout_seconds: parseIntOr(form.timeoutSeconds, 30),
      guard: form.guard === NO_GUARD ? null : form.guard,
      denied_columns: denied.length > 0 ? denied : null,
      allowed_tables: allowed.length > 0 ? allowed : null,
    };

    try {
      await createAgent.mutateAsync(body);
      toast({ title: 'Agent created', description: name });
      onClose();
    } catch (err) {
      // 409 exists / 422 invalid surface the backend detail message.
      toast({
        title: 'Failed to create agent',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    }
  };

  return (
    <SectionCard
      icon="add"
      title="New Agent"
      action={
        <Button
          label="Cancel"
          icon="close"
          iconPosition="left"
          modifier="ghost"
          size="small"
          onClick={onClose}
        />
      }
    >
      <VStack className="gap-5 items-stretch p-5">
        {/* Default view = the three decisions that matter: name, database, and
            what the agent is allowed to see. Everything else is deferred to
            Advanced below. */}
        <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
          <VStack className="gap-1.5 items-start">
            <FieldLabel>Name</FieldLabel>
            <BaseInputText
              name="agent-name"
              value={form.name}
              onChange={(e: React.ChangeEvent<HTMLInputElement>) => update('name', e.target.value)}
              placeholder="orders-analyst"
              error={!nameValid}
            />
            <Show when={!nameValid}>
              <Text level="caption" className="text-content-negative-soft">
                Use letters, digits, _ or -; must start with a letter or underscore.
              </Text>
            </Show>
          </VStack>
          <VStack className="gap-1.5 items-start">
            <FieldLabel>Database</FieldLabel>
            <BaseInputSelect
              name="agent-target"
              options={targetOptions}
              value={form.target}
              onValueChange={(value) => update('target', value)}
              placeholder="Select a database"
              disabled={targetOptions.length === 0}
            />
          </VStack>
        </div>

        <VStack className="gap-1.5 items-start">
          <FieldLabel>Can see</FieldLabel>
          <div className="w-full">
            <BaseInputSelect
              name="agent-guard"
              options={guardOptions}
              value={form.guard}
              onValueChange={(value) => update('guard', value)}
            />
          </div>
          <Text level="caption" className="text-content-layout-3">
            Bind a guard to restrict what this agent may read.
          </Text>
        </VStack>

        <Disclosure
          title="Advanced"
          subtitle="description, row & time limits, table/column overrides"
        >
          <VStack className="gap-5 items-stretch">
            <VStack className="gap-1.5 items-start">
              <FieldLabel>Description</FieldLabel>
              <BaseInputText
                name="agent-description"
                value={form.description}
                onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                  update('description', e.target.value)
                }
                placeholder="Answers questions about orders and customers"
              />
            </VStack>

            <div className="grid grid-cols-2 tablet:grid-cols-4 gap-4">
              <VStack className="gap-1.5 items-start">
                <FieldLabel>Max Rows</FieldLabel>
                <BaseInputText
                  name="agent-max-rows"
                  type="number"
                  value={form.maxRows}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    update('maxRows', e.target.value)
                  }
                  placeholder="1000"
                />
              </VStack>
              <VStack className="gap-1.5 items-start">
                <FieldLabel>Timeout (s)</FieldLabel>
                <BaseInputText
                  name="agent-timeout"
                  type="number"
                  value={form.timeoutSeconds}
                  onChange={(e: React.ChangeEvent<HTMLInputElement>) =>
                    update('timeoutSeconds', e.target.value)
                  }
                  placeholder="30"
                />
              </VStack>
            </div>

            <div className="grid grid-cols-1 tablet:grid-cols-2 gap-4">
              <VStack className="gap-1.5 items-start">
                <FieldLabel>Denied Columns (optional)</FieldLabel>
                <BaseInputTextarea
                  name="agent-denied-columns"
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
                <FieldLabel>Allowed Tables (optional)</FieldLabel>
                <BaseInputTextarea
                  name="agent-allowed-tables"
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
          </VStack>
        </Disclosure>
      </VStack>

      <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
        <HStack className="justify-end gap-2">
          <Button label="Cancel" modifier="ghost" onClick={onClose} />
          <Button
            label="Create agent"
            icon="tick"
            iconPosition="left"
            variant="primary"
            onClick={handleCreate}
            loading={createAgent.isPending}
          />
        </HStack>
      </div>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Agent list
// ---------------------------------------------------------------------------

function AgentListRow({
  agent,
  selected,
  onSelect,
  onDelete,
  deleting,
}: {
  agent: AgentSummary;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
  deleting: boolean;
}) {
  // A native <button> may not wrap the delete Button (nested buttons are
  // invalid HTML), so the row is a clickable div. It is a listbox option
  // (role="option" + aria-selected, valid where role="button" was not) with a
  // keyboard handler. Selected = three cues: left accent bar, a one-step-lighter
  // raised surface, and elevation — never colour alone.
  return (
    <div
      role="option"
      tabIndex={0}
      aria-selected={selected}
      onClick={onSelect}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onSelect();
        }
      }}
      className={`group relative px-5 py-3 border-l-2 transition-[background,box-shadow,border-color] cursor-pointer outline-none focus-visible:shadow-focus ${
        selected
          ? 'border-l-border-primary-solid bg-surface-raised shadow-elevation-1'
          : 'border-l-transparent hover:bg-surface-layout-2/50'
      }`}
    >
      <VStack className="gap-1 items-stretch min-w-0">
        {/* Line 1: name + delete affordance (tertiary — revealed on hover,
            keyboard focus, or when the row is selected). */}
        <HStack className="justify-between items-start gap-2">
          <Text level="label-medium" className="truncate min-w-0 text-content-layout-1">
            {agent.name}
          </Text>
          <div
            className={`shrink-0 -mt-1 -mr-1.5 transition-opacity ${
              selected || deleting
                ? 'opacity-100'
                : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'
            }`}
          >
            <Button
              label=""
              aria-label={`Delete agent ${agent.name}`}
              icon="trash"
              iconPosition="icon"
              modifier="ghost"
              variant="negative"
              size="small"
              loading={deleting}
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            />
          </div>
        </HStack>

        {/* Line 2: badges (wrap) */}
        <HStack className="gap-1.5 items-center flex-wrap">
          <Tag size="small" variant="informative" modifier="ghost" label={agent.target} />
          <Show when={!!agent.guard}>
            <Tag size="small" variant="primary" modifier="ghost" label={`guard: ${agent.guard}`} />
          </Show>
        </HStack>

        {/* Line 3: description */}
        <Show when={!!agent.description}>
          <Text level="caption" className="text-content-layout-3 line-clamp-1">
            {agent.description}
          </Text>
        </Show>

        {/* Line 4: max rows */}
        <Text level="mono-small" className="text-content-layout-3 tabular-nums">
          max {(agent.max_rows ?? 1000).toLocaleString()} rows
        </Text>
      </VStack>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Transcript rendering
// ---------------------------------------------------------------------------

function isQueryToolData(data: unknown): data is QueryToolData {
  return !!data && typeof data === 'object' && 'sql' in data && 'columns' in data;
}

function isSchemaToolData(data: unknown): data is SchemaToolData {
  return !!data && typeof data === 'object' && 'tables' in data && 'source' in data;
}

function ToolCallChip({ event, target }: { event: ChatToolCallEvent; target: string | null }) {
  const input = event.input ?? {};
  let text: string;
  if (event.name === 'get_schema') {
    const tableName = (input as { table_name?: string }).table_name;
    text = tableName ? `schema: ${tableName}` : 'schema';
  } else {
    text = (input as { question?: string }).question ?? event.name;
  }
  void target;
  return (
    <HStack className="gap-2 items-center px-3 py-1.5 rounded-lg bg-surface-layout-2/60 border border-border-layout-1 w-fit">
      <Icon name="play" label="Tool call" className="w-3.5 h-3.5 text-content-layout-3 shrink-0" />
      <Text level="mono-small" className="text-content-layout-2">
        &gt; {text}
      </Text>
    </HStack>
  );
}

function QueryResultView({ data, target }: { data: QueryToolData; target: string | null }) {
  const [expanded, setExpanded] = useState(data.rows.length <= 10);
  const rows = expanded ? data.rows : data.rows.slice(0, 5);
  const hashShort = data.query_hash ? data.query_hash.slice(0, 8) : null;

  return (
    <VStack className="gap-2 items-stretch">
      <div className="rounded-lg bg-surface-layout-2/60 border border-border-layout-1 overflow-hidden">
        <pre className="px-3 py-2 text-mono-small text-content-layout-1 overflow-x-auto whitespace-pre-wrap">
          {data.sql}
        </pre>
      </div>

      <HStack className="gap-2 items-center flex-wrap">
        <Tag
          size="small"
          variant="informative"
          modifier="ghost"
          label={`${data.row_count} rows`}
        />
        <Tag
          size="small"
          variant="positive"
          modifier="ghost"
          label={`${data.execution_time_ms.toFixed(1)}ms`}
        />
        <Show when={data.truncated}>
          <Tag size="small" variant="warning" modifier="ghost" label="truncated" />
        </Show>
        <Show when={!!hashShort}>
          <Link
            to="/results"
            search={{ query: data.sql, target: target ?? undefined }}
            className="text-content-primary-soft hover:underline"
          >
            <Text level="mono-small" className="text-content-primary-soft">
              saved as {hashShort}
            </Text>
          </Link>
        </Show>
      </HStack>

      <Show when={data.rows.length > 0}>
        <div className="rounded-lg border border-border-layout-1 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2 border-b border-border-layout-1">
                  {data.columns.map((col, i) => (
                    <TableHeaderCell key={i} className="whitespace-nowrap">
                      {col}
                    </TableHeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rowIdx) => (
                  <tr
                    key={rowIdx}
                    className="border-b border-border-layout-1 last:border-b-0"
                  >
                    {row.map((cell, cellIdx) => (
                      <td
                        key={cellIdx}
                        className="px-3 py-2 text-content-layout-1 text-mono-small whitespace-nowrap"
                      >
                        {cell === null ? (
                          <span className="text-content-layout-3 italic">NULL</span>
                        ) : (
                          String(cell)
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <Show when={data.rows.length > 5 && !expanded}>
          <Button
            label={`Show all ${data.rows.length} rows`}
            modifier="ghost"
            size="small"
            onClick={() => setExpanded(true)}
          />
        </Show>
      </Show>
    </VStack>
  );
}

function SchemaResultView({ data }: { data: SchemaToolData }) {
  return (
    <VStack className="gap-1.5 items-stretch">
      <Text level="caption" className="text-content-layout-3 uppercase tracking-wider">
        Schema ({data.tables.length} tables, {data.source})
      </Text>
      <HStack className="gap-1.5 items-center flex-wrap">
        {data.tables.map((table, i) => {
          const name =
            table && typeof table === 'object' && 'name' in table
              ? String((table as { name: unknown }).name)
              : String(table);
          return <Tag key={i} size="small" variant="informative" modifier="ghost" label={name} />;
        })}
      </HStack>
    </VStack>
  );
}

function ToolResultView({
  event,
  target,
}: {
  event: ChatToolResultEvent;
  target: string | null;
}) {
  const data = event.data;
  if (event.success && isQueryToolData(data)) {
    return <QueryResultView data={data} target={target} />;
  }
  if (event.success && isSchemaToolData(data)) {
    return <SchemaResultView data={data} />;
  }
  return (
    <div
      className={`px-3 py-2 rounded-lg border ${
        event.success
          ? 'bg-surface-layout-2/40 border-border-layout-1'
          : 'bg-surface-negative-soft/30 border-border-negative-soft'
      }`}
    >
      <Text
        level="body-small"
        className={event.success ? 'text-content-layout-2' : 'text-content-negative-soft'}
      >
        {event.content}
      </Text>
    </div>
  );
}

// Renders a single ChatEvent. The switch is exhaustive over ChatEvent's union.
function EventBubble({ event, target }: { event: ChatEvent; target: string | null }) {
  switch (event.type) {
    case 'status':
      // Status events are surfaced via the hook's live statusMessage
      // indicator, not the transcript.
      return null;
    case 'thinking':
      return (
        <Text level="body-small" className="text-content-layout-3 italic whitespace-pre-wrap">
          {event.text}
        </Text>
      );
    case 'tool_call':
      return <ToolCallChip event={event} target={target} />;
    case 'tool_result':
      return <ToolResultView event={event} target={target} />;
    case 'response':
      return (
        <div className="px-4 py-3 rounded-2xl bg-surface-layout-1 border border-border-layout-1">
          <MarkdownText text={event.text} />
        </div>
      );
    case 'complete':
      return null;
    case 'error':
      return (
        <div className="px-4 py-3 rounded-2xl bg-surface-negative-soft/30 border border-border-negative-soft">
          <HStack className="gap-2 items-start">
            <Icon
              name="alert"
              label="Error"
              className="w-4 h-4 mt-0.5 text-content-negative-soft shrink-0"
            />
            <Text level="body-small" className="text-content-negative-soft whitespace-pre-wrap">
              {event.message}
            </Text>
          </HStack>
        </div>
      );
    default: {
      // Exhaustiveness guard: adding a variant to ChatEvent without handling it
      // here fails tsc.
      const _exhaustive: never = event;
      void _exhaustive;
      return null;
    }
  }
}

function TranscriptRow({ item, target }: { item: TranscriptItem; target: string | null }) {
  if (item.kind === 'user') {
    return (
      <HStack className="justify-end">
        <div className="max-w-[80%] px-4 py-2.5 rounded-2xl bg-surface-primary-soft text-content-primary-soft">
          <Text level="body-small" className="text-content-primary-soft whitespace-pre-wrap">
            {item.text}
          </Text>
        </div>
      </HStack>
    );
  }
  const bubble = <EventBubble event={item.event} target={target} />;
  if (bubble === null) return null;
  return <div className="max-w-[90%]">{bubble}</div>;
}

// ---------------------------------------------------------------------------
// Chat panel
// ---------------------------------------------------------------------------

function ChatPanel({ agent }: { agent: AgentSummary | null }) {
  const chat = useAgentChat(agent?.name ?? null);
  const navigate = useNavigate();
  const [input, setInput] = useState('');
  const scrollRef = useRef<HTMLDivElement | null>(null);

  const streaming = chat.state === 'streaming';

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chat.transcript]);

  const handleSend = () => {
    const value = input.trim();
    if (!value || streaming) return;
    setInput('');
    void chat.send(value);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  if (!agent) {
    return (
      <SectionCard
        icon="message-multiple"
        title="Chat"
        className="min-h-[520px] shadow-elevation-1"
      >
        <div className="p-12">
          <VStack className="gap-3 items-center">
            <Icon
              name="message-multiple"
              label="Select an agent"
              className="w-10 h-10 text-content-layout-3"
            />
            <Text level="body-medium" className="text-content-layout-2">
              Select an agent to start a conversation.
            </Text>
          </VStack>
        </div>
      </SectionCard>
    );
  }

  return (
    <SectionCard
      icon="message-multiple"
      title={`Chat: ${agent.name}`}
      className="flex flex-col shadow-elevation-1"
      action={
        <HStack className="gap-2 items-center">
          <Show when={chat.messageCount > 0}>
            <Tag
              size="small"
              variant="informative"
              modifier="ghost"
              label={`${chat.messageCount} message${chat.messageCount === 1 ? '' : 's'}`}
            />
          </Show>
          <Button
            label="New conversation"
            icon="add"
            iconPosition="left"
            modifier="ghost"
            size="small"
            onClick={() => void chat.clearSession()}
            disabled={streaming}
          />
        </HStack>
      }
    >
      <VStack className="items-stretch">
        <ScrollArea.Root className="h-[440px] w-full overflow-hidden">
          <ScrollArea.Viewport
            ref={scrollRef}
            className="h-full w-full custom-scrollbar [&>div]:h-full"
          >
            <div className="h-full px-5 py-4 flex flex-col gap-3">
              <Show
                when={chat.transcript.length > 0}
                fallback={
                  <VStack className="gap-2 items-center justify-center h-full">
                    <Icon
                      name="sparkles"
                      label="Start chatting"
                      className="w-8 h-8 text-content-layout-3"
                    />
                    <Text level="body-small" className="text-content-layout-2">
                      Ask a question about {agent.target}. History is kept in memory and clears when the
                      server restarts.
                    </Text>
                  </VStack>
                }
              >
                {chat.transcript.map((item) => (
                  <TranscriptRow key={item.id} item={item} target={agent.target} />
                ))}
                <Show when={!!chat.statusMessage}>
                  <HStack className="gap-2 items-center">
                    <Spinner size="base" />
                    <Text level="body-small" className="text-content-layout-3">
                      {chat.statusMessage}
                    </Text>
                  </HStack>
                </Show>
              </Show>
            </div>
          </ScrollArea.Viewport>
          <ScrollArea.Scrollbar
            className="flex select-none touch-none bg-border-layout-2 transition-[background,width] duration-fast ease-base w-2 hover:w-4"
            orientation="vertical"
          >
            <ScrollArea.Thumb className="relative flex-1 bg-content-layout-disabled transition-[background] duration-fast ease-base hover:bg-content-layout-3" />
          </ScrollArea.Scrollbar>
        </ScrollArea.Root>

        <div className="px-5 py-4 border-t border-border-layout-1 bg-surface-layout-2/30">
          {/* Upfront precondition: chatting runs an LLM, so surface the
              requirement before the user types and submits, not after it fails.
              Shown until the first message; a neutral info notice (state can't
              be probed here without an extra request). */}
          <Show when={chat.transcript.length === 0}>
            <InlineNotice
              errorClass="valid-negative"
              accent="info"
              icon="key"
              title="Chatting uses AI"
              message="Needs an Anthropic key or an active trial."
              action={{
                label: 'Configure',
                icon: 'arrow-right',
                onClick: () =>
                  navigate({
                    to: '/configure',
                    search: {
                      section: 'ai',
                      returnTo:
                        typeof window !== 'undefined'
                          ? `${window.location.pathname}${window.location.search}`
                          : undefined,
                    },
                  }),
              }}
              className="mb-3"
            />
          </Show>
          <HStack className="gap-2 items-end">
            <div className="flex-1">
              <BaseInputTextarea
                name="chat-input"
                value={input}
                onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={
                  streaming ? 'Waiting for response…' : 'Ask a question (Enter to send)…'
                }
                rows={2}
                className="min-h-[2.5lh] max-h-[8lh]"
                disabled={streaming}
              />
            </div>
            <Button
              label="Send"
              icon="arrow-up-right"
              iconPosition="left"
              variant="primary"
              onClick={handleSend}
              loading={streaming}
              disabled={streaming || !input.trim()}
            />
          </HStack>
        </div>
      </VStack>
    </SectionCard>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

// The empty account's first impression: an illustrated card with one emphasized
// CTA in the primary slot. The list, chat panel and composer are not rendered
// until an agent exists, so no inert UI competes with "create your first agent".
function EmptyAgentsState({ onCreate }: { onCreate: () => void }) {
  return (
    <Card className="w-full">
      <Card.Content className="py-16">
        <VStack className="gap-6 items-center text-center">
          <m.div
            initial={{ scale: 0.85, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            transition={{ duration: 0.3 }}
            className="w-20 h-20 rounded-3xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center shadow-elevation-1"
          >
            <Icon
              name="message-multiple"
              label=""
              aria-hidden="true"
              className="w-10 h-10 text-content-primary-soft"
            />
          </m.div>
          <VStack className="gap-2 items-center">
            <Text as="h2" level="headline-4" className="text-content-layout-1">
              No agents yet
            </Text>
            <Text level="body-medium" className="text-content-layout-2 max-w-sm">
              Create a read-only assistant for a database, then ask it questions in plain English.
            </Text>
          </VStack>
          <Button
            label="Create your first agent"
            icon="add"
            iconPosition="left"
            variant="primary"
            onClick={onCreate}
          />
        </VStack>
      </Card.Content>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation
// ---------------------------------------------------------------------------

// Deleting an agent is a one-click, irreversible removal of a scoped read
// policy (audit HIGH). Route the delete through the shared ConfirmDialog so the
// consequence is named at the point of action, the red lives on the confirm
// button — never on the row trigger — and Cancel is the focused default. The
// delete handler fires only on explicit confirm; Cancel / Escape / overlay
// never delete. Exported so the open→cancel / confirm-once behaviour can be
// unit-tested in isolation, mirroring SchemaReinitDialog. [USE-077, VIS-023]
export function AgentDeleteDialog({
  agent,
  isOpen,
  loading,
  onConfirm,
  onClose,
}: {
  agent: AgentSummary | null;
  isOpen: boolean;
  loading?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const name = agent?.name ?? '';
  return (
    <ConfirmDialog
      isOpen={isOpen}
      onClose={onClose}
      onConfirm={onConfirm}
      title={`Delete agent "${name}"?`}
      subtitle={
        agent
          ? `Reads ${agent.target}${agent.guard ? ` · guarded by ${agent.guard}` : ''}`
          : undefined
      }
      notice={{
        accent: 'negative',
        icon: 'alert',
        title: 'This permanently deletes the agent',
        message:
          'Deleting removes this agent and discards its in-memory chat history. This cannot be undone.',
      }}
      confirmLabel="Delete agent"
      confirmIcon="trash"
      confirmVariant="negative"
      loading={loading}
      blockCloseWhileLoading
    />
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

// `embedded` = rendered as the Ask workspace's Conversations view, which owns the
// page hero and view switcher. In that mode the agents page suppresses only its
// own identity block (icon tile + "Agents" title + subtitle); the experimental
// banner, credential notices, list, chat, and the New agent CTA all stay.
export function AgentsPage({ embedded }: { embedded?: boolean } = {}) {
  const agentsQuery = useAgentsList();
  const deleteAgent = useDeleteAgent();

  const agents = useMemo(() => agentsQuery.data?.agents ?? [], [agentsQuery.data]);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  // Kept true from the moment the create panel opens until its exit animation
  // finishes, so the header CTA / empty-state re-appear only after the panel has
  // fully collapsed — no doubled-CTA flash mid-animation.
  const [ctaSuppressed, setCtaSuppressed] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  // The agent awaiting delete confirmation (null = dialog closed). Holding the
  // whole summary lets the confirm name the target + its scope.
  const [confirmDelete, setConfirmDelete] = useState<AgentSummary | null>(null);

  const selectedAgent = agents.find((a) => a.name === selectedName) ?? null;

  // Loaded, no error, zero agents → the illustrated empty state owns the page.
  const showEmptyState =
    !agentsQuery.isLoading && !agentsQuery.isError && agents.length === 0;

  const openForm = () => {
    setCreating(true);
    setCtaSuppressed(true);
  };

  // Runs only from the confirm dialog's confirm button — never straight off the
  // row's trash affordance (which just opens the dialog). On success the dialog
  // closes; on failure it stays open with the error toast so the user can retry.
  const handleConfirmDelete = async () => {
    const agent = confirmDelete;
    if (!agent) return;
    setPendingDelete(agent.name);
    try {
      await deleteAgent.mutateAsync(agent.name);
      toast({ title: 'Agent deleted', description: agent.name });
      if (selectedName === agent.name) setSelectedName(null);
      setConfirmDelete(null);
    } catch (err) {
      toast({
        title: 'Failed to delete agent',
        description: err instanceof Error ? err.message : String(err),
        variant: 'negative',
      });
    } finally {
      setPendingDelete(null);
    }
  };

  return (
    <div className="space-y-6 w-full">
      <ExperimentalBanner name="Agents" />
      {/* Hero */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className={embedded ? 'justify-end items-start' : 'justify-between items-start'}>
          {/* Identity block is suppressed when embedded — the Ask workspace hero
              names the surface once. The New agent CTA is kept in both modes. */}
          {!embedded && (
            <HStack className="gap-4 items-center">
              <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
                <Icon
                  name="message-multiple"
                  label="Agents"
                  className="w-6 h-6 text-content-primary-soft"
                />
              </div>
              <VStack className="gap-1 items-start">
                <Text as="h1" level="headline-3" className="text-content-layout-1">
                  Agents
                </Text>
                <Text level="body-small" className="text-content-layout-2">
                  Ask your database questions in plain English — read-only.
                </Text>
              </VStack>
            </HStack>
          )}
          {/* Hidden in the empty state (the empty-state CTA is the single primary
              action there) and while the create panel is open or closing. */}
          <Show when={!ctaSuppressed && !showEmptyState}>
            {/* Steps down to outline while an agent is selected — the ChatPanel
                "Send" is the primary then, so the screen keeps exactly one solid
                primary [S4; VIS-011/016, VIS-022/023]. */}
            <Button
              label="New agent"
              icon="add"
              iconPosition="left"
              variant="primary"
              modifier={selectedAgent ? 'outline' : 'solid'}
              onClick={openForm}
            />
          </Show>
        </HStack>
      </m.div>

      {/* Create panel */}
      <AnimatePresence onExitComplete={() => setCtaSuppressed(false)}>
        {creating && (
          <m.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: 'auto' }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ duration: 0.25 }}
          >
            <CreateAgentForm onClose={() => setCreating(false)} />
          </m.div>
        )}
      </AnimatePresence>

      {showEmptyState ? (
        // Suppressed while the create panel is present/closing so the empty
        // state doesn't flash back in over the collapsing form.
        ctaSuppressed ? null : (
          <EmptyAgentsState onCreate={openForm} />
        )
      ) : (
        // Two-column layout: agent list (secondary) + chat (primary).
        <div className="grid grid-cols-1 laptop:grid-cols-[minmax(280px,340px)_1fr] gap-6 items-start">
          <SectionCard icon="user-group" title={`Agents (${agents.length})`}>
            <Show
              when={!agentsQuery.isLoading}
              fallback={
                <div className="p-5">
                  <HStack className="gap-2 items-center">
                    <Spinner size="base" />
                    <Text level="body-small" className="text-content-layout-3">
                      Loading agents…
                    </Text>
                  </HStack>
                </div>
              }
            >
              <Show
                when={!agentsQuery.isError}
                fallback={
                  <div className="p-5">
                    <Text level="body-small" className="text-content-negative-soft">
                      Failed to load agents:{' '}
                      {agentsQuery.error instanceof Error ? agentsQuery.error.message : ''}
                    </Text>
                  </div>
                }
              >
                <div
                  role="listbox"
                  aria-label="Your agents"
                  className="divide-y divide-border-layout-1"
                >
                  {agents.map((agent) => (
                    <AgentListRow
                      key={agent.name}
                      agent={agent}
                      selected={selectedName === agent.name}
                      onSelect={() => setSelectedName(agent.name)}
                      onDelete={() => setConfirmDelete(agent)}
                      deleting={pendingDelete === agent.name}
                    />
                  ))}
                </div>
              </Show>
            </Show>
          </SectionCard>

          {/* Keyed by agent so switching agents starts a fresh transcript and session. */}
          <ChatPanel key={selectedAgent?.name ?? 'none'} agent={selectedAgent} />
        </div>
      )}

      {/* One confirm dialog for the whole list; the pending agent drives it. */}
      <AgentDeleteDialog
        agent={confirmDelete}
        isOpen={confirmDelete !== null}
        loading={deleteAgent.isPending}
        onConfirm={handleConfirmDelete}
        onClose={() => setConfirmDelete(null)}
      />
    </div>
  );
}
