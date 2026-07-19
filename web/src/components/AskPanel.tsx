import { useState, useCallback } from "react";
import { Button } from "@rs/ui-new/button";
import { Show } from "@rs/ui-new/show";
import { Text } from "@rs/ui-new/text";
import { VStack, HStack } from "@rs/ui-new/stack";
import { Icon } from "@rs/ui-new/icon";
import { Spinner } from "@rs/ui-new/spinner";
import { BaseInputTextarea } from "@rs/ui-new/base-input-textarea";
import { BaseInputRadioGroup } from "@rs/ui-new/base-input-radio-group";
import { BaseInputText } from "@rs/ui-new/base-input-text";
import { Card } from "@rs/ui-new/card";
import { Tag } from "@rs/ui-new/tag";
import { CopyButton } from "@rs/ui-new/copy-button";
import { m } from "@rs/ui-new/motion";
import { useAsk, AskClarificationQuestion, AskStatusEvent, AskSchemaLoadedEvent } from "../lib/ask";
import { createCsvFilename, downloadCsv, toCsv } from "../lib/csv";
import { SQLDisplay } from "./SQLDisplay";

interface AskPanelProps {
  target?: string | null;
  disabled?: boolean;
}

// Example questions to help users get started
const exampleQuestions = [
  "Show me the top 10 customers by revenue",
  "What are the most popular products this month?",
  "Find all orders placed in the last 7 days",
  "Which users haven't logged in for 30 days?",
];

export function AskPanel({ target, disabled = false }: AskPanelProps) {
  const [question, setQuestion] = useState("");
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

  const handleSubmit = useCallback(async () => {
    if (disabled) return;
    if (!question.trim()) return;
    await ask({
      question: question.trim(),
      target: target || undefined,
    });
  }, [disabled, question, target, ask]);

  const handleClarificationSubmit = useCallback(
    async (answers: Record<string, string>) => {
      if (disabled) return;
      await resumeWithAnswers(answers);
    },
    [disabled, resumeWithAnswers],
  );

  const handleNewQuestion = useCallback(() => {
    reset();
    setQuestion("");
  }, [reset]);

  const handleExampleClick = useCallback((example: string) => {
    if (disabled) return;
    setQuestion(example);
  }, [disabled]);

  const isLoading = state === "loading" || state === "generating";

  // Show the SQL that actually ran (post-validation, from the result event),
  // not the pre-validation generated SQL. The backend injects a LIMIT during
  // validation but does not emit an explicit warning yet (T15) — derive it by
  // comparing: a LIMIT present in the executed SQL but not the generated one
  // means one was added. [QW13]
  const executedSql = result?.sql ?? sqlGenerated?.sql ?? "";
  const limitAdded = Boolean(
    result?.sql &&
      sqlGenerated?.sql &&
      /\blimit\b/i.test(result.sql) &&
      !/\blimit\b/i.test(sqlGenerated.sql),
  );
  const runsAgainst = target ?? "demo";

  return (
    <VStack className="gap-6 w-full">
      {/* Question Input */}
      {state === "idle" && (
        <m.div
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
        >
          <Card className="w-full overflow-hidden">
            <Card.Content className="p-0">
              {/* Input area */}
              <div className="p-5">
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
                <HStack className="gap-1.5 items-center mt-3">
                  <Icon name="user-shield" label="Read-only" className="w-3.5 h-3.5 text-content-info-soft shrink-0" />
                  <Text level="caption" className="text-content-layout-3">
                    Runs a read-only query against{" "}
                    <span className="font-medium text-content-layout-2">{runsAgainst}</span>
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

              {/* Example questions */}
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
                      className="px-3 py-1.5 rounded-lg bg-surface-layout-1 border border-border-layout-1 text-content-layout-2 text-label-small hover:border-border-primary-soft hover:text-content-primary-soft transition-colors"
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
          <LoadingState status={status} schemaLoaded={schemaLoaded} />
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
            questions={clarification.questions}
            onSubmit={handleClarificationSubmit}
            disabled={disabled}
          />
        </m.div>
      )}

      {/* Results */}
      {(state === "complete" || sqlGenerated) && (
        <m.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.4 }}
          className="overflow-x max-w-5xl"
        >
          <VStack className="gap-6 items-start w-full">
            {/* Generated SQL */}
            {sqlGenerated && (
              <m.div
                className="w-full"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3 }}
              >
                <SQLResultCard sql={executedSql} explanation={sqlGenerated.explanation} limitAdded={limitAdded} />
              </m.div>
            )}

            {/* Query Results */}
            {result && (
              <m.div
                className="w-full"
                initial={{ opacity: 0, y: 20 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.3, delay: 0.1 }}
              >
                <ResultsTable result={result} />
              </m.div>
            )}

            {/* Actions */}
            {result && (
              <m.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }}>
                <Button
                  onClick={handleNewQuestion}
                  variant="primary"
                  modifier="outline"
                  label="Ask another question"
                  icon="add"
                  iconPosition="left"
                />
              </m.div>
            )}
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
          <ErrorState error={error} onRetry={handleNewQuestion} />
        </m.div>
      )}
    </VStack>
  );
}

