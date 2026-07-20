import { useState, useCallback } from "react";
import { useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "@rs/ui-new/button";
import { Show } from "@rs/ui-new/show";
import { TableHeaderCell } from "./TableHeaderCell";
import { Text } from "@rs/ui-new/text";
import { VStack, HStack } from "@rs/ui-new/stack";
import { Icon } from "@rs/ui-new/icon";
import { Spinner } from "@rs/ui-new/spinner";
import { BaseInputTextarea } from "@rs/ui-new/base-input-textarea";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Card } from "@rs/ui-new/card";
import { Tag } from "@rs/ui-new/tag";
import { CopyButton } from "@rs/ui-new/copy-button";
import { m } from "@rs/ui-new/motion";
import { useAsk } from "../lib/ask";
import type { AskClarificationQuestion, AskStatusEvent, AskSchemaLoadedEvent } from "../lib/ask";
import { fetchAskExamples, fetchAskHistory, type AskHistoryItem } from "../lib/api";
import { formatTimestamp } from "../lib/formatters";
import { classifyError } from "../lib/errorContract";
import { RoutableNotice } from "./RoutableNotice";
import { createCsvFilename, downloadCsv, toCsv } from "../lib/csv";
import { SQLDisplay } from "./SQLDisplay";

interface AskPanelProps {
  target?: string | null;
  disabled?: boolean;
}

// Human wording for the internal SchemaSource enum ('semantic' | 'database').
function sourceLabel(source: string | undefined): string {
  return source === "semantic" ? "semantic layer" : "live introspection";
}

// Per-target rail of past questions (rdst-e7s.17). Every answered ask is
// auto-saved with its original question text; this lists them newest-first,
// searchable, and re-asks one on click. The caller renders it only when there
// is history, so there is no inert empty state here.
function HistoryRail({
  items,
  onReask,
  disabled,
}: {
  items: AskHistoryItem[];
  onReask: (question: string) => void;
  disabled: boolean;
}) {
  const [filter, setFilter] = useState("");
  const shown = filter.trim()
    ? items.filter((i) => i.question.toLowerCase().includes(filter.toLowerCase()))
    : items;

  return (
    <VStack className="gap-3 items-stretch">
      <BaseInputText
        name="ask-history-search"
        placeholder="Search your questions"
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
      />
      <VStack className="gap-2 items-stretch max-h-80 overflow-y-auto">
        {shown.map((item) => (
          <button
            type="button"
            key={item.hash}
            disabled={disabled}
            onClick={() => onReask(item.question)}
            className="text-left rounded-lg border border-border-layout-1 bg-surface-layout-1 p-3 hover:border-border-primary-soft transition-colors disabled:opacity-50"
          >
            <Text level="label-small" className="text-content-layout-1 line-clamp-2">
              {item.question}
            </Text>
            <HStack className="gap-2 items-center mt-1.5">
              <Text level="caption" className="text-content-layout-3">
                {item.last_used ? formatTimestamp(item.last_used) : ""}
              </Text>
              <Text level="caption" className="text-content-primary-soft ml-auto">
                re-ask &rarr;
              </Text>
            </HStack>
          </button>
        ))}
      </VStack>
    </VStack>
  );
}

