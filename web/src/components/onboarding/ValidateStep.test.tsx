import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ValidateStep } from "./ValidateStep";
import { fetchEnvRequirements, fetchStatus } from "../../lib/api";
import type { ValidationResult } from "../../types/onboarding";

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual("../../lib/api");
  return {
    ...actual,
    fetchEnvRequirements: vi.fn(),
    fetchStatus: vi.fn(),
  };
});

vi.mock("../EnvSecretsDialog", () => ({
  EnvSecretsDialog: ({
    isOpen,
    onSuccess,
    requirements,
  }: {
    isOpen: boolean;
    onSuccess?: () => void;
    requirements: Array<{ accepted_names: string[] }>;
  }) =>
    isOpen ? (
      <div>
        <div data-testid="dialog-requirements">
          {requirements.map((item) => item.accepted_names[0]).join(",")}
        </div>
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Save Secrets
        </button>
      </div>
    ) : null,
}));

vi.mock("../TrialRegistrationDialog", () => ({
  TrialRegistrationDialog: ({
    isOpen,
    onSuccess,
  }: {
    isOpen: boolean;
    onSuccess?: () => void;
  }) =>
    isOpen ? (
      <div data-testid="trial-dialog">
        <button type="button" onClick={() => onSuccess?.()}>
          Mock Trial Success
        </button>
      </div>
    ) : null,
}));

function renderValidateStep(
  onRun = vi.fn(),
  options?: { results?: ValidationResult | null; isLoading?: boolean }
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  const results: ValidationResult | null =
    options && Object.prototype.hasOwnProperty.call(options, "results")
      ? (options.results ?? null)
      : {
          target_results: [{ name: "prod", success: true }],
          llm_result: { success: false, error: "ANTHROPIC_API_KEY not set" },
        };
  return render(
    <QueryClientProvider client={queryClient}>
      <ValidateStep
        targetNames={["prod"]}
        results={results}
        onRun={onRun}
        onNext={vi.fn()}
        onBack={vi.fn()}
        isLoading={options?.isLoading}
      />
    </QueryClientProvider>
  );
}

describe("ValidateStep", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(fetchStatus).mockResolvedValue({
      configured: true,
      default_target: "prod",
      targets: [{ name: "prod", has_password: true, is_default: true }],
      version: "test",
      error: null,
    });
  });

  afterEach(() => {
    cleanup();
  });

  it("shows trial and key actions when env requirements are missing", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: false,
          source: "missing",
        },
      ],
    });

    renderValidateStep();

    expect(await screen.findByRole("button", { name: /Claim free trial credits/i })).toBeTruthy();
    expect(await screen.findByRole("button", { name: /I Have a Key/i })).toBeTruthy();
  });

  it("shows auto-run guidance before validation results exist", async () => {
    const onRun = vi.fn();
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [],
    });

    renderValidateStep(onRun, { results: null, isLoading: false });

    await waitFor(() => {
      expect(onRun).toHaveBeenCalled();
    });
    expect(screen.getByText(/Validation starts automatically when you open this step/i)).toBeTruthy();
    expect(screen.getByText(/If it doesn't start, click "Run Tests"./i)).toBeTruthy();
  });

  it("shows Set action for missing target password requirements", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "target_password",
          accepted_names: ["PROD_DB_PASSWORD"],
          target: "prod",
          satisfied: false,
          source: "missing",
        },
      ],
    });

    renderValidateStep(undefined, {
      results: {
        target_results: [{ name: "prod", success: false, error: "auth failed" }],
        llm_result: { success: false, error: "ANTHROPIC_API_KEY not set" },
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: /Set/i }));
    const requirementsText = (await screen.findByTestId("dialog-requirements"))
      .textContent || "";
    expect(requirementsText.includes("PROD_DB_PASSWORD")).toBe(true);
    expect(requirementsText.includes("RDST_ANTHROPIC_API_KEY")).toBe(false);
  });

  it("does not show DB Set action when target connection succeeds", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "target_password",
          accepted_names: ["PROD_DB_PASSWORD"],
          target: "prod",
          satisfied: true,
          source: "process_env",
        },
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: true,
          source: "process_env",
        },
      ],
    });

    renderValidateStep(undefined, {
      results: {
        target_results: [{ name: "prod", success: true }],
        llm_result: { success: true, model: "claude-sonnet" },
      },
    });

    await waitFor(() => {
      expect(screen.queryByText(/Database password required/i)).toBeNull();
    });
  });

  it("hides trial/key buttons when Anthropic is already connected", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: true,
          source: "process_env",
        },
      ],
    });

    renderValidateStep(undefined, {
      results: {
        target_results: [{ name: "prod", success: true }],
        llm_result: { success: true, model: "claude-sonnet" },
      },
    });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /Claim free trial credits/i })).toBeNull();
      expect(screen.queryByRole("button", { name: /I Have a Key/i })).toBeNull();
    });
  });

  it("clears setup warning when Anthropic env requirement is now satisfied", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: true,
          source: "process_env",
        },
      ],
    });

    renderValidateStep(undefined, {
      results: {
        target_results: [{ name: "prod", success: true }],
        llm_result: { success: false, error: "ANTHROPIC_API_KEY not set" },
      },
    });

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: /I Have a Key/i })).toBeNull();
    });
    expect(screen.queryByText(/API Key Required for AI/i)).toBeNull();
  });

  it("passes only Anthropic requirements to key dialog", async () => {
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: false,
          source: "missing",
        },
        {
          kind: "target_password",
          accepted_names: ["DOCS_READYSET_PASSWORD"],
          target: "other",
          satisfied: false,
          source: "missing",
        },
      ],
    });

    renderValidateStep();
    fireEvent.click(await screen.findByRole("button", { name: /I Have a Key/i }));

    const requirementsText = (await screen.findByTestId("dialog-requirements"))
      .textContent || "";
    expect(requirementsText.includes("RDST_ANTHROPIC_API_KEY")).toBe(true);
    expect(requirementsText.includes("DOCS_READYSET_PASSWORD")).toBe(false);
  });

  it("re-runs validation after successful secret save", async () => {
    const onRun = vi.fn();
    vi.mocked(fetchEnvRequirements).mockResolvedValue({
      keyring_available: true,
      requirements: [
        {
          kind: "anthropic_api_key",
          accepted_names: ["RDST_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"],
          target: null,
          satisfied: false,
          source: "missing",
        },
      ],
    });

    renderValidateStep(onRun);

    fireEvent.click(await screen.findByRole("button", { name: /I Have a Key/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Mock Save Secrets/i }));

    await waitFor(() => {
      expect(onRun).toHaveBeenCalled();
    });
  });
});
