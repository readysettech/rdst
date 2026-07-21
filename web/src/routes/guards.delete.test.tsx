import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

// The page module runs createFileRoute('/guards')(...) at import time; neutralise
// it so the exported dialog can be imported without a router context. Everything
// else in @tanstack/react-router stays real.
vi.mock("@tanstack/react-router", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@tanstack/react-router")>();
  return { ...actual, createFileRoute: () => (options: unknown) => options };
});

import { GuardDeleteDialog } from "./guards";
import type { GuardSummary } from "../types/guards";

const guard: GuardSummary = {
  name: "pii-mask",
  derived: false,
  description: "",
  mask_count: 3,
  max_rows: 1000,
  rules: ["require_where", "no_select_star"],
};

describe("GuardDeleteDialog (audit HIGH: deleting a guard is a security-boundary change)", () => {
  afterEach(cleanup);

  it("names the guard, the protection it removes, and the permanent consequence", () => {
    render(
      <GuardDeleteDialog guard={guard} isOpen onConfirm={vi.fn()} onClose={vi.fn()} />,
    );

    expect(
      screen.getAllByRole("heading", { name: 'Delete guard "pii-mask"?' }).length,
    ).toBeGreaterThan(0);
    // Security framing subtitle.
    expect(screen.getByText(/changes a security boundary/i)).toBeTruthy();
    // Protection summary is built from row data (mask_count + rules), not invented.
    expect(
      screen.getAllByText(/masks 3 columns and enforces WHERE required, no SELECT \*/i).length,
    ).toBeGreaterThan(0);
    expect(
      screen.getAllByText(/Any agent bound to this guard loses that protection/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/cannot be undone/i).length).toBeGreaterThan(0);
  });

  it("omits the protection clause when the row carries no masks or rules", () => {
    const bare: GuardSummary = {
      name: "empty-guard",
      derived: false,
      description: "",
      mask_count: 0,
      max_rows: 1000,
      rules: [],
    };
    render(
      <GuardDeleteDialog guard={bare} isOpen onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect(screen.queryByText(/This guard masks/i)).toBeNull();
    expect(
      screen.getAllByText(/Any agent bound to this guard loses that protection/i).length,
    ).toBeGreaterThan(0);
  });

  it("opening then cancelling never deletes", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <GuardDeleteDialog guard={guard} isOpen onConfirm={onConfirm} onClose={onClose} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    // The destructive handler (deleteGuard.mutateAsync) must not fire on cancel.
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirming deletes exactly once", () => {
    const onConfirm = vi.fn();

    render(
      <GuardDeleteDialog guard={guard} isOpen onConfirm={onConfirm} onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /Delete guard/ }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("disables Cancel while a delete is in flight (blockCloseWhileLoading)", () => {
    render(
      <GuardDeleteDialog guard={guard} isOpen loading onConfirm={vi.fn()} onClose={vi.fn()} />,
    );
    expect((screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });

  it("renders nothing actionable when closed", () => {
    render(
      <GuardDeleteDialog
        guard={guard}
        isOpen={false}
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /Delete guard/ })).toBeNull();
  });
});