export function AskPanel({ target, disabled = false }: AskPanelProps) {
  const [question, setQuestion] = useState("");
  // The post-validation SQL is trust evidence, not the headline: collapsed by
  // default so the answer table leads. [ask.md answer-first; VIS-011]
  const [showSql, setShowSql] = useState(false);
  // Target stamped at stream time: provenance on a rendered answer must name
  // the target that ANSWERED it, never the live selector — after completion
  // the target-switch lock releases, and switching must not relabel an
  // existing answer. schema_loaded's backend-resolved target wins; this
  // submit-time stamp is the fallback for streams that never emit it.
  const [askedTarget, setAskedTarget] = useState<string | null>(null);
  const navigate = useNavigate();
  const {
    ask,
    resumeWithAnswers,
    state,
    status,
    schemaLoaded,
    clarification,
    sqlGenerated,
    result,
    error,
    reset,
  } = useAsk();

  // Example questions grounded in the current target's own schema, replacing
  // the hardcoded e-commerce prompts (ports schema-grounded examples from
  // CL 14059; the endpoint always returns a deterministic fallback). [USE-014]
  const { data: examples } = useQuery({
    queryKey: ["ask", "examples", target],
    queryFn: () => fetchAskExamples(target!),
    staleTime: 5 * 60_000,
    enabled: !!target,
  });
  const exampleQuestions = examples?.examples ?? [];

  // Past questions for this target, newest-first (rdst-e7s.17).
  const { data: history } = useQuery({
    queryKey: ["ask", "history", target],
    queryFn: () => fetchAskHistory(target, 50),
    enabled: !!target,
  });
  const historyItems = history?.items ?? [];

  const handleSubmit = useCallback(async () => {
    if (disabled) return;
    if (!question.trim()) return;
    setShowSql(false);
    setAskedTarget(target ?? null);
    await ask({
      question: question.trim(),
      target: target || undefined,
    });
  }, [disabled, question, target, ask]);

  // True retry: re-run the SAME question, never wiping the input [USE-077, F11].
  const handleRetry = useCallback(async () => {
    if (disabled || !question.trim()) return;
    setShowSql(false);
    setAskedTarget(target ?? null);
    await ask({ question: question.trim(), target: target || undefined });
  }, [disabled, question, target, ask]);

  const handleClarificationSubmit = useCallback(
    async (answers: Record<string, string>) => {
      if (disabled) return;
      await resumeWithAnswers(answers);
    },
    [disabled, resumeWithAnswers],
  );

  // Only "Ask another" clears the box [USE-077, F11].
  const handleNewQuestion = useCallback(() => {
    reset();
    setQuestion("");
    setShowSql(false);
  }, [reset]);

  // Refine: reopen the current question, pre-filled, to tweak it.
  const handleRefine = useCallback(() => {
    reset();
    setShowSql(false);
  }, [reset]);

  const handleExampleClick = useCallback((example: string) => {
    if (disabled) return;
    setQuestion(example);
  }, [disabled]);

  // Re-ask a past question: fill the input and answer it immediately
  // (rdst-e7s.17), unlike an example which only fills.
  const handleReask = useCallback(async (past: string) => {
    if (disabled || !past.trim()) return;
    setQuestion(past);
    setShowSql(false);
    setAskedTarget(target ?? null);
    await ask({ question: past.trim(), target: target || undefined });
  }, [disabled, target, ask]);

  // Hand off the EXACT post-validation SQL that ran (not the pre-validation
  // generated text) so /results does not force a spurious ParameterDialog and
  // analyzes the query the user is looking at. [U4 adaptation; T13/T15]
  const handleAnalyze = useCallback(() => {
    const sql = result?.sql || sqlGenerated?.sql;
    if (!sql) return;
    navigate({ to: "/results", search: { query: sql, target: target || undefined } });
  }, [result, sqlGenerated, target, navigate]);

  const handleViewSaved = useCallback(() => {
    navigate({ to: "/query-registry" });
  }, [navigate]);

  const isLoading = state === "loading" || state === "generating";

  // Show the SQL that actually ran (post-validation, from the result event).
  const executedSql = result?.sql ?? sqlGenerated?.sql ?? "";
  // Prefer the backend's explicit signal (T15); fall back to comparing the
  // generated vs executed SQL for streams that predate the field. [QW13]
  const limitAdded =
    result?.limit_added ??
    Boolean(
      result?.sql &&
        sqlGenerated?.sql &&
        /\blimit\b/i.test(result.sql) &&
        !/\blimit\b/i.test(sqlGenerated.sql),
    );
  // Live prop — used ONLY for the idle trust line (what the NEXT ask hits).
  const runsAgainst = target ?? "demo";
  // Stream-stamped — used for everything that labels a rendered answer.
  // Immutable once the answer renders: schemaLoaded/askedTarget only change
  // when a new stream starts (which unmounts the answer first).
  const answeredFrom = schemaLoaded?.target || askedTarget || "demo";
  const provenanceSource = sourceLabel(schemaLoaded?.source);
  const savedTag = result?.query_tag || "";

  return (
    <VStack className="gap-6 w-full">
      {/* Question Input */}
      {state === "idle" && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          {/* The Ask card reads as raised above the page — the single focal
              region of the idle state [VIS-075, VIS-080, VIS-105]. */}
          <Card className="w-full overflow-hidden bg-surface-raised shadow-elevation-1">
            <Card.Content className="p-0">
              {/* Light sparkle identity motif at low contrast — the empty state
                  as a designed first impression, decorative/AT-hidden
                  [VIS-102, VIS-115]. */}
              <div
                aria-hidden="true"
                className="pointer-events-none absolute -right-5 -top-5 opacity-5"
              >
                <Icon
                  name="sparkles"
                  label=""
                  className="h-28 w-28 text-content-primary-soft"
                />
              </div>
              {/* Input area — sits above the motif [paint order via relative]. */}
              <div className="relative p-5">
                <div className="relative">
                  <BaseInputTextarea
                    placeholder="Ask a question about your data..."
                    value={question}
                    onChange={(e) => setQuestion(e.target.value)}
                    rows={4}
                    className="w-full pr-12 text-body-medium"
                    disabled={disabled}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        handleSubmit();
                      }
                    }}
                  />
                </div>
                {/* Persistent read-only assurance on the info-soft surface, with
                    the target surfaced — trust stays visible up-front [F2;
                    USE-065, VIS-014]. */}
                <HStack className="gap-2 items-center mt-3 w-fit max-w-full rounded-lg bg-surface-info-soft px-3 py-1.5">
                  <Icon name="user-shield" label="Read-only" className="w-3.5 h-3.5 text-content-info-soft shrink-0" />
                  <Text level="caption" className="text-content-info-soft">
                    Read-only against{" "}
                    <span className="font-semibold">{runsAgainst}</span>
                    {" "}· writes blocked · capped at 1,000 rows
                  </Text>
                </HStack>
                <HStack className="justify-between items-center mt-4">
                  <HStack className="gap-2 items-center">
                    <Icon name="info" label="Hint" className="w-4 h-4 text-content-layout-3" />
                    <Text level="caption" className="text-content-layout-3">
                      Press Enter to submit, Shift+Enter for new line
                    </Text>
                  </HStack>
                  <Button
                    onClick={handleSubmit}
                    disabled={disabled || !question.trim()}
                    variant="rising"
                    modifier="solid"
                    label="Ask"
                    icon="sparkles"
                    iconPosition="left"
                  />
                </HStack>
              </div>

              {/* Schema-derived example questions — only once a target exists
                  and the backend returned some (hidden otherwise, no inert
                  affordances). [VIS-103, USE-014] */}
              {!!target && exampleQuestions.length > 0 && (
                <div className="border-t border-border-layout-1 bg-surface-layout-2/50 p-4">
                  <Text
                    level="overline"
                    className="text-content-layout-3 uppercase tracking-wider mb-3"
                  >
                    Try an example
                  </Text>
                  <div className="flex flex-wrap gap-2">
                    {exampleQuestions.map((example, i) => (
                      <m.button
                        key={i}
                        className="px-3 py-1.5 rounded-lg bg-surface-layout-1 border border-border-layout-1 text-content-layout-2 text-label-small hover:border-border-primary-soft hover:text-content-primary-soft transition-colors text-left"
                        onClick={() => handleExampleClick(example)}
                        disabled={disabled}
                        initial={{ opacity: 0, scale: 0.9 }}
                        animate={{ opacity: 1, scale: 1 }}
                        transition={{ delay: 0.1 + i * 0.05 }}
                        whileHover={{ scale: 1.02 }}
                        whileTap={{ scale: 0.98 }}
                      >
                        {example}
                      </m.button>
                    ))}
                  </div>
                </div>
              )}

              {/* Per-target question history (rdst-e7s.17): only shown once there
                  is history, mirroring the examples' no-inert-affordances rule. */}
              {!!target && historyItems.length > 0 && (
                <div className="border-t border-border-layout-1 bg-surface-layout-2/50 p-4">
                  <Text
                    level="overline"
                    className="text-content-layout-3 uppercase tracking-wider mb-3"
                  >
                    Recent questions
                  </Text>
                  <HistoryRail
                    items={historyItems}
                    onReask={handleReask}
                    disabled={disabled}
                  />
                </div>
              )}
            </Card.Content>
          </Card>
        </m.div>
      )}

      {/* Loading State */}
      {isLoading && (
        <m.div
          className="py-12"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3 }}
        >
          <LoadingState status={status} schemaLoaded={schemaLoaded} question={question} />
        </m.div>
      )}

      {/* Clarification Needed */}
      {state === "clarification_needed" && clarification && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <ClarificationPanel
            question={question}
            questions={clarification.questions}
            onSubmit={handleClarificationSubmit}
            onEditQuestion={handleRefine}
            disabled={disabled}
          />
        </m.div>
      )}

      {/* Answer (success) — answer-first: table primary, SQL collapsed. */}
      {state === "complete" && result && (
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
          className="w-full max-w-5xl"
        >
          <VStack className="gap-4 items-start w-full">
            {/* Echoed question + provenance (2nd) [USE-077] */}
            <VStack className="gap-1 items-start w-full">
              {question && (
                <Text level="headline-5" className="text-content-layout-1">
                  {question}
                </Text>
              )}
              <HStack className="gap-2 items-center flex-wrap">
                <Icon name="database" label="Source" className="w-3.5 h-3.5 text-content-layout-3" />
                <Text level="caption" className="text-content-layout-3">
                  Answered from{" "}
                  <span className="font-medium text-content-layout-2">{answeredFrom}</span>
                  {" "}via {provenanceSource}
                </Text>
                {sqlGenerated?.explanation && (
                  <>
                    <span className="text-content-layout-3">·</span>
                    <Text level="caption" className="text-content-layout-2">
                      {sqlGenerated.explanation}
                    </Text>
                  </>
                )}
              </HStack>
            </VStack>

            {/* Results table = PRIMARY [VIS-011, VIS-110] */}
            <div className="w-full">
              <ResultsTable result={result} target={answeredFrom} />
            </div>

            {/* Collapsed post-validation SQL disclosure [ask.md item 3] */}
            <SqlDisclosure
              sql={executedSql}
              open={showSql}
              onToggle={() => setShowSql((v) => !v)}
              limitAdded={limitAdded}
            />

            {/* Follow-through actions co-located with the answer [VIS-127] */}
            <VStack className="gap-3 items-start w-full">
              <HStack className="gap-3 items-center flex-wrap">
                <Button
                  onClick={handleAnalyze}
                  variant="rising"
                  modifier="solid"
                  size="small"
                  label="Analyze this query"
                  icon="querypilot"
                  iconPosition="left"
                />
              </HStack>
              {/* Surface the otherwise-invisible auto-save [USE-065]. */}
              {savedTag && (
                <HStack className="gap-2 items-center">
                  <Icon name="tick-double" label="Saved" className="w-4 h-4 text-content-positive-soft shrink-0" />
                  <Text level="caption" className="text-content-layout-3">
                    Saved to Queries as{" "}
                    <span className="font-medium text-content-layout-2">{savedTag}</span>
                  </Text>
                  <button
                    type="button"
                    onClick={handleViewSaved}
                    className="text-content-primary-soft text-label-small hover:underline"
                  >
                    View →
                  </button>
                </HStack>
              )}
            </VStack>

            {/* Ask-another: a fresh-question affordance set apart from the
                answer it follows — separator, left title+description, right
                button [Ask 4; VIS-036, VIS-104]. */}
            <div className="w-full border-t border-border-layout-1" />
            <HStack className="justify-between items-center gap-4 w-full flex-wrap">
              <VStack className="gap-0.5 items-start">
                <Text level="label-small" className="text-content-layout-1">
                  Ask another question
                </Text>
                <Text level="caption" className="text-content-layout-3">
                  Start fresh — this answer stays in Saved Queries.
                </Text>
              </VStack>
              <Button
                onClick={handleNewQuestion}
                variant="primary"
                modifier="outline"
                size="small"
                label="Ask another"
                icon="add"
                iconPosition="left"
              />
            </HStack>
          </VStack>
        </m.div>
      )}

      {/* Error State */}
      {state === "error" && error && (
        <m.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.3 }}
        >
          <ErrorState error={error} onRetry={handleRetry} onNewQuestion={handleNewQuestion} />
        </m.div>
      )}
    </VStack>
  );
}

