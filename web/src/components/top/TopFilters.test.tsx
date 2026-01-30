import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { TopFilters } from "./TopFilters";

describe("TopFilters", () => {
  it("disables start action when target actions are locked", () => {
    render(
      <TopFilters
        mode="historical"
        setMode={vi.fn()}
        source="auto"
        setSource={vi.fn()}
        sort="total_time"
        setSort={vi.fn()}
        limit={10}
        setLimit={vi.fn()}
        filterPattern=""
        setFilterPattern={vi.fn()}
        duration={0}
        setDuration={vi.fn()}
        autoSave
        setAutoSave={vi.fn()}
        state="idle"
        onStart={vi.fn()}
        onStop={vi.fn()}
        hasTarget={false}
      />,
    );

    const button = screen.getByRole("button", { name: /Get Top Queries/i });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });
});
