import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TopPage } from "./top";
import { useTop } from "../lib/useTop";
import { useQueryRegistry } from "../lib/useQueryRegistry";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";

vi.mock("@tanstack/react-router", () => ({
  createFileRoute: () => (options: unknown) => options,
  useNavigate: () => vi.fn(),
}));

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

vi.mock("../lib/useQueryRegistry", () => ({
  useQueryRegistry: vi.fn(),
}));

vi.mock("../hooks/useTarget", () => ({
  useTarget: () => ({ target: "prod" }),
}));

vi.mock("../lib/useTop", () => ({
  useTop: vi.fn(),
}));

vi.mock("../lib/useTargetPasswordLock", () => ({
  useTargetPasswordLock: vi.fn(),
}));

vi.mock("../components", () => ({
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}));

vi.mock("../components/top", () => ({
  TopFilters: () => <div data-testid="top-filters" />,
  TopHeader: () => <div data-testid="top-header" />,
  TopQueryTable: () => <div data-testid="top-query-table" />,
  TopStatus: () => <div data-testid="top-status" />,
  ParameterDialog: () => null,
  hasParameters: () => false,
}));

function setupMocks(overrides: Partial<ReturnType<typeof useTop>> = {}) {
  vi.mocked(useQueryRegistry).mockReturnValue({
    queries: [],
    isLoading: false,
    addQuery: vi.fn(),
    addMutation: {
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      isIdle: true,
      isSuccess: false,
      data: undefined,
      error: null,
      reset: vi.fn(),
      status: "idle",
      variables: undefined,
      failureCount: 0,
      failureReason: null,
      submittedAt: 0,
      context: undefined,
      isPaused: false,
    } as any,
    removeQuery: vi.fn(),
    updateTag: vi.fn(),
  });

  vi.mocked(useTargetPasswordLock).mockReturnValue({
    isLocked: false,
    targetName: "prod",
    message: "",
    missingTargetRequirements: [],
    keyringAvailable: true,
  });

  vi.mocked(useTop).mockReturnValue({
    getTop: vi.fn(),
    startRealtime: vi.fn(),
    stopRealtime: vi.fn(),
    reset: vi.fn(),
    state: "idle",
    queries: [],
    connectionInfo: null,
    sourceFallback: null,
    dbLimitWarning: null,
    runtimeSeconds: 0,
    totalTracked: 0,
    newlySaved: 0,
    savedHashes: new Set(),
    error: null,
    ...overrides,
  });
}

describe("TopPage db limit warning", () => {
  it("does not show warning when dbLimitWarning is null", () => {
    setupMocks({ dbLimitWarning: null });
    render(<TopPage />);

    expect(screen.queryByText(/Low Database Query Size Limit/i)).toBeNull();
  });

  it("shows warning when dbLimitWarning is present", () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: "track_activity_query_size",
        db_engine: "postgresql",
      },
    });
    render(<TopPage />);

    expect(screen.getByText(/Low Database Query Size Limit/i)).toBeTruthy();
    expect(screen.getAllByText(/track_activity_query_size/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/1\s*KB/i)).toBeTruthy();
    expect(screen.getByText(/4\s*KB/i)).toBeTruthy();
  });

  it("shows ALTER SYSTEM command for PostgreSQL", () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: "track_activity_query_size",
        db_engine: "postgresql",
      },
    });
    render(<TopPage />);

    expect(
      screen.getAllByText(/ALTER SYSTEM SET track_activity_query_size/i).length
    ).toBeGreaterThan(0);
  });

  it("shows SET GLOBAL command for MySQL", () => {
    setupMocks({
      dbLimitWarning: {
        db_limit_bytes: 1024,
        recommended_bytes: 4096,
        setting_name: "performance_schema_max_digest_length",
        db_engine: "mysql",
      },
    });
    render(<TopPage />);

    expect(
      screen.getAllByText(/SET GLOBAL performance_schema_max_digest_length/i).length
    ).toBeGreaterThan(0);
  });
});
