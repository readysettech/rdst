import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m } from "@rs/ui-new/motion";
import { QueryEditor, QueryHistory, TargetLockNotice } from "../components";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";
import { useTarget } from "../hooks/useTarget";

export const Route = createFileRoute("/analyze")({
  component: AnalyzePage,
});

function AnalyzePage() {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [fast, setFast] = useState(false);
  const { target: selectedTarget } = useTarget();
  const passwordLock = useTargetPasswordLock(selectedTarget);
  const { queries, addQuery } = useQueryRegistry();

  const handleAnalyze = useCallback(() => {
    if (passwordLock.isLocked) return;
    if (!query.trim()) return;
    addQuery(query, selectedTarget || undefined);
    navigate({
      to: "/results",
      search: {
        query: query.trim(),
        target: selectedTarget || undefined,
        fast: fast || undefined,
      },
    });
  }, [passwordLock.isLocked, query, selectedTarget, fast, addQuery, navigate]);

  const handleSelectHistory = useCallback((selectedQuery: string) => {
    setQuery(selectedQuery);
  }, []);

  return (
    <div className="space-y-8 w-full">
      {/* Hero Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-info-soft flex items-center justify-center">
            <Icon name="querypilot" label="Analyze Query" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Analyze Query
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Paste SQL to see how fast it runs and how to speed it up.
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {/* Query Editor */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.1 }}
      >
        {passwordLock.isLocked && (
          <div className="mb-4">
            <TargetLockNotice
              message={passwordLock.message}
              requirements={passwordLock.missingTargetRequirements}
              keyringAvailable={passwordLock.keyringAvailable}
            />
          </div>
        )}
        <QueryEditor
          value={query}
          onChange={setQuery}
          onAnalyze={handleAnalyze}
          disabled={passwordLock.isLocked}
          target={selectedTarget}
          fast={fast}
          onFastChange={setFast}
        />
      </m.div>

      {/* Query History */}
      <m.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, delay: 0.2 }}
      >
        <QueryHistory queries={queries} onSelect={handleSelectHistory} />
      </m.div>
    </div>
  );
}
