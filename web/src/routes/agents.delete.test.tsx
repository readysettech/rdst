import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page module runs createFileRoute('/agents')(...) at import time; neutralise
// it so the exported dialog can be imported without a router context. Everything
// else in @tanstack/react-router stays real.
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, createFileRoute: () => (options: unknown) => options };
});

import { AgentDeleteDialog } from "./agents";
import type { AgentSummary } from "../types/agents";

const agent: AgentSummary = {
  name: "orders-analyst",
  target: "orders_db",
  guard: "pii-mask",
  max_rows: 1000,
  description: "",
};

describe("AgentDeleteDialog (audit HIGH: one-click delete needs confirmation)", () => {
  afterEach(cleanup);

  it("names the agent, its scope, and the permanent consequence when open", () => {
    render(
      <AgentDeleteDialog agent={agent} isOpen onConfirm={vi.fn()} onClose={vi.fn()} />,
    );

    expect(
      screen.getAllByRole("heading", { name: 'Delete agent "orders-analyst"?' }).length,
    ).toBeGreaterThan(0);
    // Scope subtitle: the database it reads and the guard it is bound to.
    expect(screen.getByText(/Reads orders_db · guarded by pii-mask/)).toBeTruthy();
    expect(screen.getAllByText(/permanently deletes the agent/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/discards its in-memory chat history/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/cannot be undone/i).length).toBeGreaterThan(0);
  });

  it("offers a red destructive confirm and a Cancel", () => {
    render(
      <AgentDeleteDialog agent={agent} isOpen onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.getByRole("button", { name: /Delete agent/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeTruthy();
  });

  it("opening then cancelling never deletes", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <AgentDeleteDialog agent={agent} isOpen onConfirm={onConfirm} onClose={onClose} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    // The destructive handler (deleteAgent.mutateAsync) must not fire on cancel.
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirming deletes exactly once", () => {
    const onConfirm = vi.fn();

    render(
      <AgentDeleteDialog agent={agent} isOpen onConfirm={onConfirm} onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Delete agent/ }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables Cancel while a delete is in flight (blockCloseWhileLoading)", () => {
    render(
      <AgentDeleteDialog agent={agent} isOpen loading onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("renders nothing actionable when closed", () => {
    render(
      <AgentDeleteDialog
        agent={agent}
        isOpen={false}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /Delete agent/ })).toBeNull();
  });
});
