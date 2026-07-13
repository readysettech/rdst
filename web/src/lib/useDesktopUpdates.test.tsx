import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useToastStore } from "@rs/ui-new/use-toast";

import type { DesktopUpdateState } from "./desktop";
import { useDesktopUpdates } from "./useDesktopUpdates";

function Probe() {
  useDesktopUpdates();
  return null;
}

describe("useDesktopUpdates", () => {
  let stateCallback: ((state: DesktopUpdateState) => void) | null = null;
  const install = vi.fn();

  beforeEach(() => {
    useToastStore.setState({ toasts: [] });
    stateCallback = null;
    install.mockClear();
    window.rdstDesktop = {
      isDesktop: true,
      platform: "linux",
      updates: {
        getState: () => Promise.resolve(null),
        install,
        onStateChange: (callback) => {
          stateCallback = callback;
          return () => {
            stateCallback = null;
          };
        },
      },
    };
  });

  afterEach(() => {
    cleanup();
    delete window.rdstDesktop;
  });

  it("shows a restart toast when an update is ready", async () => {
    render(<Probe />);
    await act(async () => {});

    act(() =>
      stateCallback?.({ status: "ready", version: "1.0.9", downloadLinks: [] }),
    );

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(String(toasts[0]?.title)).toContain("1.0.9");
    expect(toasts[0]?.action).toBeTruthy();
  });

  it("shows download links when the install needs a manual download", async () => {
    render(<Probe />);
    await act(async () => {});

    act(() =>
      stateCallback?.({
        status: "available",
        version: "1.0.9",
        downloadLinks: [
          { label: ".deb", url: "https://example.invalid/a.deb" },
          { label: ".rpm", url: "https://example.invalid/a.rpm" },
        ],
      }),
    );

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(String(toasts[0]?.title)).toContain("available");
    expect(toasts[0]?.action).toBeFalsy();
  });

  it("does not re-notify for the same version", async () => {
    render(<Probe />);
    await act(async () => {});

    const state: DesktopUpdateState = {
      status: "ready",
      version: "1.0.9",
      downloadLinks: [],
    };
    act(() => stateCallback?.(state));
    const firstId = useToastStore.getState().toasts[0]?.id;
    act(() => stateCallback?.(state));

    const toasts = useToastStore.getState().toasts;
    expect(toasts).toHaveLength(1);
    expect(toasts[0]?.id).toBe(firstId);
  });
});
