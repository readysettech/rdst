import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { SchemaReinitDialog } from "./SchemaReinitDialog";

describe("SchemaReinitDialog (B4/T4)", () => {
  afterEach(cleanup);

  it("names the destructive consequence and the target when open", () => {
    render(
      <SchemaReinitDialog isOpen target="demo" onConfirm={vi.fn()} onClose={vi.fn()} />,
    );

    expect(
      screen.getAllByRole("heading", { name: "Re-initialize semantic layer?" }).length,
    ).toBeGreaterThan(0);
    expect(screen.getAllByText(/discards every annotation/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/permanently deleted/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/cannot be undone/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Target: demo/)).toBeTruthy();
  });

  it("cancelling never triggers the destructive re-init (annotations survive cancel)", () => {
    const onConfirm = vi.fn();
    const onClose = vi.fn();

    render(
      <SchemaReinitDialog
        isOpen
        target="demo"
        onConfirm={onConfirm}
        onClose={onClose}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    // The confirm handler (which calls initSchema with force:true) must not fire.
    expect(onConfirm).not.toHaveBeenCalled();
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("confirming runs the re-init exactly once", () => {
    const onConfirm = vi.fn();

    render(
      <SchemaReinitDialog isOpen target="demo" onConfirm={onConfirm} onClose={vi.fn()} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Discard & re-init" }));

    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("renders nothing actionable when closed", () => {
    render(
      <SchemaReinitDialog
        isOpen={false}
        target="demo"
        onConfirm={vi.fn()}
        onClose={vi.fn()}
      />,
    );

    expect(
      screen.queryAllByRole("heading", { name: "Re-initialize semantic layer?" }).length,
    ).toBe(0);
    expect(screen.queryByRole("button", { name: "Discard & re-init" })).toBeNull();
  });
});
