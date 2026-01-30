import { createFileRoute } from "@tanstack/react-router";
import { Text } from "@rs/ui-new/text";
import { ReadysetPanel, TargetLockNotice } from "../components";
import { useTarget } from "../hooks/useTarget";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";

export const Route = createFileRoute("/readyset")({
  component: ReadysetPage,
});

function ReadysetPage() {
  const { target: selectedTarget } = useTarget();
  const passwordLock = useTargetPasswordLock(selectedTarget);

  return (
    <div className="space-y-6 w-full">
      <div className="space-y-2">
        <Text as="h1" level="headline-3" className="text-content-layout-1">
          Readyset Cache Testing
        </Text>
        <Text level="body-medium" className="text-content-layout-2">
          Test query cacheability using local Docker containers. This spins up a test database with
          your schema and a Readyset instance to verify which queries can be cached.
        </Text>
      </div>

      {passwordLock.isLocked && (
        <TargetLockNotice
          message={passwordLock.message}
          requirements={passwordLock.missingTargetRequirements}
          keyringAvailable={passwordLock.keyringAvailable}
        />
      )}

      {!passwordLock.isLocked && (
        <ReadysetPanel
          target={selectedTarget}
          disabled={false}
        />
      )}
    </div>
  );
}
