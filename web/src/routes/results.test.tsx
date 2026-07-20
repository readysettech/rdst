import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Route } from "./results";
import { ResultsPage } from "./-results-page";
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
      errorEnvelope: undefined,
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

describe("/results no-query guard (B1)", () => {
  it("validateSearch never throws — it parses a missing query to empty", () => {
    // Throwing `redirect` from validateSearch is what crashed the whole app to
    // the chrome-less screen; parsing must stay pure.
    const validateSearch = Route.options.validateSearch as (
      s: Record<string, unknown>,
    ) => { query: string };
    expect(() => validateSearch({})).not.toThrow();
    expect(validateSearch({}).query).toBe("");
    expect(validateSearch({ query: "select 1" }).query).toBe("select 1");
  });

  it("beforeLoad redirects to /analyze when the query is missing", () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { query: string };
    }) => void;

    let thrown: unknown;
    try {
      beforeLoad({ search: { query: "" } });
    } catch (e) {
      thrown = e;
    }
    expect(thrown).toBeDefined();
    expect(JSON.stringify(thrown)).toContain("/analyze");
  });

  it("beforeLoad allows a present query through (no redirect)", () => {
    const beforeLoad = Route.options.beforeLoad as (ctx: {
      search: { query: string };
    }) => void;
    expect(() => beforeLoad({ search: { query: "select 1" } })).not.toThrow();
  });
});