// Loading State Component
function LoadingState({
  status,
  schemaLoaded,
}: {
  status?: AskStatusEvent;
  schemaLoaded?: AskSchemaLoadedEvent;
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
      {/* Stage indicators */}
      <div className="flex items-center justify-center mb-10">
        {stages.map((stage, index) => {
          const isComplete = index < currentStageIndex;
          const isCurrent = index === currentStageIndex;
          const isPending = index > currentStageIndex;

          return (
            <div key={stage.id} className="flex items-center">
              <m.div
                className={`
                  relative w-12 h-12 rounded-xl flex items-center justify-center transition-all
                  ${isComplete ? "bg-surface-positive-soft" : ""}
                  ${isCurrent ? "bg-surface-primary-soft ring-2 ring-border-primary-soft" : ""}
                  ${isPending ? "bg-surface-layout-1 border border-border-layout-1" : ""}
                `}
                initial={false}
                animate={isCurrent ? { scale: [1, 1.05, 1] } : { scale: 1 }}
                transition={{ duration: 1.5, repeat: isCurrent ? Infinity : 0, ease: "easeInOut" }}
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
              {index < stages.length - 1 && (
                <div className="w-12 mx-1.5 h-0.5 rounded-full overflow-hidden bg-surface-layout-1">
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
          <Text level="headline-4" className="text-content-layout-1 mb-2">
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
              from {schemaLoaded.source}
            </Text>
          </HStack>
        </m.div>
      )}
    </div>
  );
}

// SQL Result Card Component
function SQLResultCard({
  sql,
  explanation,
  limitAdded,
}: {
  sql: string;
  explanation?: string | null;
  limitAdded?: boolean;
}) {
  return (
    <Card className="w-full overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <HStack className="justify-between items-center w-full">
          <HStack className="gap-3 items-center">
            <div className="w-8 h-8 rounded-lg bg-surface-positive-soft flex items-center justify-center">
              <Icon
                name="tick-double"
                label="Generated"
                className="w-4 h-4 text-content-positive-soft"
              />
            </div>
            <Card.Title>Generated SQL</Card.Title>
          </HStack>
          <CopyButton text={sql} />
        </HStack>
      </Card.Header>
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
      {explanation && (
        <div className="p-4 border-t border-border-layout-1">
          <HStack className="gap-2 items-start">
            <Icon
              name="info"
              label="Explanation"
              className="w-4 h-4 text-content-layout-3 mt-0.5 shrink-0"
            />
            <Text level="body-small" className="text-content-layout-2 leading-relaxed">
              {explanation}
            </Text>
          </HStack>
        </div>
      )}
    </Card>
  );
}

// Results Table Component
function ResultsTable({
  result,
}: {
  result: { columns: string[]; rows: any[][]; row_count: number; execution_time_ms: number };
}) {
  const handleDownloadCsv = useCallback(() => {
    const csv = toCsv(result.columns, result.rows);
    downloadCsv(csv, createCsvFilename());
  }, [result]);

  return (
    <Card className="w-full overflow-hidden">
      <Card.Header className="border-b border-border-layout-1">
        <HStack className="justify-between items-center w-full">
          <HStack className="gap-3 items-center">
            <div className="w-8 h-8 rounded-lg bg-surface-info-soft flex items-center justify-center">
              <Icon name="dashboard" label="Results" className="w-4 h-4 text-content-info-soft" />
            </div>
            <Card.Title>Query Results</Card.Title>
          </HStack>
          <HStack className="gap-3">
            <Button
              onClick={handleDownloadCsv}
              variant="primary"
              modifier="outline"
              size="small"
              label="Download CSV"
              icon="arrow-down"
              iconPosition="left"
            />
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
          </HStack>
        </HStack>
      </Card.Header>
      <Card.Content className="p-0 overflow-hidden">
        {result.rows.length > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="bg-surface-layout-2 border-b border-border-layout-1">
                  {result.columns.map((col, i) => (
                    <th
                      key={i}
                      className="text-left px-4 py-3 text-content-layout-2 text-label-small font-medium whitespace-nowrap"
                    >
                      {col}
                    </th>
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
          <div className="p-8 text-center">
            <Icon
              name="empty"
              label="No results"
              className="w-12 h-12 text-content-layout-3 mx-auto mb-3"
            />
            <Text level="body-medium" className="text-content-layout-3">
              No results returned
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
}: {
  error: { message: string; phase?: string | null };
  onRetry: () => void;
}) {
  const isAuthenticationError = /trial access|api key|authentication|unauthorized|\b401\b/i.test(
    error.message,
  );
  const title = isAuthenticationError
    ? 'AI service authentication failed'
    : error.phase === 'generate'
      ? "Couldn't generate SQL"
      : 'Request failed';
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
          <Button
            onClick={onRetry}
            variant="primary"
            modifier="outline"
            size="small"
            label="Try again"
            icon="arrow-left"
            iconPosition="left"
          />
        </VStack>
      </HStack>
    </div>
  );
}

// Clarification Panel Component
interface ClarificationPanelProps {
  questions: AskClarificationQuestion[];
  onSubmit: (answers: Record<string, string>) => void;
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

function ClarificationPanel({
  questions,
  onSubmit,
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
      {/* Info banner */}
      <m.div
        className="w-full rounded-xl border border-border-warning-soft bg-surface-warning-soft/50 p-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
      >
        <HStack className="gap-3 items-center">
          <div className="w-8 h-8 rounded-lg bg-surface-warning-soft flex items-center justify-center shrink-0">
            <Icon
              name="alert"
              label="Clarification needed"
              className="w-4 h-4 text-content-warning-soft"
            />
          </div>
          <Text level="body-small" className="text-content-warning-soft">
            I need some clarification to better understand your question.
          </Text>
        </HStack>
      </m.div>

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
                <BaseInputRadioGroup
                  options={[
                    ...currentQuestion.options.map((option) => ({
                      value: option,
                      label: option,
                    })),
                    {
                      value: CUSTOM_OPTION_VALUE,
                      label: "Something else (let me type it)",
                    },
                  ]}
                  value={selectedValue || ""}
                  onValueChange={(value) => handleSelect(currentQuestion.id, value)}
                />
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
          <HStack className="justify-end items-center w-full">
            <Button
              onClick={handleSkip}
              variant="primary"
              modifier="ghost"
              label="Skip question"
              icon="arrow-right"
              iconPosition="right"
              disabled={disabled}
            />
            <Button
              onClick={handleNext}
              disabled={disabled || !hasAnswer}
              variant="rising"
              modifier="solid"
              label={isLastQuestion ? "Ask" : "Continue"}
              icon={isLastQuestion ? "sparkles" : "arrow-right"}
              iconPosition="right"
            />
          </HStack>
        </Card.Footer>
      </Card>
    </VStack>
  );
}
