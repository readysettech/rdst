import { useEffect, useSyncExternalStore } from 'react';

export type TargetLockReason =
  | 'ask'
  | 'analyze'
  | 'schema'
  | 'top'
  | 'scan'
  | 'benchmark'
  | 'readyset'
  | 'cache-deploy'
  | 'cache-run';

interface TargetSwitchLockState {
  isLocked: boolean;
  message: string;
}

const reasonMessage: Record<TargetLockReason, string> = {
  ask: 'Target switching is disabled while Ask is in progress.',
  analyze: 'Target switching is disabled while Analyze is in progress.',
  schema: 'Target switching is disabled while Schema operations are in progress.',
  top: 'Target switching is disabled while Top Queries is in progress.',
  scan: 'Target switching is disabled while Scan is in progress.',
  benchmark: 'Target switching is disabled while Benchmark is in progress.',
  readyset: 'Target switching is disabled while Readyset operations are in progress.',
  'cache-deploy': 'Target switching is disabled while cache deployment is in progress.',
  'cache-run': 'Target switching is disabled while cache performance comparison is in progress.',
};

const lockCounts = new Map<TargetLockReason, number>();
const listeners = new Set<() => void>();

let snapshot: TargetSwitchLockState = {
  isLocked: false,
  message: '',
};

function getActiveReasons(): TargetLockReason[] {
  return [...lockCounts.entries()]
    .filter(([, count]) => count > 0)
    .map(([reason]) => reason);
}

function computeSnapshot(): TargetSwitchLockState {
  const activeReasons = getActiveReasons();

  if (activeReasons.length === 0) {
    return {
      isLocked: false,
      message: '',
    };
  }

  if (activeReasons.length === 1) {
    return {
      isLocked: true,
      message: reasonMessage[activeReasons[0]],
    };
  }

  return {
    isLocked: true,
    message: 'Target switching is disabled while multiple operations are in progress.',
  };
}

function emitChange() {
  snapshot = computeSnapshot();
  for (const listener of listeners) {
    listener();
  }
}

function acquireLock(reason: TargetLockReason) {
  const currentCount = lockCounts.get(reason) ?? 0;
  lockCounts.set(reason, currentCount + 1);
  emitChange();
}

function releaseLock(reason: TargetLockReason) {
  const currentCount = lockCounts.get(reason) ?? 0;
  if (currentCount <= 1) {
    lockCounts.delete(reason);
  } else {
    lockCounts.set(reason, currentCount - 1);
  }
  emitChange();
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function getSnapshot() {
  return snapshot;
}

function getServerSnapshot(): TargetSwitchLockState {
  return {
    isLocked: false,
    message: '',
  };
}

export function useTargetSwitchLockState(): TargetSwitchLockState {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

export function useTargetSwitchLock(reason: TargetLockReason, active: boolean): void {
  useEffect(() => {
    if (!active) {
      return;
    }

    acquireLock(reason);
    return () => {
      releaseLock(reason);
    };
  }, [reason, active]);
}

export function __resetTargetSwitchLockForTests(): void {
  lockCounts.clear();
  snapshot = {
    isLocked: false,
    message: '',
  };
  for (const listener of listeners) {
    listener();
  }
}
