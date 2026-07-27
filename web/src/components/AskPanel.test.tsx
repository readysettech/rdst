import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskPanel } from "./AskPanel";
import { useAsk, type AskResultEvent } from '../lib/ask';
import { createCsvFilename, downloadCsv, toCsv } from '../lib/csv';
import { fetchAskHistory } from '../lib/api';

vi.mock("../lib/ask", () => ({
  useAsk: vi.fn(),
}));

vi.mock("../lib/csv", () => ({
  toCsv: vi.fn(() => "csv-content"),
  downloadCsv: vi.fn(),
  createCsvFilename: vi.fn(() => "rdst-query-results-20250102-030405.csv"),
}));

// Stub the target selector (its own data-fetching is out of scope here) and
// keep useNavigate a no-op so the panel renders without a live router.
vi.mock("./TargetDropdown", () => ({ TargetDropdown: () => null }));
vi.mock("../lib/useSchema", () => ({
  useSchema: () => ({ checkStatus: vi.fn(async () => null) }),
}));
vi.mock("@tanstack/react-router", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@tanstack/react-router")>()),
  useNavigate: () => vi.fn(),
}));
// CodeMirror does not render its document text reliably under jsdom; a plain
// <pre> keeps the disclosure assertions about WHICH SQL is shown meaningful.
vi.mock("./SQLDisplay", () => ({
  SQLDisplay: ({ sql }: { sql: string }) => <pre>{sql}</pre>,
}));
// Keep the example-questions query deterministic (no real fetch in jsdom).
vi.mock("../lib/api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/api")>()),
  fetchAskExamples: vi.fn(async () => ({
    examples: [],
    source: "introspection",
  })),
  fetchAskHistory: vi.fn(async () => ({ items: [] })),
  fetchSchemaStatus: vi.fn(async () => ({
    target: "imdb",
    exists: true,
    tables: 7,
    columns: 41,
    relationships: 0,
    terminology: 0,
    updated_at: null,
  })),
}));

function renderPanel(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrap = (node: ReactElement) => (
    <QueryClientProvider client={client}>{node}</QueryClientProvider>
  );
  const view = render(wrap(ui));
  return {
    ...view,
    rerenderPanel: (node: ReactElement) => view.rerender(wrap(node)),
  };
}

const baseUseAskState = {
  ask: vi.fn(),
  resumeWithAnswers: vi.fn(),
  status: undefined,
  schemaLoaded: undefined,
  clarification: undefined,
  sqlGenerated: undefined,
  error: undefined,
  reset: vi.fn(),
};

