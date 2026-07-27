import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import type { EnvRequirement } from "../lib/api";
import { EnvSecretsDialog } from "./EnvSecretsDialog";
import { RoutableNotice } from "./RoutableNotice";

interface TargetLockNoticeProps {
  message: string;
  requirements: EnvRequirement[];
  keyringAvailable: boolean;
  onUnlocked?: () => void;
}

/**
 * The password-needed return trip (configure-and-identity step 5): an inline,
 * non-blocking notice that names the offending connection and routes the fix
 * one obvious move away. Primary action deep-links to that connection's edit
 * form carrying a `returnTo`, so the user lands back on the feature after
 * saving; the secondary keeps the quick in-place "set the secret" path.
 * Rendered through the shared `RoutableNotice{password-needed}` (which builds
 * on the B7/T24 `InlineNotice`) — no second error surface.
 * [USE-100, USE-099, USE-021, USE-077]
 */
export function TargetLockNotice({
  message,
  requirements,
  keyringAvailable,
  onUnlocked,
}: TargetLockNoticeProps) {
  const queryClient = useQueryClient();
  const [isDialogOpen, setDialogOpen] = useState(false);

  const canSetInWeb = requirements.length > 0;
  const lockedTarget =
    requirements.find((r) => r.kind === "target_password")?.target ??
    requirements[0]?.target ??
    null;

  const handleSuccess = () => {
    queryClient.invalidateQueries({ queryKey: ["status"] });
    queryClient.invalidateQueries({ queryKey: ["init-status"] });
    queryClient.invalidateQueries({ queryKey: ["env-requirements"] });
    onUnlocked?.();
  };

  return (
    <>
      <RoutableNotice
        kind="password-needed"
        target={lockedTarget}
        message={message}
        onPrimaryAction={canSetInWeb ? () => setDialogOpen(true) : undefined}
        primaryActionLabel={canSetInWeb ? "Set password" : undefined}
      />

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
