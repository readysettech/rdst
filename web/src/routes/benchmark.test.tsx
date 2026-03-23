import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { BenchmarkPage } from "./benchmark";
import { useBenchmark } from "../lib/sse";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: unknown) => options,
}));

vi.mock("../lib/useQueryRegistry", () => ({
  useQueryRegistry: vi.fn(),
}));

vi.mock("../hooks/useTarget", () => ({
  useTarget: () => ({ target: "prod" }),
}));

vi.mock("../lib/sse", () => ({
  useBenchmark: vi.fn(),
}));

vi.mock("../lib/useTargetPasswordLock", () => ({
  useTargetPasswordLock: vi.fn(),
}));

vi.mock("../components", () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}));

function setup(lockActive: boolean) {
  vi.mocked(useQueryRegistry).mockReturnValue({
    queries: [
      {
        sql: "select 1",
        hash: "abc12345",
        tag: "Q1",
        last_analyzed: "2025-01-01",
        target: "prod",
        frequency: 1,
        source: "web",
      },
    ],
    isLoading: false,
    addQuery: vi.fn(),
    addMutation: { mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, isIdle: true, isSuccess: false, data: undefined, error: null, reset: vi.fn(), status: 'idle', variables: undefined, failureCount: 0, failureReason: null, submittedAt: 0, context: undefined, isPaused: false } as any,
    removeQuery: vi.fn(),
    updateTag: vi.fn(),
    isFetching: false,
    total: 1,
    limit: 50,
    offset: 0,
    setLimit: vi.fn(),
    setOffset: vi.fn(),
    nextPage: vi.fn(),
    prevPage: vi.fn(),
    resetPagination: vi.fn(),
  });

  vi.mocked(useBenchmark).mockReturnValue({
    start: vi.fn(),
    stop: vi.fn(),
    state: "idle",
    progress: undefined,
    error: undefined,
    reset: vi.fn(),
  });

  vi.mocked(useTargetPasswordLock).mockReturnValue({
    isLocked: lockActive,
    targetName: "prod",
    message: "Target 'prod' is locked.",
    missingTargetRequirements: [],
    keyringAvailable: true,
  });

  render(<BenchmarkPage />);

  fireEvent.click(screen.getAllByText("Q1")[0]);
}

describe("BenchmarkPage password lock", () => {
  it("allows start when unlocked and query is selected", () => {
    setup(false);
    const startButton = screen.getAllByRole("button", { name: /Start Benchmark/i })[0];
    expect((startButton as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps start disabled when target is password-locked", () => {
    setup(true);
    const startButton = screen.getAllByRole("button", { name: /Start Benchmark/i })[0];
    expect((startButton as HTMLButtonElement).disabled).toBe(true);
  });
});
