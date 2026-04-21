import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ResultsPage } from "./results";
import { useAnalyze } from "../lib/sse";
import { useTargetPasswordLock } from "../lib/useTargetPasswordLock";

vi.mock("@tanstack/react-router", async () => {
  const actual = await vi.importActual("@tanstack/react-router");
  return {
    ...actual,
    useNavigate: () => vi.fn(),
  };
});

vi.mock("@tanstack/react-query", () => ({
  useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData: vi.fn() }),
  useQuery: () => ({ data: undefined, isLoading: false, isFetching: false, error: null }),
  useMutation: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false, isError: false, isSuccess: false, data: undefined, error: null, reset: vi.fn() }),
}));

vi.mock("../lib/sse", () => ({
  useAnalyze: vi.fn(),
}));

vi.mock("../lib/useTargetPasswordLock", () => ({
  useTargetPasswordLock: vi.fn(),
}));

vi.mock("../components", () => ({
  AnalysisResults: () => <div>analysis-results</div>,
  SQLDisplay: () => <div>sql-display</div>,
  InteractivePanel: () => null,
  TargetLockNotice: ({ message }: { message: string }) => <div>{message}</div>,
}));

vi.mock("../components/top", () => ({
  ParameterDialog: () => null,
  hasParameters: vi.fn(() => false),
}));

describe("ResultsPage password lock", () => {
  it("does not auto-run analyze when target is password-locked", async () => {
    const analyzeSpy = vi.fn();
    vi.mocked(useAnalyze).mockReturnValue({
      analyze: analyzeSpy,
      state: "idle",
      progress: undefined,
      results: undefined,
      rewriteTesting: undefined,
      readysetCacheability: undefined,
      error: undefined,
      reset: vi.fn(),
    });
    vi.mocked(useTargetPasswordLock).mockReturnValue({
      isLocked: true,
      targetName: "prod",
      message: "Target 'prod' is locked.",
      missingTargetRequirements: [],
      keyringAvailable: true,
    });

    render(
      <ResultsPage
        search={{ query: "select 1", target: "prod", fast: false }}
      />,
    );

    await waitFor(() => {
      expect(screen.getByText("Target 'prod' is locked.")).toBeTruthy();
    });

    expect(analyzeSpy).not.toHaveBeenCalled();
  });
});