// Loading State Component
function LoadingState({
  status,
  schemaLoaded,
  question,
}: {
  status?: AskStatusEvent;
  schemaLoaded?: AskSchemaLoadedEvent;
  question?: string;
}) {
  const stages = [
    { id: "loading_schema", label: "Loading Schema", icon: "database" as const },
    { id: "understanding", label: "Understanding", icon: "sparkles" as const },
    { id: "generate", label: "Generating SQL", icon: "querypilot" as const },
  ];

  const currentStageIndex = Math.max(
    0,
    stages.findIndex((s) => s.id === status?.phase),
  );

  return (
    <div className="max-w-md mx-auto">
      {/* Echoed question stays in view while we work [USE-077]. */}
      {question && (
        <Text level="body-small" className="text-content-layout-3 text-center mb-6">
          {question}
        </Text>
      )}
      {/* Stage indicators */}
      <div className="flex items-center justify-center mb-6">
        {stages.map((stage, index) => {
          const isComplete = index < currentStageIndex;
          const isCurrent = index === currentStageIndex;
          const isPending = index > currentStageIndex;

          return (
            <div key={stage.id} className="flex items-center">
              <div className="flex flex-col items-center gap-2">
                <m.div
                  className={`
                    relative w-12 h-12 rounded-xl flex items-center justify-center transition-all
                    ${isComplete ? "bg-surface-positive-soft" : ""}
                    ${isCurrent ? "bg-surface-primary-soft ring-2 ring-border-primary-soft" : ""}
                    ${isPending ? "bg-surface-layout-1 border border-border-layout-1" : ""}
                  `}
                  initial={false}
                  animate={isCurrent ? { scale: [1, 1.05, 1] } : { scale: 1 }}
                  transition={{ duration: 1.5, repeat: isCurrent ? Number.POSITIVE_INFINITY : 0, ease: "easeInOut" }}
                >
                  {isComplete ? (
                    <Icon
                      name="tick-double"
                      label="Complete"
                      className="w-5 h-5 text-content-positive-soft"
                    />
                  ) : isCurrent ? (
                    <Spinner size="base" />
                  ) : (
                    <Icon
                      name={stage.icon}
                      label={stage.label}
                      className="w-5 h-5 text-content-layout-3"
                    />
                  )}
                </m.div>
                {/* Visible stage caption, not icon-only [USE-012, F10]. */}
                <Text
                  level="caption"
                  className={
                    isCurrent
                      ? "text-content-primary-soft"
                      : isComplete
                        ? "text-content-positive-soft"
                        : "text-content-layout-3"
                  }
                >
                  {stage.label}
                </Text>
              </div>
              {index < stages.length - 1 && (
                <div className="w-12 mx-1.5 h-0.5 rounded-full overflow-hidden bg-surface-layout-1 -mt-6">
                  <m.div
                    className="h-full bg-content-positive-soft"
                    initial={{ width: "0%" }}
                    animate={{ width: index < currentStageIndex ? "100%" : "0%" }}
                    transition={{ duration: 0.5 }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Current stage info */}
      <div className="text-center mb-6">
        <m.div
          key={status?.phase || "loading"}
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <Text level="body-medium" className="text-content-layout-2">
            {status?.message || "Processing your question..."}
          </Text>
        </m.div>
      </div>

      {/* Schema info */}
      {schemaLoaded && (
        <m.div
          className="bg-surface-layout-1 rounded-xl p-4 border border-border-layout-1"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <HStack className="gap-3 items-center justify-center">
            <Icon name="database" label="Schema" className="w-4 h-4 text-content-positive-soft" />
            <Text level="body-small" className="text-content-layout-2">
              Schema loaded:{" "}
              <span className="text-content-layout-1 font-medium">
                {schemaLoaded.table_count} tables
              </span>{" "}
              from {sourceLabel(schemaLoaded.source)}
            </Text>
          </HStack>
        </m.div>
      )}
    </div>
  );
}

// Collapsed disclosure for the post-validation SQL that actually ran.
// NEEDS VARIANT (C-07/C-08): a shared Disclosure primitive with aria-expanded;
// inlined here for now.
function SqlDisclosure({
  sql,
  open,
  onToggle,
  limitAdded,
}: {
  sql: string;
  open: boolean;
  onToggle: () => void;
  limitAdded?: boolean;
}) {
  if (!sql) return null;
  return (
    <div className="w-full rounded-xl border border-border-layout-1 bg-surface-layout-1 overflow-hidden">
      {/* Toggle and Copy are siblings — never nest a <button> in a <button>. */}
      <div className="w-full flex items-center justify-between gap-3 px-4 py-3 hover:bg-surface-layout-2/50 transition-colors">
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
        >
          <Icon
            name={open ? "arrow-down" : "arrow-right"}
            label={open ? "Collapse" : "Expand"}
            className="w-4 h-4 text-content-layout-3 shrink-0"
          />
          <Text level="label-small" className="text-content-layout-2">
            Show the SQL that ran
          </Text>
          <Text level="caption" className="text-content-layout-3">
            · read-only, capped at 1,000
          </Text>
        </button>
        <CopyButton text={sql} />
      </div>
      {open && (
        <div className="border-t border-border-layout-1">
          <div className="bg-surface-layout-2">
            <SQLDisplay sql={sql} className="p-4" />
          </div>
          {limitAdded && (
            <div className="p-4 border-t border-border-layout-1">
              <HStack className="gap-2 items-start">
                <Icon
                  name="info"
                  label="Note"
                  className="w-4 h-4 text-content-info-soft mt-0.5 shrink-0"
                />
                <Text level="body-small" className="text-content-layout-2 leading-relaxed">
                  A <code className="font-mono">LIMIT</code> was added to keep the result set bounded.
                </Text>
              </HStack>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Results Table Component
function ResultsTable({
  result,
  target,
}: {
  result: { columns: string[]; rows: any[][]; row_count: number; execution_time_ms: number };
  target?: string;
}) {
  const handleDownloadCsv = useCallback(() => {
    const csv = toCsv(result.columns, result.rows);
    downloadCsv(csv, createCsvFilename());
  }, [result]);

  const hasRows = result.rows.length > 0;

  return (
    // The Answer card is the answer-state focal region — raised above the page
    // so the results table leads [VIS-011, VIS-075, VIS-105].
    <Card className="w-full overflow-hidden bg-surface-raised shadow-elevation-1">
      <Card.Header className="border-b border-border-layout-1">
        <HStack className="justify-between items-center w-full">
          <HStack className="gap-3 items-center">
            <div className="w-8 h-8 rounded-lg bg-surface-info-soft flex items-center justify-center">
              <Icon name="dashboard" label="Results" className="w-4 h-4 text-content-info-soft" />
            </div>
            <Card.Title>Answer</Card.Title>
          </HStack>
          <HStack className="gap-3 items-center">
            {hasRows && (
              <Button
                onClick={handleDownloadCsv}
                variant="primary"
                modifier="outline"
                size="small"
                label="Download CSV"
                icon="arrow-down"
                iconPosition="left"
              />
            )}
            <Tag
              variant="informative"
              modifier="ghost"
              size="small"
              label={`${result.row_count} rows`}
            />
            <Tag
              variant="positive"
              modifier="ghost"
              size="small"
              label={`${result.execution_time_ms.toFixed(1)}ms`}
            />
            {target && (
              <Tag variant="informative" modifier="ghost" size="small" label={target} />
            )}
          </HStack>
        </HStack>
      </Card.Header>
      <Card.Content className="p-0 overflow-hidden">
        {hasRows ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2 border-b border-border-layout-1">
                  {result.columns.map((col, i) => (
                    <TableHeaderCell key={i} className="whitespace-nowrap">
                      {col}
                    </TableHeaderCell>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.rows.slice(0, 50).map((row, rowIdx) => (
                  <m.tr
                    key={rowIdx}
                    className="border-b border-border-layout-1 last:border-b-0 hover:bg-surface-layout-2/50 transition-colors"
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: Math.min(rowIdx * 0.02, 0.5) }}
                  >
                    {row.map((cell, cellIdx) => (
                      <td
                        key={cellIdx}
                        className="px-4 py-3 text-content-layout-1 text-mono-small whitespace-nowrap"
                      >
                        {cell === null ? (
                          <span className="text-content-layout-3 italic">NULL</span>
                        ) : (
                          String(cell)
                        )}
                      </td>
                    ))}
                  </m.tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          // Distinct 0-row state — no header-only CSV, a refine hint [VIS-102, F12].
          <div className="p-8 text-center">
            <Icon
              name="empty"
              label="No rows"
              className="w-12 h-12 text-content-layout-3 mx-auto mb-3"
            />
            <Text level="body-medium" className="text-content-layout-2">
              No rows matched
            </Text>
            <Text level="body-small" className="text-content-layout-3 mt-1">
              Try broadening or rephrasing your question.
            </Text>
          </div>
        )}
      </Card.Content>
      {result.rows.length > 50 && (
        <Card.Footer className="border-t border-border-layout-1">
          <HStack className="gap-2 items-center">
            <Icon name="info" label="Info" className="w-4 h-4 text-content-layout-3" />
            <Text level="body-small" className="text-content-layout-3">
              Showing first 50 of {result.rows.length} rows
            </Text>
          </HStack>
        </Card.Footer>
      )}
    </Card>
  );
}

// Error State Component
function ErrorState({
  error,
  onRetry,
  onNewQuestion,
}: {
  error: { message: string; phase?: string | null };
  onRetry: () => void;
  onNewQuestion: () => void;
}) {
  // Route by structured error class, not a message regex: an AI-credential
  // failure (provider / trial keyservice) renders the routable credential
  // notice instead of an unwinnable retry. The ask SSE error carries no code
  // yet, so classify from the message — the same shared classifier the rest
  // of the app uses.
  const errorClass = classifyError({ code: '', message: error.message });
  const isAuthenticationError =
    errorClass === 'provider' || errorClass === 'rdst-service';
  const phaseLabels: Record<string, string> = {
    config: 'Configuration',
    schema: 'Loading database schema',
    filter: 'Selecting relevant tables',
    clarify: 'Clarifying the question',
    generate: 'Generating SQL',
    validate: 'Validating SQL',
    execute: 'Running the query',
  };
  const phaseLabel = error.phase ? phaseLabels[error.phase] : undefined;
  if (isAuthenticationError) {
    return (
      <VStack className="gap-3 items-start">
        <RoutableNotice
          kind={errorClass === 'rdst-service' ? 'trial-exhausted' : 'key-needed'}
          title="AI service authentication failed"
          message={error.message}
          className="w-full"
        />
        {phaseLabel && (
          <Tag
            variant="negative"
            modifier="ghost"
            size="small"
            label={`Failed while: ${phaseLabel}`}
          />
        )}
      </VStack>
    );
  }
  const title =
    error.phase === 'generate' ? "Couldn't generate SQL" : 'Request failed';

  return (
    <div className="bg-surface-negative-soft/50 border border-border-negative-soft rounded-xl p-6">
      <HStack className="gap-4 items-start">
        <div className="w-12 h-12 rounded-xl bg-surface-negative-soft flex items-center justify-center shrink-0">
          <Icon name="alert" label="Error" className="w-6 h-6 text-content-negative-soft" />
        </div>
        <VStack className="gap-3 items-start flex-1">
          <VStack className="gap-1 items-start">
            <Text level="headline-4" className="text-content-negative-soft">
              {title}
            </Text>
            <Text level="body-small" className="text-content-layout-2 leading-relaxed">
              {error.message}
            </Text>
          </VStack>
          {phaseLabel && (
            <Tag
              variant="negative"
              modifier="ghost"
              size="small"
              label={`Failed while: ${phaseLabel}`}
            />
          )}
          <HStack className="gap-3 items-center">
            <Button
              onClick={onRetry}
              variant="primary"
              modifier="outline"
              size="small"
              label="Try again"
              icon="arrow-left"
              iconPosition="left"
            />
            <Button
              onClick={onNewQuestion}
              variant="primary"
              modifier="ghost"
              size="small"
              label="Ask another"
              icon="add"
              iconPosition="left"
            />
          </HStack>
        </VStack>
      </HStack>
    </div>
  );
}

// Clarification Panel Component
interface ClarificationPanelProps {
  question?: string;
  questions: AskClarificationQuestion[];
  onSubmit: (answers: Record<string, string>) => void;
  onEditQuestion?: () => void;
  disabled?: boolean;
}

const CUSTOM_OPTION_VALUE = "__custom__";

function buildAnswers(
  selectedOptions: Record<string, string>,
  customInputs: Record<string, string>,
): Record<string, string> {
  const answers: Record<string, string> = {};
  for (const [id, value] of Object.entries(selectedOptions)) {
    if (value === CUSTOM_OPTION_VALUE) {
      const custom = customInputs[id]?.trim();
      if (custom) answers[id] = custom;
    } else {
      answers[id] = value;
    }
  }
  return answers;
}

// Selectable radio-card for a clarification option. The decision the user must
// make must not be the faintest text on screen: unselected options render at
// content-layout-2 (≥4.5:1) as bordered cards; only the selected card carries
// the single purple accent. Replaces the faint labeled-circle radio stack.
// NEEDS VARIANT (design-system): a shared `radio-card` — inlined here for now,
// same convention as SqlDisclosure. [F4; VIS-011, VIS-108, VIS-109, USE-004]
function ClarificationOption({
  label,
  selected,
  onSelect,
  disabled,
}: {
  label: string;
  selected: boolean;
  onSelect: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="radio"
      aria-checked={selected}
      onClick={onSelect}
      disabled={disabled}
      className={`flex w-full items-center gap-3 rounded-xl border-(length:--border-base) px-4 py-3 text-left transition-colors duration-fast ease-base focus-visible:outline-none focus-visible:shadow-focus disabled:cursor-not-allowed disabled:opacity-50 ${
        selected
          ? "border-border-primary-solid bg-surface-primary-soft"
          : "border-border-layout-1 bg-surface-layout-1 hover:border-border-primary-soft"
      }`}
    >
      <span
        className={`flex size-4 shrink-0 items-center justify-center rounded-full border-(length:--border-base) ${
          selected ? "border-border-primary-solid" : "border-border-layout-2"
        }`}
      >
        {selected && <span className="size-2 rounded-full bg-content-primary-soft" />}
      </span>
      <Text
        level="label-medium"
        className={selected ? "text-content-primary-soft" : "text-content-layout-2"}
      >
        {label}
      </Text>
    </button>
  );
}

function ClarificationPanel({
  question,
  questions,
  onSubmit,
  onEditQuestion,
  disabled = false,
}: ClarificationPanelProps) {
  const [selectedOptions, setSelectedOptions] = useState<Record<string, string>>({});
  const [customInputs, setCustomInputs] = useState<Record<string, string>>({});
  const [currentIndex, setCurrentIndex] = useState(0);

  const currentQuestion = questions[currentIndex];
  const isLastQuestion = currentIndex === questions.length - 1;
  const selectedValue = currentQuestion ? selectedOptions[currentQuestion.id] : undefined;
  const customValue = currentQuestion ? customInputs[currentQuestion.id] : "";
  const hasAnswer =
    currentQuestion &&
    ((selectedValue === CUSTOM_OPTION_VALUE && customValue.trim().length > 0) ||
      (selectedValue && selectedValue !== CUSTOM_OPTION_VALUE));

  const handleSelect = useCallback((questionId: string, option: string) => {
    setSelectedOptions((prev) => ({ ...prev, [questionId]: option }));
  }, []);

  const handleCustomChange = useCallback((questionId: string, value: string) => {
    setCustomInputs((prev) => ({ ...prev, [questionId]: value }));
  }, []);

  const clearQuestion = useCallback((questionId: string) => {
    setSelectedOptions((prev) => {
      const next = { ...prev };
      delete next[questionId];
      return next;
    });
    setCustomInputs((prev) => {
      const next = { ...prev };
      delete next[questionId];
      return next;
    });
  }, []);

  const handleNext = useCallback(() => {
    const question = questions[currentIndex];
    if (!question) return;

    if (selectedOptions[question.id] === CUSTOM_OPTION_VALUE) {
      const value = customInputs[question.id]?.trim();
      if (!value) return;
    }

    const answers = buildAnswers(selectedOptions, customInputs);
    if (isLastQuestion) {
      onSubmit(answers);
    } else {
      setCurrentIndex((prev) => prev + 1);
    }
  }, [isLastQuestion, onSubmit, selectedOptions, currentIndex, questions, customInputs]);

  const handleSkip = useCallback(() => {
    if (disabled) return;
    const question = questions[currentIndex];
    if (!question) return;
    clearQuestion(question.id);
    if (isLastQuestion) {
      const answers = buildAnswers(selectedOptions, customInputs);
      delete answers[question.id];
      onSubmit(answers);
    } else {
      setCurrentIndex((prev) => prev + 1);
    }
  }, [disabled, isLastQuestion, onSubmit, questions, currentIndex, clearQuestion, selectedOptions, customInputs]);

  if (!currentQuestion) {
    return null;
  }

  // Extract just the question part (before the colon or first bracket)
  const questionText = currentQuestion.question.split(":")[0].trim();

  return (
    <VStack className="gap-5 items-start w-full">
      {/* Calm heading + echoed original question [USE-077, F5]. */}
      <VStack className="gap-1 items-start w-full">
        <Text level="headline-5" className="text-content-layout-1">
          One quick question
        </Text>
        {question && (
          <Text level="body-small" className="text-content-layout-3">
            You asked: <span className="text-content-layout-2">{question}</span>
          </Text>
        )}
      </VStack>

      {/* Question card */}
      <Card className="w-full">
        <Card.Header className="border-b border-border-layout-1">
          <HStack className="justify-between items-center w-full">
            <HStack className="gap-3 items-center">
              <div className="w-8 h-8 rounded-lg bg-surface-primary-soft flex items-center justify-center">
                <Text level="label-medium" className="text-content-primary-soft">
                  {currentIndex + 1}
                </Text>
              </div>
              <Text level="overline" className="text-content-layout-3 uppercase tracking-wider">
                Question {currentIndex + 1} of {questions.length}
              </Text>
            </HStack>
            {/* Progress dots */}
            <HStack className="gap-1.5">
              {questions.map((_, i) => (
                <m.div
                  key={i}
                  className={`w-2 h-2 rounded-full transition-colors ${
                    i < currentIndex
                      ? "bg-content-positive-soft"
                      : i === currentIndex
                        ? "bg-content-primary-soft"
                        : "bg-surface-layout-2"
                  }`}
                  initial={false}
                  animate={{ scale: i === currentIndex ? 1.2 : 1 }}
                />
              ))}
            </HStack>
          </HStack>
        </Card.Header>
        <Card.Content className="p-5">
          <VStack className="gap-5 items-start w-full">
            <Text level="headline-5" className="text-content-layout-1">
              {questionText}
            </Text>

            <div className="w-full">
              <VStack className="gap-3 w-full">
                {/* Selectable radio-cards, not faint radio text [F4]. */}
                <div
                  role="radiogroup"
                  aria-label={questionText}
                  className="grid w-full gap-2"
                >
                  {currentQuestion.options.map((option) => (
                    <ClarificationOption
                      key={option}
                      label={option}
                      selected={selectedValue === option}
                      onSelect={() => handleSelect(currentQuestion.id, option)}
                      disabled={disabled}
                    />
                  ))}
                  <ClarificationOption
                    label="Something else (let me type it)"
                    selected={selectedValue === CUSTOM_OPTION_VALUE}
                    onSelect={() =>
                      handleSelect(currentQuestion.id, CUSTOM_OPTION_VALUE)
                    }
                    disabled={disabled}
                  />
                </div>
                <Show when={selectedValue === CUSTOM_OPTION_VALUE}>
                  <BaseInputText
                    name={`custom-${currentQuestion.id}`}
                    placeholder="Type your own answer..."
                    value={customValue || ""}
                    onChange={(event) =>
                      handleCustomChange(currentQuestion.id, event.target.value)
                    }
                    disabled={disabled}
                  />
                </Show>
              </VStack>
            </div>
          </VStack>
        </Card.Content>
        <Card.Footer className="border-t border-border-layout-1">
          <HStack className="justify-between items-center w-full">
            {onEditQuestion ? (
              <Button
                onClick={onEditQuestion}
                variant="primary"
                modifier="ghost"
                label="Edit question"
                icon="arrow-left"
                iconPosition="left"
                disabled={disabled}
              />
            ) : (
              <span />
            )}
            <HStack className="gap-2 items-center">
              <Button
                onClick={handleSkip}
                variant="primary"
                modifier="ghost"
                label="Skip"
                icon="arrow-right"
                iconPosition="right"
                disabled={disabled}
              />
              {/* Honest name: the last-question button SUBMITS the answers and
                  resumes the ask — it must say the outcome, not read like a
                  mere step advance ("Continue"). [USE-017 obvious, honest
                  names] */}
              <Button
                onClick={handleNext}
                disabled={disabled || !hasAnswer}
                variant="rising"
                modifier="solid"
                label={isLastQuestion ? "Get answer" : "Next"}
                icon={isLastQuestion ? "sparkles" : "arrow-right"}
                iconPosition="right"
              />
            </HStack>
          </HStack>
        </Card.Footer>
      </Card>
    </VStack>
  );
}
