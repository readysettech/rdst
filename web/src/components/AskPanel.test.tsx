import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AskPanel } from "./AskPanel";
import { useAsk, type AskResultEvent } from '../lib/ask';
import { createCsvFilename, downloadCsv, toCsv } from '../lib/csv';

vi.mock("../lib/ask", () => ({
  useAsk: vi.fn(),
}));

vi.mock("../lib/csv", () => ({
  toCsv: vi.fn(() => "csv-content"),
  downloadCsv: vi.fn(),
  createCsvFilename: vi.fn(() => "rdst-query-results-20250102-030405.csv"),
}));

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

    render(<AskPanel />);

    expect(screen.queryByRole("button", { name: /Download CSV/i })).toBeNull();
  });

  it("disables submission when panel is password-locked", () => {
    const askSpy = vi.fn();
    vi.mocked(useAsk).mockReturnValue({
      ...baseUseAskState,
      ask: askSpy,
      state: "idle",
      result: undefined,
    });

    render(<AskPanel disabled />);

    const textarea = screen.getByPlaceholderText(/Ask a question about your data/i);
    fireEvent.change(textarea, { target: { value: "How many users?" } });

    const submitButton = screen.getByRole("button", { name: /Generate SQL/i });
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
        success: true,
        sql: 'SELECT name, count FROM users',
        columns: ['name', 'count'],
        rows,
        row_count: 60,
        execution_time_ms: 12.3,
        llm_calls: 1,
        total_tokens: 42,
      } satisfies AskResultEvent,
    });

    render(<AskPanel />);

    fireEvent.click(screen.getByRole("button", { name: /Download CSV/i }));

    expect(toCsv).toHaveBeenCalledWith(["name", "count"], rows);
    expect(createCsvFilename).toHaveBeenCalledTimes(1);
    expect(downloadCsv).toHaveBeenCalledWith(
      "csv-content",
      "rdst-query-results-20250102-030405.csv",
    );
  });
});
