import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import {
  fetchEnvRequirements,
  fetchStatus,
  type EnvRequirement,
} from "./api";

export interface TargetPasswordLockState {
  isLocked: boolean;
  targetName: string | null;
  message: string;
  missingTargetRequirements: EnvRequirement[];
  keyringAvailable: boolean;
}

export function useTargetPasswordLock(
  selectedTarget?: string | null,
): TargetPasswordLockState {
  const { data: status } = useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    staleTime: 30000,
    retry: 1,
  });

  const { data: envRequirements } = useQuery({
    queryKey: ["env-requirements"],
    queryFn: fetchEnvRequirements,
    staleTime: 30000,
    retry: 1,
  });

  return useMemo(() => {
    const targets = status?.targets ?? [];
    const targetName =
      selectedTarget?.trim() ||
      status?.default_target ||
      targets[0]?.name ||
      null;

    const targetInfo = targetName
      ? targets.find((item) => item.name === targetName)
      : undefined;

    const missingTargetRequirements = (envRequirements?.requirements ?? []).filter(
      (item) =>
        item.kind === "target_password" &&
        !item.satisfied &&
        (item.target === targetName || item.target === null),
    );

    const isLocked = Boolean(targetInfo && !targetInfo.has_password);

    let message = "";
    if (isLocked && targetName) {
      if (missingTargetRequirements.length > 0) {
        const vars = missingTargetRequirements
          .map((item) => item.accepted_names[0])
          .filter(Boolean)
          .join(", ");
        message = `Target '${targetName}' is locked until DB password env vars are set: ${vars}.`;
      } else {
        message = `Target '${targetName}' is locked because a DB password is not configured.`;
      }
    }

    return {
      isLocked,
      targetName,
      message,
      missingTargetRequirements,
      keyringAvailable: envRequirements?.keyring_available ?? false,
    };
  }, [status, envRequirements, selectedTarget]);
}
