import { createFileRoute } from "@tanstack/react-router";
import { Text } from "@rs/ui-new/text";
import { Icon } from "@rs/ui-new/icon";
import { HStack, VStack } from "@rs/ui-new/stack";
import { m } from "@rs/ui-new/motion";
import { AskPanel } from "../components/AskPanel";
import { TargetLockNotice } from "../components/TargetLockNotice";
import { useTarget } from "../hooks/useTarget";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";

export const Route = createFileRoute("/ask")({
  component: AskPage,
});

function AskPage() {
  const { target } = useTarget();
  const passwordLock = useTargetPasswordLock(target);

  return (
    <div className="space-y-6 w-full">
      {/* Header */}
      <m.div
        className="space-y-4"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <HStack className="gap-4 items-center">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-surface-primary-soft to-surface-rising-soft flex items-center justify-center">
            <Icon name="sparkles" label="Ask AI" className="w-6 h-6 text-content-primary-soft" />
          </div>
          <VStack className="gap-1 items-start">
            <Text as="h1" level="headline-3" className="text-content-layout-1">
              Ask in Plain English
            </Text>
            <Text level="body-small" className="text-content-layout-3">
              Ask questions about your data in natural language and get SQL results
            </Text>
          </VStack>
        </HStack>
      </m.div>

      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      <AskPanel target={target} disabled={passwordLock.isLocked} />
    </div>
  );
}
