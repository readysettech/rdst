import { useEffect, useRef, useState } from 'react';
import { Dropdown } from '@rs/ui-new/dropdown';
import { Icon } from '@rs/ui-new/icon';
import { Text } from '@rs/ui-new/text';
import { Skeleton } from '@rs/ui-new/skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@rs/ui-new/tooltip';
import { type TargetInfo } from '../lib/api';
import { useSystemStatus } from '../lib/useSystemStatus';
import { useTargetSwitchLockState } from '../lib/targetSwitchLock';

interface TargetDropdownProps {
  selectedTarget: string | null;
  onSelectTarget: (target: string | null) => void;
}

export function TargetDropdown({ selectedTarget, onSelectTarget }: TargetDropdownProps) {
  const [open, setOpen] = useState(false);
  const { isLocked, message: lockMessage } = useTargetSwitchLockState();

  const { data: status, isLoading, error } = useSystemStatus();

  const targets = status?.targets || [];
  const normalizedSelectedTarget = selectedTarget?.trim() || null;
  const isSelectedValid = normalizedSelectedTarget
    ? targets.some((target) => target.name === normalizedSelectedTarget)
    : false;
  const fallbackTarget = (status?.default_target && targets.some((target) => target.name === status.default_target))
    ? status.default_target
    : (targets.length > 0 ? targets[0].name : null);
  const currentTarget = isSelectedValid ? normalizedSelectedTarget : fallbackTarget;

  // Track the last invalid→fallback correction we applied so status refetches
  // do not loop, while still allowing new invalid parent selections to resync.
  const lastCorrectionRef = useRef<string | null>(null);
  const correctionKey = !isLoading && status && currentTarget !== normalizedSelectedTarget
    ? `${normalizedSelectedTarget ?? '<none>'}=>${currentTarget ?? '<none>'}`
    : null;

  useEffect(() => {
    if (!correctionKey) {
      lastCorrectionRef.current = null;
      return;
    }

    if (lastCorrectionRef.current === correctionKey) {
      return;
    }

    lastCorrectionRef.current = correctionKey;
    onSelectTarget(currentTarget);
  }, [correctionKey, currentTarget, onSelectTarget]);

  useEffect(() => {
    if (isLocked) {
      setOpen(false);
    }
  }, [isLocked]);

  // Lock still forces the menu visually closed immediately while the effect above
  // clears the internal open state so it stays closed after unlocking.
  const effectiveOpen = open && !isLocked;

  if (isLoading) {
    return (
      <div className="p-2 pr-3 h-14 w-full flex items-center gap-2">
        <Skeleton className="h-10 w-10 rounded-md" />
        <Skeleton className="h-4 w-24" />
      </div>
    );
  }

  if (error || !status) {
    return (
      <div className="flex p-2 pr-3 h-14 w-full items-center gap-2">
        <Icon name="alert" label="Error" className="text-content-negative-soft" />
        <Text level="label-medium" className="text-content-negative-soft">
          Connection Error
        </Text>
      </div>
    );
  }

  const currentTargetInfo = targets.find((t) => t.name === currentTarget);
  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen && isLocked) return;
    setOpen(nextOpen);
  };

  // A real <button> so the one always-visible config control is keyboard
  // focusable and Enter/Space-activatable — the Dropdown.Trigger→<button> fix
  // (configure-and-identity step 2 / app-chrome HIGH). [USE-030, USE-018]
  const trigger = (
    <button
      type="button"
      aria-label={`Switch database — current: ${currentTarget ?? 'none'}`}
      aria-disabled={isLocked}
      title={isLocked ? lockMessage : undefined}
      className={`flex justify-between items-center text-content-layout-1 p-2 pr-3 h-14 w-full rounded-lg ${
        isLocked
          ? 'cursor-not-allowed opacity-60 bg-surface-layout-2/50'
          : 'cursor-pointer hover:bg-surface-primary-soft-hover focus-visible:shadow-focus focus-visible:outline-none'
      }`}
    >
      <div className="flex gap-2 items-center">
        <div className="w-8 h-8 rounded-md bg-surface-layout-2 flex items-center justify-center">
          <Icon name="database" label="Database" size="small" />
        </div>
        <Text level="label-medium">{currentTarget}</Text>
      </div>
      <div className="flex gap-1 items-center">
        {currentTargetInfo && !currentTargetInfo.has_password && (
          <Icon name="alert" label="No password" size="small" className="text-content-warning-soft" />
        )}
        <Icon name="chevron-down" label="Target Dropdown" />
      </div>
    </button>
  );

  if (targets.length === 0) {
    return (
      <div className="flex p-2 pr-3 h-14 w-full items-center gap-2">
        <Icon name="alert" label="No targets" className="text-content-warning-soft" />
        <Text level="label-medium" className="text-content-warning-soft">
          No Targets
        </Text>
      </div>
    );
  }

  return (
    <Dropdown open={effectiveOpen} onOpenChange={handleOpenChange}>
      {isLocked ? (
        <TooltipProvider delayDuration={0}>
          <Tooltip>
            <TooltipTrigger asChild>
              {trigger}
            </TooltipTrigger>
            <TooltipContent label={lockMessage} />
          </Tooltip>
        </TooltipProvider>
      ) : (
        <Dropdown.Trigger asChild>{trigger}</Dropdown.Trigger>
      )}
      <Dropdown.Content align="start" className="min-w-64">
        {targets.map((target: TargetInfo) => (
          <Dropdown.ItemWithChildren
            key={target.name}
            onClick={() => {
              if (isLocked) {
                return;
              }
              onSelectTarget(target.name);
              setOpen(false);
            }}
          >
            <div className="flex gap-2 items-center">
              <div className="w-8 h-8 rounded-md bg-surface-layout-2 flex items-center justify-center">
                <Icon name="database" label="Database" size="small" />
              </div>
              <Text level="label-medium">{target.name}</Text>
              {target.is_default && (
                <Text level="label-small" className="text-content-layout-3">(default)</Text>
              )}
            </div>
            {target.has_password ? (
              <Icon
                name="tick-double"
                label="Password configured"
                className={target.name === currentTarget ? 'text-content-positive-soft' : 'text-content-layout-3'}
              />
            ) : (
              <Icon
                name="alert"
                label="No password configured"
                className="text-content-warning-soft"
              />
            )}
          </Dropdown.ItemWithChildren>
        ))}
      </Dropdown.Content>
    </Dropdown>
  );
}
