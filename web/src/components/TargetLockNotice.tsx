import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@rs/ui-new/button";
import { Icon } from "@rs/ui-new/icon";
import { Text } from "@rs/ui-new/text";
import { HStack, VStack } from "@rs/ui-new/stack";

import type { EnvRequirement } from "../lib/api";
import { EnvSecretsDialog } from "./EnvSecretsDialog";

interface TargetLockNoticeProps {
  message: string;
  requirements: EnvRequirement[];
  keyringAvailable: boolean;
  onUnlocked?: () => void;
}

export function TargetLockNotice({
  message,
  requirements,
  keyringAvailable,
  onUnlocked,
}: TargetLockNoticeProps) {
  const queryClient = useQueryClient();
  const [isDialogOpen, setDialogOpen] = useState(false);

  const canSetInWeb = requirements.length > 0;

  const handleSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ["status"] });
    queryClient.invalidateQueries({ queryKey: ["init-status"] });
    queryClient.invalidateQueries({ queryKey: ["env-requirements"] });
    onUnlocked?.();
  };

  return (
    <>
      <div className="rounded-xl border border-border-warning-soft bg-surface-warning-soft/10 p-4">
        <HStack className="items-start justify-between gap-4">
          <HStack className="items-start gap-3">
            <Icon
              name="alert"
              label="Target locked"
              className="mt-0.5 w-4 h-4 text-content-warning-soft"
            />
            <VStack className="items-start gap-1">
              <Text level="label-small" className="text-content-warning-soft">
                Target operations are locked
              </Text>
              <Text level="body-small" className="text-content-layout-2">
                {message}
              </Text>
            </VStack>
          </HStack>
          {canSetInWeb && (
            <Button
              variant="primary"
              modifier="outline"
              icon="key"
              iconPosition="left"
              label="Set"
              onClick={() => setDialogOpen(true)}
            />
          )}
        </HStack>
      </div>

      <EnvSecretsDialog
        isOpen={isDialogOpen}
        onClose={() => setDialogOpen(false)}
        requirements={requirements}
        keyringAvailable={keyringAvailable}
        onSuccess={handleSuccess}
      />
    </>
  );
}