describe("AskPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  it("does not show Download CSV when no result is present", () => {
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "idle",
      result: undefined,
    });

    renderPanel(<AskPanel />);

    expect(screen.queryByRole("button", { name: /Download CSV/i })).toBeNull();
  });

  it("lists past questions for the target and re-asks one on click (e7s.17)", async () => {
    const askSpy = vi.fn();
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "idle",
      result: undefined,
    });
    vi.mocked(fetchAskHistory).mockResolvedValue({
      items: [
        {
          question: "How many titles rated above 9.5?",
          sql: "SELECT count(*) FROM title_ratings WHERE averagerating > 9.5",
          hash: "h1",
          tag: "titles_rated_above",
          target: "imdb",
          last_used: "",
        },
      ],
    });

    renderPanel(<AskPanel target="imdb" />);

    const reask = await screen.findByRole("button", {
      name: /How many titles rated above 9.5/,
    });
    fireEvent.click(reask);

    expect(askSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        question: "How many titles rated above 9.5?",
        target: "imdb",
      }),
    );
  });

  it("disables submission when panel is password-locked", () => {
    const askSpy = vi.fn();
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "idle",
      result: undefined,
    });

    renderPanel(<AskPanel disabled />);

    const textarea = screen.getByPlaceholderText(/Ask a question about your data/i);
    fireEvent.change(textarea, { target: { value: "How many users?" } });

    const submitButton = screen.getByRole("button", { name: /Ask/i });
    expect((submitButton as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(submitButton);

    expect(askSpy).not.toHaveBeenCalled();
  });

  it("exports all result rows as CSV when Download CSV is clicked", () => {
    const rows = Array.from({ length: 60 }, (_, index) => [`user-${index}`, index]);
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "complete",
      result: {
        type: 'result',
        success: true,
        sql: 'SELECT name, count FROM users',
        columns: ['name', 'count'],
        rows,
        row_count: 60,
        execution_time_ms: 12.3,
        llm_calls: 1,
        total_tokens: 42,
        query_hash: '',
        query_tag: '',
      } satisfies AskResultEvent,
    });

    renderPanel(<AskPanel />);

    fireEvent.click(screen.getByRole("button", { name: /Download CSV/i }));

    expect(toCsv).toHaveBeenCalledWith(["name", "count"], rows);
    expect(createCsvFilename).toHaveBeenCalledTimes(1);
    expect(downloadCsv).toHaveBeenCalledWith(
      "csv-content",
      "rdst-query-results-20250102-030405.csv",
    );
  });

  it("presents trial authentication failures without internal enum names", () => {
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "error",
      result: undefined,
      error: {
        type: "error",
        message: "RDST's AI service could not validate your trial access.",
        phase: "generate",
      },
    });

    renderPanel(<AskPanel />);

    expect(screen.getByText("AI service authentication failed")).toBeTruthy();
    expect(screen.getByText("Failed while: Generating SQL")).toBeTruthy();
    expect(screen.queryByText(/AskPhase/)).toBeNull();
    expect(screen.queryByText("Something went wrong")).toBeNull();
  });

  it("shows actionable recovery when a trial is exhausted mid-ask", () => {
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "error",
      result: undefined,
      error: {
        type: "error",
        message: "TRIAL_EXHAUSTED",
        phase: "generate",
      },
    });

    renderPanel(<AskPanel />);

    expect(
      screen.getByText(
        "Your free trial credit is used up — add your own Anthropic API key or a new trial token.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /Set key/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Start trial" })).toBeTruthy();
    expect(screen.queryByText("TRIAL_EXHAUSTED")).toBeNull();
  });

  it("re-runs the SAME question on Try again without wiping the input", () => {
    const askSpy = vi.fn();
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "idle",
      result: undefined,
    });

    const view = renderPanel(<AskPanel target="demo" />);
    fireEvent.change(
      screen.getByPlaceholderText(/Ask a question about your data/i),
      { target: { value: "How many users?" } },
    );
    fireEvent.click(screen.getByRole("button", { name: /Ask/i }));
    expect(askSpy).toHaveBeenCalledWith({
      question: "How many users?",
      target: "demo",
    });

    // The stream fails; the panel shows the error state (question kept).
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "error",
      result: undefined,
      error: { type: "error", message: "transient failure", phase: "generate" },
    });
    view.rerenderPanel(<AskPanel target="demo" />);

    fireEvent.click(screen.getByRole("button", { name: /Try again/i }));
    expect(askSpy).toHaveBeenCalledTimes(2);
    expect(askSpy).toHaveBeenLastCalledWith({
      question: "How many users?",
      target: "demo",
    });

    // The input was never wiped: back at idle, the question is still there.
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "idle",
      result: undefined,
    });
    view.rerenderPanel(<AskPanel target="demo" />);
    expect(
      (
        screen.getByPlaceholderText(
          /Ask a question about your data/i,
        ) as HTMLTextAreaElement
      ).value,
    ).toBe("How many users?");
  });

  it("keeps the SQL collapsed by default and reveals the post-validation query on toggle", () => {
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "complete",
      sqlGenerated: {
        type: "sql_generated",
        sql: "SELECT name FROM users",
        explanation: null,
      },
      result: {
        type: "result",
        success: true,
        sql: "SELECT name FROM users LIMIT 100",
        columns: ["name"],
        rows: [["Ada"]],
        row_count: 1,
        execution_time_ms: 3.2,
        llm_calls: 1,
        total_tokens: 50,
        query_hash: "h1",
        query_tag: "user_names",
        limit_added: true,
      } satisfies AskResultEvent,
    });

    renderPanel(<AskPanel target="demo" />);

    // Collapsed by default: neither SQL variant is on screen.
    expect(screen.queryByText("SELECT name FROM users LIMIT 100")).toBeNull();
    const toggle = screen.getByRole("button", {
      name: /Show the SQL that ran/i,
    });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    // The POST-VALIDATION query (the one that ran) is revealed, not the
    // pre-validation generated text — plus the backend's LIMIT-added note.
    expect(
      screen.getByText("SELECT name FROM users LIMIT 100"),
    ).toBeTruthy();
    expect(
      screen.getByText(/was added to keep the result set bounded/),
    ).toBeTruthy();
  });

  it("keeps provenance stamped to the answering target after a target switch", () => {
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      state: "complete",
      schemaLoaded: {
        type: "schema_loaded",
        source: "semantic",
        table_count: 7,
        tables: [],
        target: "demo",
      },
      result: {
        type: "result",
        success: true,
        sql: "SELECT 1",
        columns: ["c"],
        rows: [[1]],
        row_count: 1,
        execution_time_ms: 1.0,
        llm_calls: 1,
        total_tokens: 10,
        query_hash: "h2",
        query_tag: "one",
      } satisfies AskResultEvent,
    });

    const view = renderPanel(<AskPanel target="demo" />);
    const caption = screen.getByText(/Answered from/);
    expect(caption.textContent).toContain("demo");

    // The user switches the live target AFTER the answer rendered: the
    // provenance must stay stamped to the target that answered.
    view.rerenderPanel(<AskPanel target="other" />);
    const captionAfter = screen.getByText(/Answered from/);
    expect(captionAfter.textContent).toContain("demo");
    expect(captionAfter.textContent).not.toContain("other");
  });
});
